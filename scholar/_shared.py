"""
Scholar Studio — Shared CLI Objects

Defines the Typer app, console, parser, and database helper used by both
cli.py (entry point) and commands/*.py (command implementations).

This module has NO circular imports — it only depends on scholar.tex_parser
and scholar.db, which are independent domain modules.
"""
import typer
from rich.console import Console

from .tex_parser import TeXParser

# ===================================================================
# Shared objects
# ===================================================================

app = typer.Typer(
    name="scholar",
    help="Scholar Studio — Academic Research Toolkit",
    no_args_is_help=True,
)
console = Console()
parser = TeXParser()
