import csv
import hashlib
import io
import json
import subprocess
import tarfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from scholar.v2.latexml_ingest import (
    CorpusIngester,
    IngestSource,
    LaTeXMLRunner,
    discover_local_sources,
    fetch_arxiv_source,
    find_main_tex,
    grade_quality,
)
from scholar.v2.models import ScholarError


def _fixture_xml(paragraphs: int = 10, include_section: bool = True) -> str:
    body = "".join(f"<para>Paragraph {index}.</para>" for index in range(paragraphs))
    section = f"<section><title>Method</title>{body}</section>" if include_section else body
    return f"""\
<document xmlns="http://dlmf.nist.gov/LaTeXML">
  <title>Evidence First Systems</title>
  <creator><personname>Author</personname></creator>
  <abstract>A structured abstract.</abstract>
  {section}
</document>
"""


def test_find_main_tex_prefers_main_among_document_candidates(tmp_path):
    (tmp_path / "decoy.tex").write_text(r"\documentclass{article}", encoding="utf-8")
    (tmp_path / "paper.tex").write_text(
        r"\documentclass{article}\begin{document}paper\end{document}",
        encoding="utf-8",
    )
    (tmp_path / "main.tex").write_text(
        r"\documentclass{article}\begin{document}main\end{document}",
        encoding="utf-8",
    )

    assert find_main_tex(tmp_path) == "main.tex"


def test_grade_quality_structural_and_log_gates(tmp_path):
    xml_path = tmp_path / "paper.xml"
    xml_path.write_text(_fixture_xml(), encoding="utf-8")

    tier, reasons, stats = grade_quality(xml_path, "", "full")
    assert tier == "clean"
    assert reasons == []
    assert stats["paragraph_count"] == 10

    tier, reasons, _ = grade_quality(xml_path, "Fatal: broken macro", "full")
    assert tier == "degraded"
    assert reasons == ["fatal"]

    xml_path.write_text(_fixture_xml(include_section=False), encoding="utf-8")
    tier, reasons, _ = grade_quality(xml_path, "", "full")
    assert tier == "failed"
    assert reasons == ["no_sections"]

    xml_path.write_text("<document>", encoding="utf-8")
    tier, reasons, _ = grade_quality(xml_path, "", "full")
    assert tier == "failed"
    assert reasons == ["invalid_xml"]


def test_runner_builds_docker_argv_and_retries_noparse(tmp_path):
    source = tmp_path / "src"
    out = tmp_path / "out"
    source.mkdir()
    out.mkdir()
    calls = []

    def fake(command):
        calls.append(command)
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(command, 1)
        (out / "paper.xml").write_text(_fixture_xml(), encoding="utf-8")
        (out / "paper.log").write_text("", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    result = LaTeXMLRunner(timeout=7, runner=fake).run(source, "main.tex", out)

    assert result.returncode == 0
    assert "--network=none" in calls[0]
    assert "latexmlc" in calls[0]
    assert "--includestyles" in calls[0]
    assert "/source/main.tex" in calls[0]
    assert "--noparse" in calls[1]


def test_corpus_ingester_writes_manifest_and_failed_csv(tmp_path):
    source_root = tmp_path / "sources"
    source_root.mkdir()
    for paper_id in ("good", "bad"):
        paper_dir = source_root / paper_id
        paper_dir.mkdir()
        (paper_dir / "main.tex").write_text(
            r"\documentclass{article}\begin{document}\end{document}",
            encoding="utf-8",
        )
    out = tmp_path / "release"

    class FakeRunner:
        image = "fake"
        last_math_mode = "full"

        def run(self, tex_dir, main_tex, out_dir):
            if tex_dir.name == "good":
                (out_dir / "paper.xml").write_text(_fixture_xml(), encoding="utf-8")
                (out_dir / "paper.log").write_text("", encoding="utf-8")
            return subprocess.CompletedProcess([], 0, "", "")

    result = CorpusIngester().ingest(
        [
            IngestSource("good", source_root / "good"),
            IngestSource("bad", source_root / "bad"),
        ],
        out,
        FakeRunner(),
    )

    assert result == {
        "out_dir": str(out),
        "total": 2,
        "clean": 1,
        "degraded": 0,
        "failed": 1,
        "manifest": str(out / "manifest.csv"),
    }
    with (out / "manifest.csv").open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == [
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
        row = next(reader)
    raw = (out / "good" / "latexml" / "paper.xml").read_bytes()
    canonical = ET.canonicalize(xml_data=raw.decode("utf-8")).encode("utf-8")
    assert int(row["size_bytes"]) == len(raw)
    assert row["sha256"] == hashlib.sha256(raw).hexdigest()
    assert row["canonical_sha256"] == hashlib.sha256(canonical).hexdigest()
    with (out / "failed.csv").open(encoding="utf-8") as handle:
        assert "bad,exception:ScholarError" in handle.read()


def test_discover_sources_extracts_archives_and_rejects_traversal(tmp_path):
    safe = tmp_path / "hep-th-9901001"
    safe.mkdir()
    archive = safe / "source.tar.gz"
    with tarfile.open(archive, "w:gz") as handle:
        content = b"\\documentclass{article}\\begin{document}x\\end{document}"
        info = tarfile.TarInfo("main.tex")
        info.size = len(content)
        handle.addfile(info, __import__("io").BytesIO(content))
    sources = discover_local_sources(tmp_path)
    assert sources[0].paper_id == "hep-th-9901001"
    assert (sources[0].tex_dir / "main.tex").is_file()

    unsafe = tmp_path / "unsafe"
    unsafe.mkdir()
    bad_archive = unsafe / "source.tar.gz"
    with tarfile.open(bad_archive, "w:gz") as handle:
        info = tarfile.TarInfo("../evil")
        info.size = 1
        handle.addfile(info, __import__("io").BytesIO(b"x"))
    with pytest.raises(ScholarError, match="unsafe archive member"):
        discover_local_sources(tmp_path)


def test_fetch_arxiv_source_and_ingest_keep_metadata(tmp_path, monkeypatch):
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as handle:
        content = b"\\documentclass{article}\\begin{document}x\\end{document}"
        info = tarfile.TarInfo("main.tex")
        info.size = len(content)
        handle.addfile(info, io.BytesIO(content))

    class FakeResponse:
        def __init__(self, content):
            self.content = content

        def raise_for_status(self):
            return None

    class FakeClient:
        def get(self, url, timeout):
            if "e-print" in url:
                return FakeResponse(archive.getvalue())
            return FakeResponse(
                b"""\
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Fetched Paper</title>
    <author><name>Alice Example</name></author>
    <published>2025-01-01T00:00:00Z</published>
  </entry>
</feed>
"""
            )

    monkeypatch.setattr("scholar.v2.latexml_ingest.time.sleep", lambda _: None)
    dest = tmp_path / "paper"
    source_dir = fetch_arxiv_source("2501.12345", dest, FakeClient())

    assert (source_dir / "main.tex").is_file()
    assert (dest / "source.tar.gz").is_file()
    metadata = json.loads((dest / "meta.json").read_text(encoding="utf-8"))
    assert metadata["title"] == "Fetched Paper"
    assert metadata["authors"] == ["Alice Example"]

    class FakeRunner:
        image = "fake"
        last_math_mode = "full"

        def run(self, tex_dir, main_tex, out_dir):
            (out_dir / "paper.xml").write_text(_fixture_xml(), encoding="utf-8")
            (out_dir / "paper.log").write_text("", encoding="utf-8")
            return subprocess.CompletedProcess([], 0, "", "")

    CorpusIngester().ingest(
        [IngestSource("paper", source_dir)],
        tmp_path,
        FakeRunner(),
    )
    assert (dest / "meta.json").is_file()
