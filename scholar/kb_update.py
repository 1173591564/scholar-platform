"""
Scholar Studio — Knowledge Base Update

批量从 arXiv 下载论文并执行全流程入库。
支持：arxiv-download（下载）、batch-ingest（批量入库）、kb-update（一键组合）。
"""
import json
import re
import shutil
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

from . import config
from . import db as dbmod


ARXIV_NS = {"atom": "http://www.w3.org/2005/Atom"}


def _generate_ulid() -> str:
    """生成一个新的 ULID。"""
    try:
        import ulid as ulid_mod
        return str(ulid_mod.ULID())
    except ImportError:
        # 备用：使用时间戳 + 随机字符
        import secrets
        import base64
        ts = int(time.time() * 1000).to_bytes(6, "big")
        rand = secrets.token_bytes(10)
        raw = ts + rand
        encoded = base64.b32encode(raw).decode("ascii").rstrip("=")
        return encoded[:26]


def _parse_arxiv_entries(xml_data: str) -> list[dict]:
    """解析 arXiv API XML 响应，提取论文信息列表。"""
    root = ET.fromstring(xml_data)
    entries = root.findall("atom:entry", ARXIV_NS)
    results = []

    for entry in entries:
        title_elem = entry.find("atom:title", ARXIV_NS)
        title = title_elem.text.strip().replace("\n", " ") if title_elem is not None and title_elem.text else ""

        authors = [
            a.find("atom:name", ARXIV_NS).text
            for a in entry.findall("atom:author", ARXIV_NS)
            if a.find("atom:name", ARXIV_NS) is not None
               and a.find("atom:name", ARXIV_NS).text
        ]

        published = entry.find("atom:published", ARXIV_NS)
        year = published.text[:4] if published is not None and published.text else ""

        id_elem = entry.find("atom:id", ARXIV_NS)
        arxiv_id = ""
        if id_elem is not None and id_elem.text:
            raw_id = id_elem.text.strip()
            arxiv_id = raw_id.split("/abs/")[-1]
            match = re.match(r"(\d{4}\.\d{4,5})", arxiv_id)
            if match:
                arxiv_id = match.group(1)

        summary_elem = entry.find("atom:summary", ARXIV_NS)
        abstract = summary_elem.text.strip() if summary_elem is not None and summary_elem.text else ""

        # 提取 TeX source 下载链接
        source_url = ""
        for link in entry.findall("atom:link", ARXIV_NS):
            if link.get("title") == "pdf":
                source_url = link.get("href", "")

        results.append({
            "title": title,
            "authors": authors,
            "year": year,
            "arxiv_id": arxiv_id,
            "abstract": abstract,
            "pdf_url": source_url,
        })

    return results


def arxiv_download(
    query: str,
    max_results: int = 10,
    download_pdf: bool = True,
    output_dir: Optional[Path] = None,
) -> list[dict]:
    """批量下载 arXiv 论文 TeX 源码。

    流程:
    1. 搜索 arXiv API
    2. 去重（检查已有 arxiv_id）
    3. 下载 TeX source（tar.gz）
    4. 可选下载 PDF
    5. 生成 ULID 目录结构: data/papers/<ULID>/paper.pdf + source.tar.gz
    6. 返回下载结果列表
    """
    if output_dir is None:
        output_dir = config.PAPERS_DIR

    # 1. 搜索 arXiv
    xml_data = config.arxiv_request(f"all:{query}", max_results=max_results)
    entries = _parse_arxiv_entries(xml_data)

    # 2. 去重：扫描已有 JSON 的 arxiv_id
    existing_ids = set()
    for json_path in config.PARSED_DIR.glob("*.json"):
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            if data.get("arxiv_id"):
                existing_ids.add(data["arxiv_id"])
        except Exception:
            continue

    results = []
    import urllib.request

    for entry in entries:
        arxiv_id = entry["arxiv_id"]
        title = entry["title"]

        # 去重
        if arxiv_id in existing_ids:
            results.append({
                "arxiv_id": arxiv_id,
                "title": title[:80],
                "status": "already_exists",
            })
            continue

        # 生成 ULID 目录
        paper_ulid = _generate_ulid()
        paper_dir = output_dir / paper_ulid
        paper_dir.mkdir(parents=True, exist_ok=True)

        result = {
            "ulid": paper_ulid,
            "arxiv_id": arxiv_id,
            "title": title[:80],
            "authors": entry["authors"],
            "year": entry["year"],
            "abstract": entry["abstract"],
            "status": "downloaded",
        }

        # 3. 下载 TeX source
        source_url = f"https://arxiv.org/e-print/{arxiv_id}"
        try:
            source_path = paper_dir / "source.tar.gz"
            proxy_handler = urllib.request.ProxyHandler()
            opener = urllib.request.build_opener(proxy_handler)
            req = urllib.request.Request(source_url, headers={"User-Agent": "ScholarStudio/1.0"})
            with opener.open(req, timeout=60) as resp:
                source_path.write_bytes(resp.read())
            result["source_size"] = source_path.stat().st_size
        except Exception as e:
            result["status"] = f"source_failed: {e}"
            results.append(result)
            # 清理空目录，避免失败下载留下无用 ULID 目录
            try:
                shutil.rmtree(paper_dir, ignore_errors=True)
            except Exception:
                pass
            continue

        # 4. 可选下载 PDF
        if download_pdf and entry.get("pdf_url"):
            try:
                pdf_path = paper_dir / "paper.pdf"
                req = urllib.request.Request(entry["pdf_url"], headers={"User-Agent": "ScholarStudio/1.0"})
                with opener.open(req, timeout=60) as resp:
                    pdf_path.write_bytes(resp.read())
                result["pdf_downloaded"] = True
            except Exception:
                result["pdf_downloaded"] = False

        # 5. 保存初始元数据 JSON
        #    若写入失败必须清理 paper_dir，否则会遗留孤儿目录，
        #    下次 arxiv_download 会重下同一篇论文产生新 ULID，原目录孤立。
        meta = {
            "paper_id": paper_ulid,
            "title": title,
            "authors": entry["authors"],
            "year": entry.get("year"),
            "arxiv_id": arxiv_id,
            "abstract": entry.get("abstract", ""),
            "venue": "",
        }
        meta_path = paper_dir / "meta.json"
        try:
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            # meta 写失败，清理下载产物
            result["status"] = f"meta_failed: {e}"
            try:
                shutil.rmtree(paper_dir, ignore_errors=True)
            except Exception:
                pass
            continue

        results.append(result)
        existing_ids.add(arxiv_id)

        # Rate limit (仅在成功路径执行)
        time.sleep(3)

    return results


def batch_ingest(
    ulids: Optional[list[str]] = None,
    skip_notes: bool = False,
    skip_quality: bool = False,
) -> dict:
    """批量执行 ingest 全流程。

    流程:
    1. parse（解析 TeX → JSON）
    2. arXiv 元数据补全（单次 API 查询 → authors/year/arxiv_id/doi/venue）
    3. auto-notes + quality-score + classify
    """
    from .tex_parser import parse_paper
    from . import metadata_enrich as me
    from .id_resolver import get_resolver

    # 确定要处理的论文列表
    # 重要: ulids=[] (明确传入空) 与 ulids=None (未传) 语义不同!
    # 传入空列表 = 显式表示「本次无需处理任何论文」，应直接返回 0 stats
    if ulids is not None:
        paper_ids = list(ulids)
    else:
        # 找出所有有 source 但未 parse 的论文
        parsed_ids = set(dbmod.list_parsed())
        paper_ids = []
        for d in sorted(config.PAPERS_DIR.iterdir()):
            if d.is_dir() and d.name not in parsed_ids:
                has_source = any(
                    (d / name).exists()
                    for name in ["source.tar.gz", "source.tgz", "source.tar", "source.zip"]
                )
                if has_source:
                    paper_ids.append(d.name)

    stats = {
        "total": len(paper_ids),
        "parsed": 0,
        "enriched": 0,
        "noted": 0,
        "scored": 0,
        "classified": 0,
        "errors": [],
    }

    for paper_id in paper_ids:
        paper_dir = config.PAPERS_DIR / paper_id

        # Step 1: Parse (file-only)
        try:
            data = parse_paper(paper_dir, paper_id)
            dbmod.save_parsed(data)
            stats["parsed"] += 1
        except Exception as e:
            stats["errors"].append({"paper_id": paper_id, "step": "parse", "error": str(e)})
            continue

        json_path = config.PARSED_DIR / f"{paper_id}.json"

        # Step 2: arXiv metadata best-effort (1 API call → all fields)
        try:
            paper_data = json.loads(json_path.read_text(encoding="utf-8"))
            title = paper_data.get("title", "")
            needs_enrich = (
                not paper_data.get("arxiv_id")
                or not paper_data.get("authors")
                or not paper_data.get("year")
                or not paper_data.get("venue")
            )
            if needs_enrich and title:
                meta = me.fetch_arxiv_metadata(title)
                if meta:
                    me.apply_arxiv_metadata(paper_data, meta)
                # 兜底：arXiv 无匹配时，有 title 就设 "Preprint"
                if not paper_data.get("venue") and title:
                    paper_data["venue"] = "Preprint"
                json_path.write_text(
                    json.dumps(paper_data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                if meta:
                    stats["enriched"] += 1
                time.sleep(3)
        except Exception:
            pass

        # Step 3: Auto-notes + quality + classify
        if not skip_notes:
            try:
                from . import auto_notes as an
                an.generate_single_note(paper_id, force=True)
                stats["noted"] += 1
            except Exception:
                pass

        if not skip_quality:
            try:
                from . import quality as q
                q.score_single_paper(paper_id)
                stats["scored"] += 1
            except Exception:
                pass

        try:
            from . import classify as cl
            cl.classify_single_paper(paper_id)
            stats["classified"] += 1
        except Exception:
            pass

    # 刷新 IDResolver 缓存
    get_resolver().refresh()

    return stats


def kb_update(
    query: str = "",
    max_results: int = 10,
    download_pdf: bool = True,
) -> dict:
    """一键更新知识库：搜索 → 下载 → 批量入库。

    Args:
        query: arXiv 搜索关键词（空则只处理本地未入库论文）
        max_results: 最大下载数量
        download_pdf: 是否同时下载 PDF（默认 True，与现有论文结构一致）
    """
    results = {"downloaded": [], "ingest": None}

    # Step 1: 下载（如果有 query）
    if query:
        try:
            download_results = arxiv_download(query, max_results=max_results, download_pdf=download_pdf)
            results["downloaded"] = download_results
            # 提取新下载的 ULID 列表
            new_ulids = [r["ulid"] for r in download_results if r.get("status") == "downloaded"]
        except Exception as e:
            results["download_error"] = str(e)
            new_ulids = []
    else:
        new_ulids = None

    # Step 2: 批量入库
    ingest_stats = batch_ingest(ulids=new_ulids)
    results["ingest"] = ingest_stats

    return results
