"""Research operations: interests, research-sync, survey, landscape."""

import json
import re
import typer
from typing import Optional
from rich.table import Table
from rich.panel import Panel

from .._shared import app, console
from .. import config


# ===================================================================
# survey: Full research survey pipeline
# ===================================================================
@app.command()
def survey(
    topic: str = typer.Argument(help="Research topic or question"),
    depth: str = typer.Option("standard", "--depth", "-d", help="standard or full"),
    limit: int = typer.Option(20, "--limit", "-n", help="Max papers to include"),
):
    """Full research survey: keyword search -> classify -> timeline -> structured output."""
    from .. import classify as cl

    console.print(f"[cyan]Surveying:[/] {topic}  (depth={depth})\n")

    # 1. Keyword search across parsed JSON
    console.print("[bold]Step 1: Keyword Search[/]")
    seen_ids: list[str] = []
    try:
        kw_results: list[str] = []
        topic_lower = topic.lower()
        for ppath in config.PARSED_DIR.glob("*.json"):
            try:
                pdata = json.loads(ppath.read_text(encoding="utf-8"))
                title = (pdata.get("title") or "").lower()
                abstract = (pdata.get("abstract") or "").lower()
                if topic_lower in title or topic_lower in abstract:
                    kw_results.append(ppath.stem)
                    if len(kw_results) >= limit:
                        break
            except Exception:
                continue
        for pid in kw_results:
            if pid and pid not in seen_ids:
                seen_ids.append(pid)
    except Exception as e:
        console.print(f"  [yellow]Keyword search failed: {e}[/]")

    # 2. Enrich with metadata
    console.print("\n[bold]Step 2: Enrich & Classify[/]")
    papers_data: list[dict] = []
    for pid in seen_ids[:limit]:
        ppath = config.PARSED_DIR / f"{pid}.json"
        if ppath.exists():
            try:
                pdata = json.loads(ppath.read_text(encoding="utf-8"))
                pdata["ulid"] = pid
                papers_data.append(pdata)
            except Exception:
                pass
    console.print(f"  Loaded {len(papers_data)} paper records")

    tag_summary: dict[str, int] = {}
    for p in papers_data[:10]:
        tags = p.get("tags", {})
        for d in tags.get("domains", []):
            tag_summary[d] = tag_summary.get(d, 0) + 1

    # 3. Timeline
    console.print("\n[bold]Step 3: Timeline & Summary[/]")
    by_year: dict[int, list] = {}
    for p in papers_data:
        y = p.get("year", 0)
        if y:
            by_year.setdefault(y, []).append(p)

    # Output
    out_dir = config.project_drafts_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_topic = re.sub(r"[^\w\-]", "_", topic)[:50]
    out_path = out_dir / f"survey_{safe_topic}.md"

    lines = [f"# Research Survey: {topic}\n"]
    lines.append(f"**Papers found:** {len(papers_data)}  ")
    lines.append(
        f"**Domains:** {', '.join(f'{k}({v})' for k, v in sorted(tag_summary.items(), key=lambda x: -x[1]))}\n"
    )

    if by_year:
        lines.append("## Timeline\n")
        for y in sorted(by_year.keys()):
            titles = [
                f"- {(p.get('title') or 'Untitled')[:80]}" for p in by_year[y][:5]
            ]
            lines.append(
                f"### {y} ({len(by_year[y])} papers)\n" + "\n".join(titles) + "\n"
            )

    lines.append("## Papers\n")
    for i, p in enumerate(papers_data, 1):
        title = (p.get("title") or "Untitled")[:100]
        year = p.get("year", "?")
        authors = ", ".join((p.get("authors") or [])[:3])
        venue = p.get("venue", "")
        quality = p.get("quality", {})
        grade = quality.get("grade", "")
        lines.append(f"{i}. **{title}** ({year})")
        lines.append(f"   Authors: {authors}")
        if venue or grade:
            lines.append(f"   {venue}{' | Grade: ' + grade if grade else ''}")
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    console.print(f"\n[green]Survey saved to {out_path}[/]")


# ===================================================================
# landscape: Field landscape analysis
# ===================================================================
@app.command()
def landscape(
    topic: str = typer.Argument(
        help="Research field or domain (e.g., NLP, RL, Safety)"
    ),
):
    """Field landscape analysis: classify tags -> year distribution -> key papers."""
    from .. import classify as cl

    console.print(f"[cyan]Landscape Analysis:[/] {topic}\n")

    # 1. Tag matching
    console.print("[bold]Step 1: Domain Tag Matching[/]")
    all_tags = cl.list_all_tags()
    domain_papers: list[dict] = []
    matched_domain = None
    for d_name, d_count in all_tags.get("domains", {}).items():
        if d_name.lower() == topic.lower() or topic.lower() in d_name.lower():
            matched_domain = d_name
            console.print(f"  Matched domain: {matched_domain} ({d_count} papers)")
            break

    if not matched_domain:
        for sd_name, sd_count in all_tags.get("sub_directions", {}).items():
            if topic.lower() in sd_name.lower():
                matched_domain = sd_name
                console.print(
                    f"  Matched sub-direction: {matched_domain} ({sd_count} papers)"
                )
                break

    # 2. Scan papers matching the topic
    console.print("\n[bold]Step 2: Paper Collection[/]")
    for ppath in sorted(config.PARSED_DIR.glob("*.json")):
        try:
            pdata = json.loads(ppath.read_text(encoding="utf-8"))
            tags = pdata.get("tags", {})
            domains = [d.lower() for d in tags.get("domains", [])]
            subs = [s.lower() for s in tags.get("sub_directions", [])]
            all_tag_str = " ".join(domains + subs).lower()
            if topic.lower() in all_tag_str:
                pdata["ulid"] = ppath.stem
                domain_papers.append(pdata)
        except Exception:
            pass
    console.print(f"  {len(domain_papers)} papers in landscape")

    # 3. Year distribution
    console.print("\n[bold]Step 3: Year Distribution[/]")
    year_dist: dict[int, int] = {}
    for p in domain_papers:
        y = p.get("year", 0)
        if y:
            year_dist[y] = year_dist.get(y, 0) + 1
    for y in sorted(year_dist.keys()):
        bar = "█" * min(year_dist[y], 40)
        console.print(f"  {y}: {bar} {year_dist[y]}")

    # 4. Quality distribution
    console.print("\n[bold]Step 4: Quality Distribution[/]")
    grades: dict[str, int] = {}
    for p in domain_papers:
        g = p.get("quality", {}).get("grade", "N/A")
        grades[g] = grades.get(g, 0) + 1
    for g in ["A", "B", "C", "D", "F", "N/A"]:
        if g in grades:
            console.print(f"  {g}: {grades[g]} papers")

    # Save report
    out_dir = config.project_drafts_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_topic = re.sub(r"[^\w\-]", "_", topic)[:50]
    out_path = out_dir / f"landscape_{safe_topic}.md"

    lines = [f"# Landscape Analysis: {topic}\n"]
    lines.append(f"**Total papers:** {len(domain_papers)}")
    lines.append(f"**Matched:** {matched_domain or topic}\n")

    lines.append("## Year Distribution\n")
    for y in sorted(year_dist.keys()):
        lines.append(f"- {y}: {year_dist[y]} papers")

    lines.append("\n## Quality Distribution\n")
    for g in ["A", "B", "C", "D", "F", "N/A"]:
        if g in grades:
            lines.append(f"- Grade {g}: {grades[g]}")

    lines.append("\n## Key Papers\n")
    sorted_papers = sorted(
        domain_papers, key=lambda p: p.get("quality", {}).get("total", 0), reverse=True
    )
    for i, p in enumerate(sorted_papers[:20], 1):
        title = (p.get("title") or "Untitled")[:80]
        year = p.get("year", "?")
        grade = p.get("quality", {}).get("grade", "?")
        lines.append(f"{i}. **{title}** ({year}) -- Grade {grade}")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    console.print(f"\n[green]Landscape report saved to {out_path}[/]")


# ===================================================================
# Research Loop
# ===================================================================
@app.command()
def interests(
    action: str = typer.Argument(
        "list", help="Action: list, add, remove, logs, mark-analyzed"
    ),
    keywords: str = typer.Option(
        "", "--keywords", help="Comma-separated keywords (for add)"
    ),
    category: str = typer.Option("general", "--category", help="Interest category"),
    max_results: int = typer.Option(
        10, "--max", help="Max results per search (for add)"
    ),
    week: str = typer.Option(
        "", "--week", help="Week ID like 2026-W24 (for mark-analyzed)"
    ),
    interests_found: int = typer.Option(
        0, "--found", help="Number of interests found (for mark-analyzed)"
    ),
    project: str = typer.Option(
        "",
        "--project",
        help="Project name (for mark-analyzed, empty = current project)",
    ),
):
    """Manage research interests and conversation log analysis."""
    from .. import research_loop as rl

    if action == "list":
        data = rl.load_interests()
        if not data["interests"]:
            console.print(
                '[yellow]No interests yet. Use: interests add --keywords "..." --category "..."[/]'
            )
            return
        rows = []
        for i, item in enumerate(data["interests"], 1):
            rows.append(
                f"{i}. [bold]{item['category']}[/bold]\n"
                f"   Keywords: {item['keywords']}\n"
                f"   Searches: {item.get('search_count', 0)} | Last: {item.get('last_searched', 'never')}"
            )
        console.print(
            Panel(
                "\n".join(rows),
                title=f"Research Interests ({len(data['interests'])} directions)",
            )
        )

    elif action == "add":
        if not keywords:
            console.print('[red]Please provide --keywords "..."[/]')
            return
        data = rl.add_interest(keywords, category, max_results)
        console.print(
            f"[green]OK[/green] Added direction [bold]{category}[/bold]: {keywords}"
        )

    elif action == "remove":
        _, removed = rl.remove_interest(category)
        if removed:
            console.print(
                f"[green]OK[/green] Removed direction [bold]{category}[/bold]"
            )
        else:
            console.print(
                f"[yellow]!![/yellow] Direction [bold]{category}[/bold] not found"
            )

    elif action == "logs":
        all_logs = rl.get_unanalyzed_logs()
        if not all_logs:
            console.print("[yellow]No unanalyzed conversation logs[/]")
            return
        for proj_name, (path, entries) in all_logs.items():
            week_id = path.stem.replace("week-", "")
            display_lines = []
            for e in entries[:40]:
                ts_str = e.get("ts", "?")
                role = e.get("role", "")
                text = e.get("text", "")
                if role == "user":
                    display_lines.append(
                        f"  [{ts_str}] [bold cyan]Q:[/bold cyan] {text[:120]}"
                    )
                elif role == "assistant":
                    display_lines.append(
                        f"  [{ts_str}] [bold green]A:[/bold green] {text[:120]}"
                    )
                else:
                    display_lines.append(f"  [{ts_str}] {text[:120]}")
            total = len(entries)
            user_count = sum(1 for e in entries if e.get("role") == "user")
            asst_count = sum(1 for e in entries if e.get("role") == "assistant")
            old_count = total - user_count - asst_count
            proj_label = proj_name if proj_name != "_legacy" else "(legacy)"
            summary = (
                f"Project: [bold magenta]{proj_label}[/bold magenta]\n"
                f"Week: [bold]{week_id}[/bold]\n"
                f"Entries: {total} ({user_count} user, {asst_count} assistant"
                + (f", {old_count} legacy" if old_count > 0 else "")
                + ")\n\n"
                + "\n".join(display_lines)
                + (f"\n  ... and {total - 40} more" if total > 40 else "")
            )
            console.print(Panel(summary, title=f"Unanalyzed Logs -- {proj_label}"))

    elif action == "mark-analyzed":
        if not week:
            console.print("[red]Please provide --week 2026-W24[/]")
            return
        if project:
            proj_dir = config.LOGS_DIR / config.sanitize_project_name(project)
        else:
            proj_dir = config.LOGS_DIR
        week_file = proj_dir / f"week-{week}.jsonl"
        if not week_file.exists():
            console.print(f"[red]x[/red] Week log not found: {week_file}")
            console.print("    Generate logs through conversation first")
            return
        entry_count = sum(
            1
            for line in week_file.read_text(encoding="utf-8").strip().splitlines()
            if line.strip()
        )
        if entry_count == 0:
            console.print(f"[red]x[/red] {week} log is empty, refusing to mark")
            return
        rl.mark_week_analyzed(week, interests_found, entry_count, project=project)
        proj_label = f" [{project}]" if project else ""
        console.print(
            f"[green]OK[/green] Marked {week}{proj_label} as analyzed ({entry_count} entries)"
        )

    else:
        console.print(
            f"[red]Unknown action: {action}. Available: list, add, remove, logs, mark-analyzed[/]"
        )


@app.command(name="research-sync")
def research_sync(
    category: str = typer.Option(
        "", "--category", help="Sync specific direction (empty = all)"
    ),
    max_results: int = typer.Option(10, "--max", help="Max papers per keyword"),
):
    """Search arXiv for research directions and ingest papers."""
    from .. import research_loop as rl

    console.print("[cyan]Starting research sync...[/]")

    if category:
        result = rl.sync_direction(category, max_results=max_results)
        console.print(
            Panel(
                f"Direction: [bold]{result['category']}[/bold]\n"
                f"Downloaded: {result['downloaded']}\n"
                f"Ingested:   {result['ingested']}\n"
                f"Errors:     [red]{len(result['errors'])}[/]",
                title="Research Sync Result",
            )
        )
        for p in result.get("papers", []):
            console.print(
                f"  - {p.get('title', '?')[:60]} ({p.get('year', '?')}) -> {p.get('ulid', '?')[:8]}"
            )
    else:
        result = rl.sync_all_directions(max_results=max_results)
        if "message" in result:
            console.print(f"[yellow]{result['message']}[/]")
            return
        console.print(
            Panel(
                f"Directions: {result['total_categories']}\n"
                f"Total papers: {result['total_papers']}",
                title="Research Sync -- All Directions",
            )
        )
        for r in result["results"]:
            status = "[green]OK[/green]" if not r["errors"] else "[red]x[/red]"
            console.print(
                f"  {status} {r['category']}: {r['downloaded']} downloaded, {r['ingested']} ingested"
            )
