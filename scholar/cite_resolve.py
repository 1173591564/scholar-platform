"""
Scholar Studio — Citation Resolution Enhancement (V2)

Resolves citation references using a 3-level matching pipeline:
  1. DOI exact match (from .bib bibliography → parsed JSON doi field)
  2. Title fuzzy match (rapidfuzz token_sort_ratio ≥ 85)
  3. arXiv API fallback (for unresolved refs)

Key improvement over V1: uses bibliography titles instead of ref_keys for matching.
A ref_key like "vaswani2017" cannot fuzzy-match "Attention Is All You Need",
but the bibliography entry provides the actual title.

CLI: scholar cite-resolve [--limit N] [--dry-run]
"""
import json
import time
import re
import ast
import hashlib
from pathlib import Path
from typing import Optional
from collections import Counter

from . import config


# ===================================================================
# Normalization
# ===================================================================

def _normalize(text: str) -> str:
    """Normalize text for matching."""
    if not text:
        return ""
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _cache_key(text: str) -> str:
    """Generate a filesystem-safe cache key from text."""
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:16]


# ===================================================================
# Rapidfuzz-based fuzzy matching (100x faster than Levenshtein)
# ===================================================================

try:
    from rapidfuzz import fuzz
    _HAS_RAPIDFUZZ = True
except ImportError:
    _HAS_RAPIDFUZZ = False

    def _levenshtein(s1: str, s2: str) -> int:
        if len(s1) < len(s2):
            return _levenshtein(s2, s1)
        if len(s2) == 0:
            return len(s1)
        prev_row = range(len(s2) + 1)
        for i, c1 in enumerate(s1):
            curr_row = [i + 1]
            for j, c2 in enumerate(s2):
                insertions = prev_row[j + 1] + 1
                deletions = curr_row[j] + 1
                substitutions = prev_row[j] + (c1 != c2)
                curr_row.append(min(insertions, deletions, substitutions))
            prev_row = curr_row
        return prev_row[-1]

    def _similarity(s1: str, s2: str) -> float:
        max_len = max(len(s1), len(s2))
        if max_len == 0:
            return 1.0
        return 1.0 - _levenshtein(s1, s2) / max_len


def _title_similarity(s1: str, s2: str) -> float:
    """Return 0-100 similarity score using rapidfuzz or fallback."""
    if _HAS_RAPIDFUZZ:
        return fuzz.token_sort_ratio(s1, s2)
    else:
        return _similarity(_normalize(s1), _normalize(s2)) * 100


# ===================================================================
# Internal index: title → paper info + DOI → paper info
# ===================================================================

def build_internal_index(parsed_dir: Path = None) -> dict:
    """
    Build index of all known papers.

    Returns:
        {
            "titles": {normalized_title: {ulid, title, year, doi}},
            "dois": {doi_lower: {ulid, title, year}},
            "count": int
        }
    """
    if parsed_dir is None:
        parsed_dir = config.PARSED_DIR

    titles = {}
    dois = {}

    for json_file in parsed_dir.glob("*.json"):
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        ulid = data.get("paper_id", "")
        title = data.get("title", "")
        year = data.get("year", "")
        doi = data.get("doi", "")

        info = {"ulid": ulid, "title": title, "year": year, "doi": doi}

        if title:
            titles[_normalize(title)] = info

        if doi:
            dois[doi.lower().strip()] = info

    return {"titles": titles, "dois": dois, "count": len(titles)}


# ===================================================================
# Level 1: DOI exact match
# ===================================================================

def match_via_doi(doi: str, internal_index: dict) -> Optional[dict]:
    """Try to match a DOI to a known internal paper."""
    if not doi:
        return None
    doi_lower = doi.lower().strip()
    return internal_index["dois"].get(doi_lower)


# ===================================================================
# Level 2: Title fuzzy match (rapidfuzz)
# ===================================================================

def match_via_title(title: str, internal_index: dict, threshold: float = 85) -> Optional[dict]:
    """
    Try to match a title to a known internal paper using rapidfuzz.

    Strategy:
    1. Exact normalized match
    2. token_sort_ratio ≥ threshold
    3. partial_ratio ≥ threshold (for substring titles)
    """
    if not title:
        return None

    norm_title = _normalize(title)
    titles = internal_index["titles"]

    # Exact match
    if norm_title in titles:
        return titles[norm_title]

    if not _HAS_RAPIDFUZZ:
        # Fallback: Levenshtein similarity
        best_match = None
        best_sim = 0
        for norm_t, info in titles.items():
            sim = _similarity(norm_title, norm_t) * 100
            if sim > best_sim:
                best_sim = sim
                best_match = info
        if best_sim >= threshold:
            return best_match
        return None

    # rapidfuzz: use process.extractOne for speed
    from rapidfuzz import process, fuzz

    choices = list(titles.keys())
    if not choices:
        return None

    # token_sort_ratio handles word reordering
    result = process.extractOne(
        norm_title, choices,
        scorer=fuzz.token_sort_ratio,
        score_cutoff=threshold,
    )
    if result:
        matched_title, score, _ = result
        return titles[matched_title]

    # partial_ratio for short titles that are substrings
    result = process.extractOne(
        norm_title, choices,
        scorer=fuzz.partial_ratio,
        score_cutoff=95,  # Higher threshold for partial to avoid false positives
    )
    if result:
        matched_title, score, _ = result
        # Verify length ratio to avoid matching "GAN" to "Origins of the Human Brain"
        len_ratio = min(len(norm_title), len(matched_title)) / max(len(norm_title), len(matched_title))
        if len_ratio > 0.5:
            return titles[matched_title]

    return None


# ===================================================================
# Level 3: arXiv API fallback (with disk cache)
# ===================================================================

def resolve_via_arxiv(query: str, cache_dir: Path = None) -> Optional[dict]:
    """
    Try to resolve a citation via arXiv API.
    Uses disk cache to avoid redundant API calls.

    Returns: {title, authors, year, arxiv_id} or None
    """
    # Check disk cache
    if cache_dir is None:
        cache_dir = config.OUTPUT_DIR / "cache" / "cite_resolve"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{_cache_key(query)}.json"

    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            return cached if cached.get("_cached") else cached
        except Exception:
            pass

    try:
        import xml.etree.ElementTree as ET
        from . import config as _cfg

        search_query = query.replace("_", " ").replace("-", " ")
        xml_data = _cfg.arxiv_request(f"ti:{search_query[:200]}", max_results=1)

        ns = {"atom": "http://www.w3.org/2005/Atom"}
        root = ET.fromstring(xml_data)
        entries = root.findall("atom:entry", ns)

        if entries:
            entry = entries[0]
            title = entry.find("atom:title", ns).text.strip().replace("\n", " ")
            authors = [
                a.find("atom:name", ns).text
                for a in entry.findall("atom:author", ns)
            ]
            published = entry.find("atom:published", ns).text
            arxiv_id = entry.find("atom:id", ns).text.split("/abs/")[-1]

            title_sim = _title_similarity(_normalize(query), _normalize(title))
            if title_sim >= 70:
                result = {
                    "title": title,
                    "authors": authors[:5],
                    "year": int(published[:4]) if published else None,
                    "arxiv_id": arxiv_id,
                    "similarity": title_sim,
                }
                # Cache positive result
                cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
                return result
    except Exception:
        pass

    # Cache negative result
    cache_file.write_text(json.dumps({"_cached": True, "result": None}, ensure_ascii=False), encoding="utf-8")
    return None


# ===================================================================
# Neo4j ExternalPaper node creation
# ===================================================================
# Main resolution pipeline (V2)
# ===================================================================

# ===================================================================
# refs-resolved.json sidecar (replaces Neo4j ExternalPaper persistence)
# ===================================================================

def _sidecar_path() -> Path:
    return config.OUTPUT_DIR / "index" / "refs-resolved.json"


def merge_sidecar(resolved_map: dict, externals: list[dict]) -> None:
    """Merge new resolutions into output/index/refs-resolved.json.

    Format: {"refs": {ref_key: ulid}, "external": {ref_key: {title, year,
    arxiv_id}}, "updatedAt": iso}. graph_mem consumes the "refs" section as
    curated ref_key -> ULID edges.
    """
    path = _sidecar_path()
    data = {"refs": {}, "external": {}}
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(existing, dict):
            data["refs"] = existing.get("refs") or {}
            data["external"] = existing.get("external") or {}
    except Exception:
        pass
    data["refs"].update(resolved_map)
    for p in externals:
        data["external"][p["ref_key"]] = {
            "title": p.get("title", ""),
            "year": p.get("year"),
            "arxiv_id": p.get("arxiv_id", ""),
        }
    data["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                    encoding="utf-8")


def resolve_citations(parsed_dir: Path = None, limit: int = 200,
                      dry_run: bool = True) -> dict:
    """
    Run the full citation resolution pipeline (V2).

    3-level matching:
    1. DOI exact match (from bibliography → parsed JSON doi)
    2. Title fuzzy match (rapidfuzz token_sort_ratio ≥ 85)
    3. arXiv API fallback

    Returns statistics.
    """
    if parsed_dir is None:
        parsed_dir = config.PARSED_DIR

    # Step 1: Build internal index (titles + DOIs)
    internal_index = build_internal_index(parsed_dir)

    # Step 2: Collect all citations with bibliography context
    # ref_key -> {from_ulids: [...], bib_entry: {...} | None}
    all_refs: dict[str, dict] = {}

    for json_file in parsed_dir.glob("*.json"):
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        ulid = data.get("paper_id", "")
        citations = data.get("citations", [])
        if isinstance(citations, str):
            try:
                citations = ast.literal_eval(citations)
            except Exception:
                citations = []

        # Build bibliography lookup: ref_key -> bib_entry
        bibliography = data.get("bibliography", [])
        bib_lookup = {}
        if isinstance(bibliography, list):
            for bib in bibliography:
                if isinstance(bib, dict) and bib.get("ref_key"):
                    bib_lookup[bib["ref_key"]] = bib

        for ref in citations:
            if ref not in all_refs:
                all_refs[ref] = {"from_ulids": [], "bib_entry": None}
            all_refs[ref]["from_ulids"].append(ulid)

            # Attach bibliography entry if available
            if ref in bib_lookup and all_refs[ref]["bib_entry"] is None:
                all_refs[ref]["bib_entry"] = bib_lookup[ref]

    total_refs = len(all_refs)
    resolved_map: dict[str, str] = {}

    # Step 3: Level 1 — DOI exact match
    resolved_doi = 0
    unresolved_after_l1 = {}

    for ref_key, info in all_refs.items():
        bib = info.get("bib_entry") or {}
        doi = bib.get("doi", "")
        if doi:
            match = match_via_doi(doi, internal_index)
            if match:
                resolved_doi += 1
                if match.get("ulid"):
                    resolved_map[ref_key] = match["ulid"]
                continue
        unresolved_after_l1[ref_key] = info

    # Step 4: Level 2 — Title fuzzy match
    resolved_title = 0
    unresolved_after_l2 = {}

    for ref_key, info in unresolved_after_l1.items():
        bib = info.get("bib_entry") or {}
        title = bib.get("title", "")

        # If no title from bibliography, try ref_key as fallback
        if not title:
            title = ref_key.replace("_", " ")

        match = match_via_title(title, internal_index, threshold=85)
        if match:
            resolved_title += 1
            if match.get("ulid"):
                resolved_map[ref_key] = match["ulid"]
            continue
        unresolved_after_l2[ref_key] = info

    # Step 5: Level 3 — arXiv API fallback
    resolved_arxiv = 0
    external_papers = []
    queried = 0

    for ref_key, info in unresolved_after_l2.items():
        if queried >= limit:
            break
        queried += 1

        bib = info.get("bib_entry") or {}
        # Use title for arXiv search if available, else ref_key
        search_term = bib.get("title", "") or ref_key.replace("_", " ")

        result = resolve_via_arxiv(search_term)
        if result:
            resolved_arxiv += 1
            result["ref_key"] = ref_key
            result["from_ulid"] = info["from_ulids"][0] if info["from_ulids"] else None
            external_papers.append(result)

    # Step 6: Persist resolutions to refs-resolved.json sidecar (graph_mem reads it)
    sidecar_refs = 0
    sidecar_external = 0
    if not dry_run and (resolved_map or external_papers):
        try:
            sidecar_refs = len(resolved_map)
            sidecar_external = len(external_papers)
            merge_sidecar(resolved_map, external_papers)
        except Exception:
            sidecar_refs = 0
            sidecar_external = 0

    still_unresolved = len(unresolved_after_l2) - resolved_arxiv

    return {
        "total_refs": total_refs,
        "unique_refs": total_refs,
        "resolved_doi": resolved_doi,
        "resolved_title": resolved_title,
        "resolved_arxiv": resolved_arxiv,
        "resolved_total": resolved_doi + resolved_title + resolved_arxiv,
        "resolution_rate": f"{(resolved_doi + resolved_title + resolved_arxiv) / max(total_refs, 1) * 100:.1f}%",
        "sidecar_refs": sidecar_refs,
        "sidecar_external": sidecar_external,
        "still_unresolved": still_unresolved,
        "queried_arxiv": queried,
        "has_rapidfuzz": _HAS_RAPIDFUZZ,
    }
