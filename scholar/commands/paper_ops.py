"""Paper operations: parse, parse-all, ingest, export-bib."""
import json
from typing import Optional

import typer
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.panel import Panel

from .._shared import app, console
from .. import config
from ..tex_parser import parse_paper
from .. import db as dbmod


# ===================================================================
# parse: Parse a single paper
# ===================================================================
@app.command()
def parse(paper_id: str = typer.Argument(help="Paper ID (ULID/arXiv/DOI/slug)")):
    """Parse a single paper's TeX source into structured JSON."""
    from ..id_resolver import resolve_id
    ulid = resolve_id(paper_id) or paper_id
    paper_dir = config.PAPERS_DIR / ulid
    if not paper_dir.exists():
        console.print(f"[red]Error:[/] Paper directory not found: {paper_dir}")
        raise typer.Exit(1)

    console.print(f"Parsing [cyan]{ulid}[/]...")
    try:
        data = parse_paper(paper_dir, ulid)
        out_path = dbmod.save_parsed(data)

        console.print(Panel(
            f"Title:     {data.get('title', 'N/A')}\n"
            f"Authors:   {', '.join(data.get('authors', [])[:5])}{'...' if len(data.get('authors', [])) > 5 else ''}\n"
            f"Year:      {data.get('year', 'N/A')}\n"
            f"Venue:     {data.get('venue', 'N/A')}\n"
            f"Sections:  {len(data.get('sections', []))}\n"
            f"Formulas:  {len(data.get('formulas', []))}\n"
            f"Citations: {len(data.get('citations', []))}\n"
            f"Output:    {out_path}",
            title=f"[green]Parsed OK[/] {ulid}",
        ))
    except Exception as e:
        console.print(f"[red]Parse failed:[/] {e}")
        raise typer.Exit(1)


# ===================================================================
# parse-all: Batch parse all papers
# ===================================================================
@app.command(name="parse-all")
def parse_all(
    limit: int = typer.Option(0, help="Max papers to parse (0=all)"),
    force: bool = typer.Option(False, help="Re-parse already parsed"),
):
    """Batch parse all papers' TeX sources."""
    paper_dirs = sorted(config.PAPERS_DIR.iterdir())
    paper_dirs = [d for d in paper_dirs if d.is_dir()]
    parsed_ids = set(dbmod.list_parsed())

    if not force:
        paper_dirs = [d for d in paper_dirs if d.name not in parsed_ids]

    if limit > 0:
        paper_dirs = paper_dirs[:limit]

    console.print(f"Parsing {len(paper_dirs)} papers...")

    success = 0
    failed = 0
    errors = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Parsing...", total=len(paper_dirs))
        for d in paper_dirs:
            ulid = d.name
            progress.update(task, description=f"Parsing {ulid[:16]}...")
            try:
                data = parse_paper(d, ulid)
                dbmod.save_parsed(data)

                success += 1
            except Exception as e:
                failed += 1
                errors.append((ulid, str(e)))
            progress.advance(task)

    console.print(
        Panel(
            f"Success: [green]{success}[/]  |  Failed: [red]{failed}[/]",
            title="Batch Parse Complete",
        )
    )
    if errors:
        console.print("[yellow]Failed papers:[/]")
        for ulid, err in errors[:20]:
            console.print(f"  {ulid}: {err}")
        if len(errors) > 20:
            console.print(f"  ... and {len(errors) - 20} more")


# ===================================================================
# export-bib: Generate BibTeX from parsed papers
# ===================================================================
@app.command(name="export-bib")
def export_bib(
    output: str = typer.Option("output/bib/references.bib", help="Output .bib file path"),
):
    """Export BibTeX entries for all parsed papers."""
    entries = []
    for paper_id in dbmod.list_parsed():
        data = dbmod.load_parsed(paper_id)
        if not data:
            continue
        title = data.get("title", "Untitled")
        authors = " and ".join(data.get("authors", ["Unknown"]))
        year = data.get("year", "")
        venue = data.get("venue", "")

        cite_key = paper_id

        entry = f"@article{{{cite_key},\n"
        entry += f"  title = {{{title}}},\n"
        entry += f"  author = {{{authors}}},\n"
        if year:
            entry += f"  year = {{{year}}},\n"
        if venue:
            entry += f"  journal = {{{venue}}},\n"
        entry += "}\n"
        entries.append(entry)

    out_path = config.PROJECT_ROOT / output
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(entries), encoding="utf-8")
    console.print(f"Exported [green]{len(entries)}[/] BibTeX entries to {out_path}")


# ===================================================================
# ingest: Incremental paper ingestion
# ===================================================================
@app.command()
def ingest(
    paper_id: str = typer.Argument(help="Paper ID (ULID/arXiv/DOI/slug)"),
):
    """Ingest a single new paper: parse -> author-fix -> auto-notes -> quality -> classify."""
    from ..id_resolver import resolve_id
    from .. import auto_notes as an
    from .. import quality as q
    from .. import classify as cl
    from .. import year_fix as yf

    ulid = resolve_id(paper_id) or paper_id
    paper_dir = config.PAPERS_DIR / ulid
    if not paper_dir.exists():
        console.print(f"[red]Error:[/] Paper directory not found: {paper_dir}")
        raise typer.Exit(1)

    console.print(f"[cyan]Ingesting {ulid}...[/]")

    # 1. Parse
    console.print("  [1/6] Parsing...")
    try:
        data = parse_paper(paper_dir, ulid)
        dbmod.save_parsed(data)
        console.print(f"  Title: {(data.get('title') or 'N/A')[:60]}")
    except Exception as e:
        console.print(f"  [red]Parse failed: {e}[/]")
        raise typer.Exit(1)

    # 2. Author fix (only if authors missing)
    console.print("  [2/6] Checking authors...")
    json_path = config.PARSED_DIR / f"{ulid}.json"
    try:
        paper_data = json.loads(json_path.read_text(encoding="utf-8"))
        authors = paper_data.get("authors", [])
        if isinstance(authors, str):
            try:
                import ast as _ast
                authors = _ast.literal_eval(authors)
            except Exception:
                authors = []
        if not authors:
            new_authors = yf.fetch_arxiv_authors(paper_data.get("title", ""))
            if new_authors:
                paper_data["authors"] = new_authors
                json_path.write_text(
                    json.dumps(paper_data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                console.print(f"  Filled {len(new_authors)} authors from arXiv")
            else:
                console.print("  [yellow]No arXiv result, authors left empty[/]")
    except Exception as e:
        console.print(f"  [yellow]Author check skipped: {e}[/]")

    # 3. Auto-notes
    console.print("  [3/6] Generating note...")
    an.generate_single_note(ulid, force=True)

    # 4. Quality
    console.print("  [4/6] Scoring quality...")
    q_result = q.score_single_paper(ulid)
    if q_result:
        console.print(f"  Grade: {q_result['grade']} ({q_result['total']}/{q_result['max_total']})")

    # 5. Classify
    console.print("  [5/6] Classifying...")
    cl_result = cl.classify_single_paper(ulid)
    if cl_result:
        console.print(f"  Domains: {', '.join(cl_result['domains'])}")

    console.print(f"\n[green]Ingested {ulid} successfully.[/]")
