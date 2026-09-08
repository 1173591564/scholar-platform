"""Core operations: init, scan, info, search, list-papers, stats."""
import json
from typing import Optional

import typer
from rich.table import Table
from rich.panel import Panel

from .._shared import app, console
from .. import config
from .. import db as dbmod


def _runtime_mode() -> str:
    if config.IS_FROZEN:
        return "frozen (.exe)"
    return "development (source)" if config.IS_SOURCE_TREE else "installed package"


# ===================================================================
# init: Initialize global knowledge base
# ===================================================================
@app.command()
def init():
    """Initialize Scholar Studio global knowledge base (~/.scholar-studio/).

    Creates the directory structure and .env.example file.
    Run this once after installing scholar.exe.
    """
    console.print("[cyan]Initializing Scholar Studio...[/]\n")

    result = config.init_scholar_home()
    home = result["home"]
    created = result["created"]

    if result["already_exists"]:
        console.print("[green][OK][/green] Knowledge base already initialized at [bold]{0}[/bold]".format(home))
    else:
        console.print("[green][OK][/green] Created knowledge base at [bold]{0}[/bold]".format(home))
        for d in created:
            console.print("  [dim]+[/dim] {0}".format(d))

    console.print("\n[bold]Next steps:[/bold]")
    console.print("  1. Copy .env.example to .env and fill in API keys:")
    console.print("     Copy-Item {0} {1}/.env".format(result['env_example'], home))
    console.print("  2. Start Docker services:")
    console.print("     cd <project>/infra && docker compose up -d")
    console.print("  3. Run your first command:")
    console.print("     scholar stats")

    console.print("\n[bold]Service check:[/bold]")
    console.print("  [green][OK][/green] File-only mode (V2 data plane uses V2Database)")

    mode = _runtime_mode()
    console.print("\n[dim]Mode: {0} | Home: {1}[/dim]".format(mode, home))


# ===================================================================
# init-workspace: Initialize a workspace directory
# ===================================================================
@app.command(name="init-workspace")
def init_workspace_cmd(
    target: str = typer.Argument(
        ".",
        help="Target project directory (default: current dir). Example: C:\\Projects\\MyProject",
    ),
):
    """Initialize workspace output directories (drafts/notes/logs).

    Run this in the target project directory, or pass the path as argument.
    SCHOLAR_HOME (paper data) stays untouched; SCHOLAR_WORKSPACE points to the target.
    """
    from pathlib import Path as _Path

    target_path = _Path(target).resolve()
    console.print(f"[cyan]Initializing workspace at {target_path}...[/]\n")

    result = config.init_workspace(target_dir=str(target_path))
    ws = result["workspace"]
    created = result["created"]

    if result["already_exists"]:
        console.print("[green][OK][/green] Workspace already initialized at [bold]{0}[/bold]".format(ws))
    else:
        console.print("[green][OK][/green] Created workspace:")
        for d in created:
            console.print("  [dim]+[/dim] {0}".format(d))

    console.print("\n[bold]Configuration:[/bold]")
    console.print("  [dim]Paper data (SCHOLAR_HOME):[/dim]  {0}".format(result["scholar_home"]))
    console.print("  [dim]Workspace (SCHOLAR_WORKSPACE):[/dim] {0}".format(ws))
    console.print("    drafts/  -> {0}".format(result["drafts_dir"]))
    console.print("    notes/   -> {0}".format(result["notes_dir"]))
    console.print("    logs/    -> {0}".format(result["logs_dir"]))

    console.print("\n[green]Done![/] Workspace output directories are ready.")


# ===================================================================
# doctor: Diagnose Scholar Studio configuration
# ===================================================================
@app.command()
def doctor():
    """Diagnose Scholar Studio configuration and MCP server reachability."""
    console.print("[cyan]Scholar Studio Doctor[/]\n")

    # 1. Check MCP server
    try:
        import importlib
        spec = importlib.util.find_spec("scholar_mcp")
        if spec:
            console.print("  [green][OK][/green] MCP server module: available")
        else:
            console.print("  [yellow][!!][/yellow] MCP server module: not found")
    except Exception:
        console.print("  [yellow][!!][/yellow] MCP server module: not found")

    # Summary
    mode = _runtime_mode()
    console.print("\n[dim]Mode: {0} | Home: {1}[/dim]".format(mode, config.SCHOLAR_HOME))


# ===================================================================
# scan: Scan papers directory and show status
# ===================================================================
@app.command()
def scan():
    """Scan all papers and show parsing status."""
    paper_dirs = sorted(config.PAPERS_DIR.iterdir()) if config.PAPERS_DIR.exists() else []
    paper_dirs = [d for d in paper_dirs if d.is_dir()]

    parsed_ids = set(dbmod.list_parsed())

    table = Table(title=f"Paper Library ({len(paper_dirs)} papers)")
    table.add_column("Status", width=6)
    table.add_column("ULID", width=28)
    table.add_column("Has Source", width=10)
    table.add_column("Has PDF", width=8)
    table.add_column("Parsed", width=8)

    parsed_count = 0
    has_source = 0
    has_pdf = 0

    display_dirs = paper_dirs
    omitted = 0
    if len(paper_dirs) > 30:
        head = paper_dirs[:15]
        tail = paper_dirs[-5:]
        display_dirs = head + tail
        omitted = len(paper_dirs) - len(head) - len(tail)

    for d in display_dirs:
        ulid = d.name
        src = any(
            (d / name).exists()
            for name in ["source.tar.gz", "source.tgz", "source.tar", "source.zip"]
        )
        pdf = (d / "paper.pdf").exists()
        parsed = ulid in parsed_ids

        if src:
            has_source += 1
        if pdf:
            has_pdf += 1
        if parsed:
            parsed_count += 1

        status = "[green]OK[/]" if parsed else "[yellow]--[/]"
        table.add_row(
            status,
            ulid,
            "[green]Y[/]" if src else "[red]N[/]",
            "[green]Y[/]" if pdf else "[red]N[/]",
            "[green]Y[/]" if parsed else "[dim]N[/]",
        )

    if omitted > 0:
        table.add_row(
            "...",
            f"[dim]{omitted} more papers omitted[/]",
            "...",
            "...",
            "...",
        )

    full_source = sum(
        1 for d in paper_dirs
        if any((d / n).exists() for n in ["source.tar.gz", "source.tgz", "source.tar", "source.zip"])
    )
    full_pdf = sum(1 for d in paper_dirs if (d / "paper.pdf").exists())
    full_parsed = sum(1 for d in paper_dirs if d.name in parsed_ids)

    console.print(table)
    console.print(
        Panel(
            f"Total: {len(paper_dirs)} | "
            f"Source: {full_source} | "
            f"PDF: {full_pdf} | "
            f"Parsed: {full_parsed}",
            title="Summary",
        )
    )


# ===================================================================
# info: Show detailed info about a paper
# ===================================================================
@app.command()
def info(
    paper_id: str = typer.Argument(help="Paper ID (ULID/arXiv/DOI/slug)"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Show detailed information about a parsed paper."""
    from ..id_resolver import resolve_id
    ulid = resolve_id(paper_id) or paper_id
    data = dbmod.load_parsed(ulid)
    if data is None:
        if json_output:
            print(json.dumps({"error": "Paper not parsed yet", "paper_id": paper_id}))
        else:
            console.print(f"[yellow]Paper not parsed yet.[/] Run: python -m scholar parse {paper_id}")
        raise typer.Exit(1)

    if json_output:
        result = {
            "paper_id": ulid,
            "title": data.get("title", "N/A"),
            "authors": data.get("authors", []),
            "year": data.get("year"),
            "venue": data.get("venue"),
            "abstract": data.get("abstract"),
            "sections": [
                {"heading": s.get("heading", "(untitled)"), "level": s.get("level", 1), "content_length": len(s.get("content", ""))}
                for s in data.get("sections", [])
            ],
            "formulas_count": len(data.get("formulas", [])),
            "citations_count": len(data.get("citations", [])),
        }
        print(json.dumps(result, ensure_ascii=False))
        return

    console.print(Panel(
        f"[bold]{data.get('title', 'N/A')}[/]\n\n"
        f"Authors:   {', '.join(data.get('authors', []))}\n"
        f"Year:      {data.get('year', 'N/A')}\n"
        f"Venue:     {data.get('venue', 'N/A')}\n"
        f"TeX files: {data.get('tex_file_count', 'N/A')}\n"
        f"Main file: {data.get('main_tex_file', 'N/A')}",
        title=f"Paper: {ulid}",
    ))

    abstract = data.get("abstract")
    if abstract:
        console.print(Panel(
            abstract[:500] + ("..." if len(abstract) > 500 else ""),
            title="Abstract",
        ))

    sections = data.get("sections", [])
    if sections:
        table = Table(title=f"Sections ({len(sections)})")
        table.add_column("#", width=4)
        table.add_column("Level", width=6)
        table.add_column("Heading")
        table.add_column("Length", width=8)
        for i, s in enumerate(sections):
            indent = "  " * (s.get("level", 1) - 1)
            table.add_row(
                str(i),
                str(s.get("level", 1)),
                f"{indent}{s.get('heading', '(untitled)')}",
                str(len(s.get("content", ""))),
            )
        console.print(table)

    formulas = data.get("formulas", [])
    if formulas:
        console.print(f"\n[bold]Formulas ({len(formulas)}):[/]")
        for f in formulas[:10]:
            label = f"[{f['label']}]" if f.get("label") else ""
            latex_preview = f["latex"][:80].replace("\n", " ")
            console.print(f"  {f.get('env_type', 'math')}{label}: ${latex_preview}...")
        if len(formulas) > 10:
            console.print(f"  ... and {len(formulas) - 10} more")

    citations = data.get("citations", [])
    if citations:
        console.print(f"\n[bold]Citations ({len(citations)}):[/]")
        console.print(f"  {', '.join(citations[:15])}")
        if len(citations) > 15:
            console.print(f"  ... and {len(citations) - 15} more")


# ===================================================================
# search: Full-text search across parsed papers
# ===================================================================
@app.command()
def search(
    keyword: str = typer.Argument(help="Search keyword"),
    limit: int = typer.Option(20, help="Max results"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Search across all parsed papers (title, abstract, sections)."""
    keyword_lower = keyword.lower()
    results = []

    for paper_id in dbmod.list_parsed():
        data = dbmod.load_parsed(paper_id)
        if not data:
            continue
        score = 0
        if keyword_lower in (data.get("title") or "").lower():
            score += 10
        if keyword_lower in (data.get("abstract") or "").lower():
            score += 5
        for s in data.get("sections", []):
            if keyword_lower in s.get("content", "").lower():
                score += 1
                if score >= 3:
                    break
        if score > 0:
            results.append({
                "paper_id": paper_id,
                "title": data.get("title", "N/A"),
                "year": data.get("year"),
                "venue": data.get("venue"),
                "score": score,
            })
    results.sort(key=lambda x: x["score"], reverse=True)
    results = results[:limit]

    if json_output:
        print(json.dumps(results, ensure_ascii=False))
        return

    if not results:
        console.print(f"No results for [cyan]'{keyword}'[/]")
        return

    table = Table(title=f"Search: '{keyword}' ({len(results)} results)")
    table.add_column("Paper ID", width=28)
    table.add_column("Title")
    table.add_column("Year", width=6)

    for r in results:
        table.add_row(
            r.get("paper_id", r.get("id", "")),
            (r.get("title") or "N/A")[:60],
            str(r.get("year", "")),
        )
    console.print(table)


# ===================================================================
# list-papers: List parsed papers
# ===================================================================
@app.command(name="list-papers")
def list_papers(
    year: Optional[int] = typer.Option(None, help="Filter by year"),
    limit: int = typer.Option(30, help="Max papers to show"),
):
    """List parsed papers with metadata."""
    papers = []
    for paper_id in dbmod.list_parsed():
        data = dbmod.load_parsed(paper_id)
        if data:
            if year and data.get("year") != year:
                continue
            papers.append(data)
    papers.sort(key=lambda x: x.get("year") or 0, reverse=True)

    papers = papers[:limit]

    table = Table(title=f"Parsed Papers ({len(papers)} shown)")
    table.add_column("Paper ID", width=28)
    table.add_column("Title", max_width=50)
    table.add_column("Year", width=6)
    table.add_column("Venue", width=10)
    table.add_column("Sec", width=4)
    table.add_column("Fml", width=4)
    table.add_column("Cit", width=4)

    for p in papers:
        table.add_row(
            p.get("paper_id", ""),
            (p.get("title") or "N/A")[:50],
            str(p.get("year", "")),
            p.get("venue", "") or "",
            str(len(p.get("sections", []))),
            str(len(p.get("formulas", []))),
            str(len(p.get("citations", []))),
        )
    console.print(table)


# ===================================================================
# stats: Show knowledge base statistics
# ===================================================================
@app.command()
def stats(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Show knowledge base statistics."""
    paper_dirs = (
        [d for d in config.PAPERS_DIR.iterdir() if d.is_dir()]
        if config.PAPERS_DIR.exists()
        else []
    )
    parsed_ids = dbmod.list_parsed()

    db_status = "v2 data plane (file-only fallback)"

    years = {}
    venues = {}
    total_formulas = 0
    total_citations = 0
    total_sections = 0
    has_year = 0
    has_authors = 0
    has_abstract = 0
    has_venue = 0

    for pid in parsed_ids:
        data = dbmod.load_parsed(pid)
        if not data:
            continue
        y = data.get("year")
        if y:
            years[y] = years.get(y, 0) + 1
            has_year += 1
        if data.get("authors"):
            has_authors += 1
        if data.get("abstract"):
            has_abstract += 1
        v = data.get("venue")
        if v:
            venues[v] = venues.get(v, 0) + 1
            has_venue += 1
        total_formulas += len(data.get("formulas", []))
        total_citations += len(data.get("citations", []))
        total_sections += len(data.get("sections", []))

    total = len(parsed_ids)

    if json_output:
        result = {
            "paper_folders": len(paper_dirs),
            "parsed": total,
            "sections": total_sections,
            "formulas": total_formulas,
            "citations": total_citations,
            "database": db_status,
            "coverage": {
                "year": round(has_year / total, 2) if total else 0,
                "authors": round(has_authors / total, 2) if total else 0,
                "abstract": round(has_abstract / total, 2) if total else 0,
                "venue": round(has_venue / total, 2) if total else 0,
            },
            "by_year": dict(sorted(years.items())),
            "by_venue": dict(sorted(venues.items(), key=lambda x: x[1], reverse=True)[:10]),
        }
        print(json.dumps(result, ensure_ascii=False))
        return

    if total == 0:
        console.print(Panel(
            f"Paper folders:   {len(paper_dirs)}\n"
            f"Parsed:          0\n"
            f"Database:        {db_status}\n\n"
            f"[dim]No parsed papers yet. Run: scholar parse-all[/]",
            title="Knowledge Base Stats",
        ))
        return
    console.print(Panel(
        f"Paper folders:   {len(paper_dirs)}\n"
        f"Parsed:          {len(parsed_ids)}\n"
        f"Total sections:  {total_sections}\n"
        f"Total formulas:  {total_formulas}\n"
        f"Total citations: {total_citations}\n"
        f"Database:        {db_status}\n"
        f"\n"
        f"[bold]Metadata Coverage:[/]\n"
        f"  Year:      {has_year}/{total} ({has_year*100//total}%)\n"
        f"  Authors:   {has_authors}/{total} ({has_authors*100//total}%)\n"
        f"  Abstract:  {has_abstract}/{total} ({has_abstract*100//total}%)\n"
        f"  Venue:     {has_venue}/{total} ({has_venue*100//total}%)",
        title="Knowledge Base Stats",
    ))

    if years:
        sorted_years = sorted(years.items())
        year_str = ", ".join(f"{y}: {c}" for y, c in sorted_years)
        console.print(f"\n[bold]By Year:[/] {year_str}")

    if venues:
        sorted_venues = sorted(venues.items(), key=lambda x: x[1], reverse=True)
        venue_str = ", ".join(f"{v}: {c}" for v, c in sorted_venues[:10])
        console.print(f"[bold]By Venue:[/] {venue_str}")
