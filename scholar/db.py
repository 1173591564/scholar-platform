"""
Scholar Studio — Parsed-paper file I/O

V1 PostgreSQL `Database` class removed; the v2 data plane uses
`scholar.v2.database.V2Database`. This module now only handles
legacy parsed JSON artifacts on disk.
"""
import json
import re
from pathlib import Path
from typing import Optional

from jsonschema import ValidationError

from . import config
from .parsed_schema import (
    VNEXT_DIRNAME,
    legacy_projection,
    validate_parsed_document,
)

_PAPER_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")


def parsed_path(paper_id: str, parsed_dir: Path = None) -> Path:
    """Return the contained parsed JSON path for a safe paper identifier."""
    if not isinstance(paper_id, str) or not _PAPER_ID_PATTERN.fullmatch(paper_id):
        raise ValueError("invalid paper identifier")
    root = Path(parsed_dir) if parsed_dir is not None else config.PARSED_DIR
    resolved_root = root.resolve()
    path = (resolved_root / f"{paper_id}.json").resolve()
    if not path.is_relative_to(resolved_root):
        raise ValueError("invalid paper identifier")
    return path


# ---------------------------------------------------------------
# File-only parsed JSON save/load
# ---------------------------------------------------------------

def save_parsed(data: dict, parsed_dir: Path = None):
    """Save parsed vNext and its legacy reader projection."""
    if parsed_dir is None:
        parsed_dir = config.PARSED_DIR
    parsed_dir.mkdir(parents=True, exist_ok=True)
    legacy = data
    if data.get("schema_version") is not None:
        validate_parsed_document(data)
        vnext_dir = parsed_dir / VNEXT_DIRNAME
        vnext_dir.mkdir(parents=True, exist_ok=True)
        vnext_path = parsed_path(data["paper_id"], vnext_dir)
        vnext_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        legacy = legacy_projection(data)
    out_path = parsed_path(legacy["paper_id"], parsed_dir)
    out_path.write_text(
        json.dumps(legacy, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return out_path


def load_parsed_vnext(paper_id: str, parsed_dir: Path = None) -> Optional[dict]:
    """Load and validate a parsed vNext artifact when available."""
    if parsed_dir is None:
        parsed_dir = config.PARSED_DIR
    try:
        path = parsed_path(paper_id, parsed_dir / VNEXT_DIRNAME)
    except ValueError:
        return None
    if not path.exists():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        validate_parsed_document(document)
        return document
    except (OSError, json.JSONDecodeError, ValidationError):
        return None


def load_parsed(paper_id: str, parsed_dir: Path = None) -> Optional[dict]:
    """Load parsed paper data from JSON."""
    try:
        path = parsed_path(paper_id, parsed_dir)
    except ValueError:
        return None
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
    return None


def list_parsed(parsed_dir: Path = None) -> list[str]:
    """List all parsed paper IDs."""
    if parsed_dir is None:
        parsed_dir = config.PARSED_DIR
    return [p.stem for p in parsed_dir.glob("*.json")]
