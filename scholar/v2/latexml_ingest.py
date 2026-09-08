"""Ingest LaTeX sources into immutable LaTeXML release artifacts."""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import re
import shutil
import subprocess
import tarfile
import time
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

import httpx

from .models import ScholarError
from .xml_utils import SAFE_ID, SECTION_KINDS, local_name


@dataclass(frozen=True)
class IngestSource:
    paper_id: str
    tex_dir: Path
    arxiv_id: str | None = None


@dataclass
class IngestResult:
    paper_id: str
    status: str
    reasons: list[str]
    xml_path: Path | None
    log_path: Path
    quality_path: Path


class LaTeXMLRunner:
    """Runs latexmlc inside the ar5ivist container for one source directory."""

    def __init__(
        self,
        image: str = "latexml/ar5ivist:2512.17",
        timeout: int = 600,
        memory: str = "4g",
        cpus: str = "1",
        docker: str = "docker",
        runner: Callable[[list[str]], subprocess.CompletedProcess] | None = None,
    ):
        self.image = image
        self.timeout = timeout
        self.memory = memory
        self.cpus = cpus
        self.docker = docker
        self.runner = runner or self._run
        self.last_math_mode = "full"

    def _run(self, command: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=self.timeout + 120,
        )

    def _command(
        self, tex_dir: Path, main_tex: str, out_dir: Path, noparse: bool = False
    ) -> list[str]:
        command = [
            self.docker,
            "run",
            "--rm",
            "--network=none",
            f"--memory={self.memory}",
            f"--cpus={self.cpus}",
            "-v",
            f"{tex_dir.resolve()}:/source:ro",
            "-v",
            f"{out_dir.resolve()}:/out",
            self.image,
            "latexmlc",
            "--format=xml",
            "--mathtex",
            "--pmml",
            "--cmml",
            "--nocomments",
            "--includestyles",
            f"--timeout={self.timeout}",
            "--log=/out/paper.log",
            "--dest=/out/paper.xml",
            f"/source/{main_tex.replace(chr(92), '/')}",
        ]
        if noparse:
            command.append("--noparse")
        return command

    def run(
        self, tex_dir: Path, main_tex: str, out_dir: Path
    ) -> subprocess.CompletedProcess:
        tex_dir = Path(tex_dir)
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        first_command = self._command(tex_dir, main_tex, out_dir)
        retry = False
        try:
            result = self.runner(first_command)
            retry = result.returncode != 0 and not (out_dir / "paper.xml").is_file()
        except subprocess.TimeoutExpired:
            retry = True
            result = subprocess.CompletedProcess(
                first_command, 124, "", "latexmlc timed out"
            )
        if retry:
            self.last_math_mode = "noparse"
            second_command = self._command(tex_dir, main_tex, out_dir, noparse=True)
            try:
                result = self.runner(second_command)
            except subprocess.TimeoutExpired:
                result = subprocess.CompletedProcess(
                    second_command, 124, "", "latexmlc noparse timed out"
                )
        else:
            self.last_math_mode = "full"
        return result


def find_main_tex(tex_dir: Path) -> str:
    """Find the most likely top-level TeX source and return its relative path."""
    files = sorted(
        (item for item in Path(tex_dir).rglob("*.tex") if item.is_file()),
        key=lambda item: str(item).lower(),
    )
    if not files:
        raise ScholarError("INVALID_ARGUMENT", f"no .tex source found in {tex_dir}")

    candidates: list[Path] = []
    for item in files:
        try:
            text = item.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if r"\documentclass" in text and r"\begin{document}" in text:
            candidates.append(item)
    choices = candidates or files
    preferred = {"main.tex": 0, "paper.tex": 1, "ms.tex": 2}
    chosen = min(
        choices,
        key=lambda item: (
            preferred.get(item.name.lower(), 3),
            -item.stat().st_size,
            str(item).lower(),
        ),
    )
    return chosen.relative_to(Path(tex_dir)).as_posix()


def _empty_stats(math_mode: str) -> dict:
    return {
        "abstract_chars": 0,
        "creator_count": 0,
        "section_count": 0,
        "paragraph_count": 0,
        "body_text_chars": 0,
        "figure_count": 0,
        "table_count": 0,
        "math_count": 0,
        "bibitem_count": 0,
        "error_count": 0,
        "warning_count": 0,
        "math_mode": math_mode,
    }


def _element_text(element: ET.Element | None) -> str:
    return " ".join("".join(element.itertext()).split()) if element is not None else ""


def _direct_child(root: ET.Element, name: str) -> ET.Element | None:
    return next((item for item in root if local_name(item.tag) == name), None)


def _document_title(root: ET.Element) -> str:
    return _element_text(_direct_child(root, "title"))


def grade_quality(
    xml_path: Path, log_text: str, math_mode: str
) -> tuple[str, list[str], dict]:
    """Grade a generated XML file using structural and log-based gates."""
    stats = _empty_stats(math_mode)
    stats["error_count"] = sum(
        1 for line in log_text.splitlines() if line.startswith("Error:")
    )
    stats["warning_count"] = sum(
        1 for line in log_text.splitlines() if line.startswith("Warning:")
    )
    try:
        root = ET.parse(xml_path).getroot()
    except (ET.ParseError, OSError):
        return "failed", ["invalid_xml"], stats

    title = _document_title(root)
    abstract = next(
        (item for item in root.iter() if local_name(item.tag) == "abstract"), None
    )
    stats.update(
        {
            "abstract_chars": len(_element_text(abstract)),
            "creator_count": sum(
                1 for item in root.iter() if local_name(item.tag) == "creator"
            ),
            "section_count": sum(
                1
                for item in root.iter()
                if local_name(item.tag) in SECTION_KINDS
            ),
            "paragraph_count": sum(
                1 for item in root.iter() if local_name(item.tag) == "para"
            ),
            "body_text_chars": len(_element_text(root)),
            "figure_count": sum(
                1 for item in root.iter() if local_name(item.tag) == "figure"
            ),
            "table_count": sum(
                1 for item in root.iter() if local_name(item.tag) == "table"
            ),
            "math_count": sum(
                1 for item in root.iter() if local_name(item.tag) == "Math"
            ),
            "bibitem_count": sum(
                1 for item in root.iter() if local_name(item.tag) == "bibitem"
            ),
        }
    )

    reasons: list[str] = []
    if not title:
        reasons.append("no_title")
    if stats["section_count"] == 0:
        reasons.append("no_sections")
    if stats["paragraph_count"] < 10:
        reasons.append("too_few_paragraphs")
    if reasons:
        return "failed", reasons, stats

    if "Fatal:" in log_text:
        reasons.append("fatal")
    if stats["error_count"] > 50:
        reasons.append("errors_gt_50")
    bibliography = next(
        (item for item in root.iter() if local_name(item.tag) == "bibliography"),
        None,
    )
    if bibliography is not None and stats["bibitem_count"] == 0:
        reasons.append("bib_lost")
    if abstract is None:
        reasons.append("abstract_lost")
    if title.casefold().startswith("supplementary"):
        reasons.append("title_supplement")
    return ("degraded" if reasons else "clean"), reasons, stats


def manifest_row(
    paper_id: str, xml_path: Path, title: str, stats: dict, tier: str
) -> dict:
    raw = Path(xml_path).read_bytes()
    canonical = ET.canonicalize(xml_data=raw.decode("utf-8")).encode("utf-8")
    return {
        "paper_id": paper_id,
        "title": title,
        "size_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "canonical_sha256": hashlib.sha256(canonical).hexdigest(),
        "abstract_chars": stats["abstract_chars"],
        "creator_count": stats["creator_count"],
        "section_count": stats["section_count"],
        "paragraph_count": stats["paragraph_count"],
        "body_text_chars": stats["body_text_chars"],
        "figure_count": stats["figure_count"],
        "table_count": stats["table_count"],
        "math_count": stats["math_count"],
        "bibitem_count": stats["bibitem_count"],
        "quality_tier": tier,
    }


def safe_paper_id(raw: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", raw)
    value = value.strip("-")
    if not value or not re.match(r"[A-Za-z0-9]", value):
        value = f"paper-{value}".strip("-")
    return value[:256]


def _validate_archive_member(name: str, destination: Path) -> None:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part in {"", ".."} for part in path.parts):
        raise ScholarError("INVALID_ARGUMENT", f"unsafe archive member: {name}")
    target = (destination / Path(*path.parts)).resolve()
    if destination.resolve() not in target.parents and target != destination.resolve():
        raise ScholarError("INVALID_ARGUMENT", f"unsafe archive member: {name}")


def _extract_archive(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    if tarfile.is_tarfile(archive):
        with tarfile.open(archive) as handle:
            for member in handle.getmembers():
                _validate_archive_member(member.name, destination)
                if member.issym() or member.islnk():
                    raise ScholarError(
                        "INVALID_ARGUMENT", f"unsafe archive member: {member.name}"
                    )
            handle.extractall(destination, filter="data")
        return
    try:
        with zipfile.ZipFile(archive) as handle:
            for member in handle.infolist():
                _validate_archive_member(member.filename, destination)
            handle.extractall(destination)
    except zipfile.BadZipFile as error:
        raise ScholarError("INVALID_ARGUMENT", f"cannot extract {archive}") from error


def discover_local_sources(root: Path) -> list[IngestSource]:
    sources: list[IngestSource] = []
    for paper_dir in sorted(Path(root).iterdir(), key=lambda item: item.name):
        if not paper_dir.is_dir():
            continue
        tex_files = list(paper_dir.rglob("*.tex"))
        archives = [
            item
            for item in paper_dir.iterdir()
            if item.is_file()
            and item.suffixes[-2:] in [[".tar", ".gz"], [".tgz"], [".zip"], [".tar"]]
        ]
        paper_id = paper_dir.name
        if not SAFE_ID.fullmatch(paper_id):
            paper_id = safe_paper_id(paper_id)
        tex_dir = paper_dir
        if not tex_files and len(archives) == 1:
            tex_dir = paper_dir / "src"
            _extract_archive(archives[0], tex_dir)
        sources.append(IngestSource(paper_id, tex_dir))
    return sources


def fetch_arxiv_source(
    arxiv_id: str, dest: Path, client: object | None = None
) -> Path:
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    source_path = dest / "source.tar.gz"
    source_dir = dest / "src"
    own_client = client is None
    http_client = (
        httpx.Client(timeout=120, follow_redirects=True)
        if own_client
        else client
    )
    try:
        response = http_client.get(
            f"https://arxiv.org/e-print/{arxiv_id}", timeout=120
        )
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
        content = response.content
        if content.startswith(b"%PDF"):
            raise ScholarError("UNSUPPORTED", "pdf-only source")
        source_path.write_bytes(content)
        source_dir.mkdir(parents=True, exist_ok=True)
        if tarfile.is_tarfile(source_path):
            _extract_archive(source_path, source_dir)
        else:
            try:
                tex = gzip.decompress(content)
            except (OSError, EOFError):
                tex = content
            (source_dir / "main.tex").write_bytes(tex)

        time.sleep(3)
        metadata = {
            "arxiv_id": arxiv_id,
            "title": "",
            "authors": [],
            "published": "",
        }
        try:
            meta_response = http_client.get(
                "https://export.arxiv.org/api/query"
                f"?id_list={arxiv_id}",
                timeout=120,
            )
            if hasattr(meta_response, "raise_for_status"):
                meta_response.raise_for_status()
            meta_root = ET.fromstring(meta_response.content)
            entry = next(
                (item for item in meta_root.iter() if local_name(item.tag) == "entry"),
                None,
            )
            if entry is not None:
                metadata["title"] = _element_text(
                    next(
                        (item for item in entry if local_name(item.tag) == "title"),
                        None,
                    )
                )
                metadata["published"] = _element_text(
                    next(
                        (
                            item
                            for item in entry
                            if local_name(item.tag) == "published"
                        ),
                        None,
                    )
                )
                metadata["authors"] = [
                    _element_text(
                        next(
                            (
                                child
                                for child in author
                                if local_name(child.tag) == "name"
                            ),
                            None,
                        )
                    )
                    for author in entry
                    if local_name(author.tag) == "author"
                ]
        except Exception:
            pass
        (dest / "meta.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return source_dir
    finally:
        if own_client:
            http_client.close()


MANIFEST_FIELDS = [
    "paper_id",
    "title",
    "size_bytes",
    "sha256",
    "canonical_sha256",
    "abstract_chars",
    "creator_count",
    "section_count",
    "paragraph_count",
    "body_text_chars",
    "figure_count",
    "table_count",
    "math_count",
    "bibitem_count",
    "quality_tier",
]


class CorpusIngester:
    def ingest(
        self,
        sources: list[IngestSource],
        out_dir: Path,
        runner: LaTeXMLRunner,
    ) -> dict:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        results: list[IngestResult] = []
        rows: list[dict] = []
        for source in sources:
            latexml_dir = out_dir / source.paper_id / "latexml"
            latexml_dir.mkdir(parents=True, exist_ok=True)
            xml_path = latexml_dir / "paper.xml"
            log_path = latexml_dir / "paper.log"
            quality_path = latexml_dir / "quality.json"
            math_mode = "full"
            message = None
            try:
                main_tex = find_main_tex(source.tex_dir)
                completed = runner.run(source.tex_dir, main_tex, latexml_dir)
                math_mode = runner.last_math_mode
                if not log_path.exists():
                    log_path.write_text(
                        (completed.stdout or "") + (completed.stderr or ""),
                        encoding="utf-8",
                    )
                log_text = log_path.read_text(encoding="utf-8", errors="replace")
                if not xml_path.is_file():
                    raise ScholarError(
                        "INTERNAL",
                        f"LaTeXML did not produce {xml_path.name} "
                        f"(exit {completed.returncode})",
                    )
                tier, reasons, stats = grade_quality(xml_path, log_text, math_mode)
                title = _document_title(ET.parse(xml_path).getroot())
            except Exception as error:
                tier = "failed"
                reasons = [f"exception:{type(error).__name__}"]
                stats = _empty_stats(math_mode)
                message = str(error)
                if not log_path.exists():
                    log_path.write_text(message + "\n", encoding="utf-8")
                title = ""
            quality = {
                "paper_id": source.paper_id,
                "tier": tier,
                "reasons": reasons,
                "stats": stats,
                "math_mode": stats.get("math_mode", math_mode),
                "image": runner.image,
                "ingest_version": "latexml-ingest-v1",
            }
            if message:
                quality["message"] = message
            quality_path.write_text(
                json.dumps(quality, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            metadata = source.tex_dir.parent / "meta.json"
            metadata_target = out_dir / source.paper_id / "meta.json"
            try:
                if metadata.is_file():
                    if metadata.resolve() != metadata_target.resolve():
                        shutil.copyfile(metadata, metadata_target)
            except OSError:
                pass
            result = IngestResult(
                source.paper_id,
                tier,
                reasons,
                xml_path if xml_path.is_file() else None,
                log_path,
                quality_path,
            )
            results.append(result)
            if tier in {"clean", "degraded"} and xml_path.is_file():
                rows.append(manifest_row(source.paper_id, xml_path, title, stats, tier))

        rows.sort(key=lambda row: row["paper_id"])
        with (out_dir / "manifest.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=MANIFEST_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        failed = [item for item in results if item.status == "failed"]
        with (out_dir / "failed.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["paper_id", "reasons"])
            writer.writerows((item.paper_id, ";".join(item.reasons)) for item in failed)
        return {
            "out_dir": str(out_dir),
            "total": len(results),
            "clean": sum(item.status == "clean" for item in results),
            "degraded": sum(item.status == "degraded" for item in results),
            "failed": len(failed),
            "manifest": str(out_dir / "manifest.csv"),
        }
