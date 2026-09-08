"""Batch operations: auto-notes, quality-score, classify, bootstrap, batch-ingest, kb-update."""

import json
import typer
from typing import Optional
from rich.panel import Panel

from .._shared import app, console
from .. import config
from .. import db as dbmod
from ..tex_parser import parse_paper


# ===================================================================
# auto-notes: Generate reading notes
# ===================================================================
@app.command(name="auto-notes")
def auto_notes(
    paper_id: Optional[str] = typer.Argument(
        None, help="Paper ID (ULID/arXiv/DOI/slug, omit for batch mode)"
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite existing notes"),
):
    """Generate structured reading notes from parsed paper data."""
    from .. import auto_notes as an

    if paper_id:
        from ..id_resolver import resolve_id

        ulid = resolve_id(paper_id) or paper_id
        result = an.generate_single_note(ulid, force=force)
        console.print(
            Panel(
                f"Status: {result['status']}\nPath:   {result['path']}",
                title=f"Auto-Note: {ulid}",
            )
        )
    else:
        console.print("[cyan]Generating auto-notes for all papers...[/]")
        result = an.generate_all_notes(force=force)
        console.print(
            Panel(
                f"Created:  [green]{result['created']}[/]\n"
                f"Skipped:  {result['skipped']}\n"
                f"Failed:   [red]{result['failed']}[/]\n"
                f"Total:    {result['total']}",
                title="[green]Auto-Notes Complete[/]",
            )
        )


# ===================================================================
# quality-score: Quality scoring
# ===================================================================
@app.command(name="quality-score")
def quality_score(
    paper_id: Optional[str] = typer.Argument(
        None, help="Paper ID (ULID/arXiv/DOI/slug, omit for --all)"
    ),
    all_papers: bool = typer.Option(False, "--all", help="Score all papers"),
):
    """Score paper quality across 7 dimensions."""
    from .. import quality as q
    from rich.table import Table

    if paper_id:
        from ..id_resolver import resolve_id

        ulid = resolve_id(paper_id) or paper_id
        result = q.score_single_paper(ulid)
        if result is None:
            console.print(f"[red]Paper not found:[/] {ulid}")
            raise typer.Exit(1)
        table = Table(title=f"Quality Score: {ulid} (Grade: {result['grade']})")
        table.add_column("Dimension", width=20)
        table.add_column("Score", width=8)
        table.add_column("Details")
        for name, dim in result["dimensions"].items():
            table.add_row(
                name,
                f"{dim['score']}/{dim['max']}",
                ", ".join(str(d) for d in dim["details"][:3]),
            )
        table.add_row(
            "[bold]Total[/]",
            f"[bold]{result['total']}/{result['max_total']}[/]",
            f"Grade: [bold]{result['grade']}[/]",
        )
        console.print(table)
    elif all_papers:
        console.print("[cyan]Scoring all papers...[/]")
        result = q.score_all_papers()
        console.print(
            Panel(
                f"Scored:  [green]{result['scored']}[/]\n"
                f"Failed:  [red]{result['failed']}[/]\n"
                f"\n[bold]Grade Distribution:[/]\n"
                f"  A: {result['grades']['A']}  B: {result['grades']['B']}  "
                f"C: {result['grades']['C']}  D: {result['grades']['D']}  "
                f"F: {result['grades']['F']}",
                title="[green]Quality Scoring Complete[/]",
            )
        )
    else:
        console.print("Specify a ULID or use --all")


# ===================================================================
# classify: Paper classification
# ===================================================================
@app.command()
def classify(
    paper_id: Optional[str] = typer.Argument(
        None, help="Paper ID (ULID/arXiv/DOI/slug, omit for --all)"
    ),
    all_papers: bool = typer.Option(False, "--all", help="Classify all papers"),
    list_tags: bool = typer.Option(
        False, "--list-tags", help="List all tags in corpus"
    ),
):
    """Classify papers into domain/sub-direction/method tags."""
    from .. import classify as cl

    if list_tags:
        tags = cl.list_all_tags()
        console.print("[bold]Domains:[/]")
        for d, c in tags["domains"].items():
            console.print(f"  {d}: {c}")
        console.print("\n[bold]Sub-directions (top 15):[/]")
        for s, c in list(tags["sub_directions"].items())[:15]:
            console.print(f"  {s}: {c}")
        console.print("\n[bold]Methods (top 20):[/]")
        for m, c in list(tags["methods"].items())[:20]:
            console.print(f"  {m}: {c}")
    elif paper_id:
        from ..id_resolver import resolve_id

        ulid = resolve_id(paper_id) or paper_id
        result = cl.classify_single_paper(ulid)
        if result is None:
            console.print(f"[red]Paper not found:[/] {paper_id}")
            raise typer.Exit(1)
        console.print(
            Panel(
                f"Domains:        {', '.join(result['domains'])}\n"
                f"Sub-directions: {', '.join(result['sub_directions'])}\n"
                f"Methods:        {', '.join(result['methods'][:8])}",
                title=f"Classification: {ulid}",
            )
        )
    elif all_papers:
        console.print("[cyan]Classifying all papers...[/]")
        result = cl.classify_all_papers()
        console.print(
            Panel(
                f"Classified: [green]{result['classified']}[/]\n"
                f"Failed:     [red]{result['failed']}[/]\n"
                f"\n[bold]Domain Distribution:[/]\n"
                + "\n".join(
                    f"  {d}: {c}"
                    for d, c in sorted(
                        result["domain_counts"].items(), key=lambda x: -x[1]
                    )
                ),
                title="[green]Classification Complete[/]",
            )
        )
    else:
        console.print("Specify a ULID, use --all, or --list-tags")


# ===================================================================
# bootstrap: Full initialization pipeline
# ===================================================================
@app.command()
def bootstrap():
    """Full initialization: parse -> year-fix -> author-fix -> auto-notes -> quality -> classify."""
    from .. import auto_notes as an
    from .. import quality as q
    from .. import classify as cl
    from .. import year_fix as yf

    console.print(
        Panel(
            "[bold]Scholar Studio Bootstrap[/]\nFull initialization pipeline",
            title="Bootstrap",
        )
    )

    # Step 1: Parse all
    console.print("\n[cyan][1/6] Parsing all papers...[/]")
    parsed_ids = dbmod.list_parsed()
    paper_dirs = [d for d in config.PAPERS_DIR.iterdir() if d.is_dir()]
    unparsed = len(paper_dirs) - len(parsed_ids)
    if unparsed > 0:
        console.print(f"  {unparsed} papers to parse...")
        parsed_count = 0
        for d in paper_dirs:
            if d.name not in set(parsed_ids):
                try:
                    data = parse_paper(d, d.name)
                    dbmod.save_parsed(data)
                    parsed_count += 1
                except Exception:
                    pass
        console.print(f"  Parsed [green]{parsed_count}[/] new papers")
    else:
        console.print("  All papers already parsed")

    # Step 2: Year fix
    console.print("\n[cyan][2/6] Completing years (Lean4 + heuristics)...[/]")
    stats, _ = yf.complete_years(dry_run=False)
    console.print(
        f"  Filled: [green]{stats['filled']}[/], Still missing: {stats['still_missing']}"
    )

    # Step 3: Author fix (arXiv API)
    console.print("\n[cyan][3/6] Completing authors (arXiv API)...[/]")
    try:
        a_stats = yf.complete_authors_arxiv(limit=100, dry_run=False)
        console.print(
            f"  Queried: {a_stats['queried']}, Filled: [green]{a_stats['filled']}[/], "
            f"Skipped (have authors): {a_stats['skipped_have_authors']}"
        )
    except Exception as e:
        console.print(f"  [yellow]Author fix skipped: {e}[/]")

    # Step 4: V2 projection note
    console.print("\n[cyan][4/6] V2 projections[/]")
    console.print("  [dim]Use 'scholar v2 build-graph' and 'scholar v2 build-vectors' after import.[/]")

    # Step 5: Auto-notes
    console.print("\n[cyan][5/6] Generating auto-notes...[/]")
    notes_result = an.generate_all_notes(force=False)
    console.print(
        f"  Created: [green]{notes_result['created']}[/], Skipped: {notes_result['skipped']}"
    )

    # Step 5: Quality scoring
    console.print("\n[cyan][5/6] Scoring quality...[/]")
    q_result = q.score_all_papers()
    console.print(
        f"  Scored: [green]{q_result['scored']}[/], Grades: A={q_result['grades']['A']} B={q_result['grades']['B']} C={q_result['grades']['C']}"
    )

    # Step 6: Classification
    console.print("\n[cyan][6/6] Classifying papers...[/]")
    cl_result = cl.classify_all_papers()
    console.print(f"  Classified: [green]{cl_result['classified']}[/]")

    console.print(
        Panel(
            f"Papers:     {len(paper_dirs)}\n"
            f"Notes:      {notes_result['created'] + notes_result['skipped']}\n"
            f"Quality:    {q_result['scored']} scored\n"
            f"Classified: {cl_result['classified']}\n"
            f"\n[bold green]Bootstrap complete![/]",
            title="[green]Bootstrap Complete",
        )
    )


# ===================================================================
# batch-ingest: Batch ingest papers
# ===================================================================
@app.command(name="batch-ingest")
def batch_ingest(
    ulids: str = typer.Option("", help="Comma-separated ULIDs (empty=all unparsed)"),
    skip_notes: bool = typer.Option(
        False, "--skip-notes", help="Skip auto-notes generation"
    ),
    skip_quality: bool = typer.Option(
        False, "--skip-quality", help="Skip quality scoring"
    ),
):
    """Batch ingest papers through the full pipeline."""
    from .. import kb_update as kb

    ulid_list = [u.strip() for u in ulids.split(",") if u.strip()] if ulids else None
    console.print(
        f"[cyan]Batch ingesting {len(ulid_list) if ulid_list else 'all unparsed'} papers..."
    )

    stats = kb.batch_ingest(
        ulids=ulid_list, skip_notes=skip_notes, skip_quality=skip_quality
    )

    console.print(
        Panel(
            f"Total:      {stats['total']}\n"
            f"Parsed:     [green]{stats['parsed']}[/]\n"
            f"Enriched:   {stats['enriched']}\n"
            f"Noted:      {stats['noted']}\n"
            f"Scored:     {stats['scored']}\n"
            f"Classified: {stats['classified']}\n"
            f"Errors:     [red]{len(stats['errors'])}[/]",
            title="Batch Ingest Complete",
        )
    )
    for err in stats["errors"][:5]:
        console.print(f"  [red]x[/] {err['paper_id']}: {err['step']} - {err['error']}")


# ===================================================================
# kb-update: One-command knowledge base update
# ===================================================================
@app.command(name="kb-update")
def kb_update(
    query: str = typer.Option("", help="arXiv search query (empty=local only)"),
    max_results: int = typer.Option(10, "--max", help="Max papers to download"),
    pdf: bool = typer.Option(
        True, "--pdf/--no-pdf", help="Also download PDF (default: yes)"
    ),
):
    """One-command knowledge base update: search -> download -> ingest."""
    from .. import kb_update as kb

    if query:
        console.print(f"[cyan]KB Update:[/] searching '{query}' on arXiv...")
    else:
        console.print("[cyan]KB Update:[/] processing local unparsed papers...")

    results = kb.kb_update(query=query, max_results=max_results, download_pdf=pdf)

    dl = results.get("downloaded", [])
    ing = results.get("ingest", {})

    console.print(
        Panel(
            f"Downloaded: [green]{len([r for r in dl if r.get('status') == 'downloaded'])}[/]\n"
            f"Parsed:     [green]{ing.get('parsed', 0)}[/]\n"
            f"Enriched:   {ing.get('enriched', 0)}\n"
            f"Errors:     [red]{len(ing.get('errors', []))}[/]",
            title="[green]KB Update Complete[/]",
        )
    )
