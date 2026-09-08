"""
Scholar Studio — CLI Entry Point

Shared objects (app, console, parser) live in _shared.py.
Command implementations live in commands/*.py.

Usage: python -m scholar <command> [options]
"""

from ._shared import app  # noqa: F401

# Import command modules — each registers @app.command() decorators
from .commands import core_ops  # noqa: F401  init, scan, info, search, list-papers, stats
from .commands import paper_ops  # noqa: F401  parse, parse-all, ingest, export-bib
from .commands import metadata_ops  # noqa: F401  year-fix, author-fix, venue-fix, metadata-enrich
from .commands import batch_ops  # noqa: F401  auto-notes, quality-score, classify, bootstrap, batch-ingest, kb-update
from .commands import research_ops  # noqa: F401  interests, research-sync, survey, landscape
from .commands import execution_ops  # noqa: F401  compile-paper, exp-*, dataset-download
from .commands import external_ops  # noqa: F401  arxiv-search, arxiv-download
from .commands import dsh_ops  # noqa: F401  init-dsh
from .commands import v2_ops  # noqa: F401  v2 corpus/build/snapshot operations
from . import lean_sync  # noqa: F401  lean-sync, lean-templates


def main():
    app()
