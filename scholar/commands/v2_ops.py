"""Operational commands for the XML-first Scholar v2 data plane."""

from __future__ import annotations

import json
from pathlib import Path
from typing import NoReturn

import typer

from .._shared import app, console
from ..config import EMBEDDING_MODEL, EMBEDDING_PROVIDER, V2_SERVING_CHANNEL
from ..v2.builders import GraphBuilder, SnapshotBuilder, VectorBuilder
from ..v2.database import V2Database
from ..v2.embeddings import configured_provider
from ..v2.importer import CorpusImporter
from ..v2.latexml_ingest import (
    CorpusIngester,
    IngestSource,
    LaTeXMLRunner,
    discover_local_sources,
    fetch_arxiv_source,
    safe_paper_id,
)
from ..v2.models import ScholarError

v2_app = typer.Typer(help="XML-first corpus, projection, and snapshot operations.")
app.add_typer(v2_app, name="v2")


def _print(payload: dict) -> None:
    console.print_json(json.dumps(payload, ensure_ascii=False, default=str))


def _error(error: Exception) -> NoReturn:
    if isinstance(error, ScholarError):
        console.print(f"[red]{error.code}: {error.message}[/]")
    else:
        console.print(f"[red]{type(error).__name__}: {error}[/]")
    raise typer.Exit(1)


@v2_app.command("init")
def initialize_schema() -> None:
    """Install or validate the additive v2 PostgreSQL schema."""
    database = V2Database()
    try:
        database.initialize()
        _print({"status": "ready", "schema": "scholar-v2-001"})
    except Exception as error:
        _error(error)
    finally:
        database.close()


@v2_app.command("import")
def import_corpus(
    source: Path = typer.Argument(..., exists=True, file_okay=False, resolve_path=True),
    release_id: str = typer.Option(..., "--release-id"),
    name: str | None = typer.Option(None, "--name"),
    artifact_root: Path | None = typer.Option(None, "--artifact-root"),
) -> None:
    """Verify and import one immutable XML corpus release."""
    database = V2Database()
    try:
        database.initialize()
        result = CorpusImporter(database).import_release(
            source, release_id, name, artifact_root
        )
        _print(result)
    except Exception as error:
        _error(error)
    finally:
        database.close()


@v2_app.command("ingest")
def ingest_corpus(
    source: Path | None = typer.Argument(
        None,
        exists=True,
        file_okay=False,
        resolve_path=True,
        help="Directory of per-paper LaTeX source dirs/archives",
    ),
    arxiv: list[str] = typer.Option(
        [], "--arxiv", help="arXiv IDs to download (repeatable)"
    ),
    arxiv_list: Path | None = typer.Option(
        None,
        "--arxiv-list",
        exists=True,
        dir_okay=False,
        help="File with one arXiv ID per line",
    ),
    out_dir: Path = typer.Option(..., "--out"),
    image: str = typer.Option("latexml/ar5ivist:2512.17", "--image"),
    timeout: int = typer.Option(600, "--timeout"),
    do_import: bool = typer.Option(False, "--import"),
    release_id: str | None = typer.Option(None, "--release-id"),
    name: str | None = typer.Option(None, "--name"),
    artifact_root: Path | None = typer.Option(None, "--artifact-root"),
) -> None:
    """Convert LaTeX sources into an immutable XML corpus release."""
    if do_import and not release_id:
        _error(ScholarError("INVALID_ARGUMENT", "--import requires --release-id"))
    ids = list(arxiv)
    if arxiv_list is not None:
        ids.extend(
            line.strip()
            for line in arxiv_list.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
    if source is None and not ids:
        _error(ScholarError("INVALID_ARGUMENT", "at least one input is required"))

    database = None
    try:
        sources = discover_local_sources(source) if source is not None else []
        for arxiv_id in ids:
            paper_id = safe_paper_id(arxiv_id)
            paper_dir = out_dir / paper_id
            tex_dir = fetch_arxiv_source(arxiv_id, paper_dir)
            sources.append(
                IngestSource(
                    paper_id=paper_id,
                    tex_dir=tex_dir,
                    arxiv_id=arxiv_id,
                )
            )
        result = CorpusIngester().ingest(
            sources,
            out_dir,
            LaTeXMLRunner(image=image, timeout=timeout),
        )
        if do_import:
            database = V2Database()
            database.initialize()
            result["import"] = CorpusImporter(database).import_release(
                out_dir, release_id, name, artifact_root
            )
        _print(result)
    except Exception as error:
        _error(error)
    finally:
        if database is not None:
            database.close()


@v2_app.command("build-graph")
def build_graph(
    release_id: str = typer.Option(..., "--release-id"),
    relational_build_id: str = typer.Option(..., "--relational-build"),
) -> None:
    """Build a sealed evidence-bearing citation and authorship graph."""
    database = V2Database()
    try:
        database.initialize()
        _print(GraphBuilder(database).build(release_id, relational_build_id))
    except Exception as error:
        _error(error)
    finally:
        database.close()


@v2_app.command("build-vectors")
def build_vectors(
    release_id: str = typer.Option(..., "--release-id"),
    relational_build_id: str = typer.Option(..., "--relational-build"),
    model_version: str = typer.Option(..., "--model-version"),
) -> None:
    """Embed all chunks with the configured provider into a sealed build."""
    provider = configured_provider()
    if provider is None:
        _error(
            ScholarError("VECTOR_UNAVAILABLE", "embedding provider is not configured")
        )
    database = V2Database()
    try:
        database.initialize()
        result = VectorBuilder(database, provider).build(
            release_id=release_id,
            relational_build_id=relational_build_id,
            provider=EMBEDDING_PROVIDER,
            model_name=EMBEDDING_MODEL,
            model_version=model_version,
        )
        _print(result)
    except Exception as error:
        _error(error)
    finally:
        database.close()


@v2_app.command("create-snapshot")
def create_snapshot(
    release_id: str = typer.Option(..., "--release-id"),
    relational_build_id: str = typer.Option(..., "--relational-build"),
    graph_build_id: str | None = typer.Option(None, "--graph-build"),
    vector_build_id: str | None = typer.Option(None, "--vector-build"),
    semantic_build_id: str | None = typer.Option(None, "--semantic-build"),
) -> None:
    """Validate compatible sealed builds and create a ready snapshot."""
    database = V2Database()
    try:
        database.initialize()
        _print(
            SnapshotBuilder(database).create(
                release_id,
                relational_build_id,
                graph_build_id,
                vector_build_id,
                semantic_build_id,
            )
        )
    except Exception as error:
        _error(error)
    finally:
        database.close()


@v2_app.command("activate")
def activate_snapshot(
    snapshot_id: str = typer.Argument(...),
    channel: str = typer.Option(V2_SERVING_CHANNEL, "--channel"),
    expected_revision: int | None = typer.Option(None, "--expected-revision"),
) -> None:
    """Atomically move a serving channel to a ready snapshot."""
    database = V2Database()
    try:
        revision = database.activate_snapshot(snapshot_id, channel, expected_revision)
        _print(
            {
                "channel": channel,
                "snapshot_id": snapshot_id,
                "revision": revision,
            }
        )
    except Exception as error:
        _error(error)
    finally:
        database.close()


@v2_app.command("status")
def status(
    channel: str = typer.Option(V2_SERVING_CHANNEL, "--channel"),
) -> None:
    """Show active snapshot and projection counts for one channel."""
    database = V2Database()
    try:
        snapshot = database.active_snapshot(channel)
        with database.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                      (SELECT count(*) FROM scholar_v2_work_versions
                       WHERE release_id = %s),
                      (SELECT count(*) FROM scholar_v2_sections
                       WHERE build_id = %s),
                      (SELECT count(*) FROM scholar_v2_content_nodes
                       WHERE build_id = %s),
                      (SELECT count(*) FROM scholar_v2_chunks
                       WHERE build_id = %s)
                    """,
                    (
                        snapshot["release_id"],
                        snapshot["relational_build_id"],
                        snapshot["relational_build_id"],
                        snapshot["relational_build_id"],
                    ),
                )
                work_count, section_count, node_count, chunk_count = cursor.fetchone()
                graph_nodes = graph_edges = embeddings = 0
                if snapshot["graph_build_id"]:
                    cursor.execute(
                        """
                        SELECT
                          (SELECT count(*) FROM scholar_v2_graph_nodes
                           WHERE build_id = %s),
                          (SELECT count(*) FROM scholar_v2_graph_edges
                           WHERE build_id = %s)
                        """,
                        (
                            snapshot["graph_build_id"],
                            snapshot["graph_build_id"],
                        ),
                    )
                    graph_nodes, graph_edges = cursor.fetchone()
                if snapshot["vector_build_id"]:
                    cursor.execute(
                        """
                        SELECT count(*) FROM scholar_v2_chunk_embeddings
                        WHERE build_id = %s
                        """,
                        (snapshot["vector_build_id"],),
                    )
                    embeddings = cursor.fetchone()[0]
                counts = {
                    "works": work_count,
                    "sections": section_count,
                    "content_nodes": node_count,
                    "chunks": chunk_count,
                    "graph_nodes": graph_nodes,
                    "graph_edges": graph_edges,
                    "embeddings": embeddings,
                }
        _print({"channel": channel, "snapshot": snapshot, "counts": counts})
    except Exception as error:
        _error(error)
    finally:
        database.close()
