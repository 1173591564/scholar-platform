"""
Unit Tests — config.py

Tests configuration paths, environment variable loading, arXiv request utility.
"""

import pytest


class TestConfigPaths:
    """Verify all config paths are correctly resolved."""

    def test_project_root_is_directory(self, project_root):
        assert project_root.is_dir()

    def test_papers_dir_under_project(self, project_root):
        from scholar import config

        assert config.PAPERS_DIR == project_root / "data" / "papers"

    def test_output_dirs_exist(self):
        from scholar import config

        for d in [
            config.PARSED_DIR,
            config.NOTES_DIR,
            config.DRAFTS_DIR,
            config.BIB_DIR,
            config.EXPERIMENTS_DIR,
            config.LOGS_DIR,
        ]:
            assert d.is_dir(), f"{d} should exist"

    def test_interests_file_path(self):
        from scholar import config

        assert config.INTERESTS_FILE.name == "research-interests.json"
        assert config.INTERESTS_FILE.parent == config.OUTPUT_DIR

    def test_pg_defaults(self):
        from scholar import config

        assert config.PG_PORT == 5433
        assert config.PG_NAME == "scholar"

    def test_embedding_defaults(self):
        from scholar import config

        assert config.EMBEDDING_PROVIDER == "zhipu"
        assert config.EMBEDDING_DIM == 1024

    def test_source_tree_detection(self, tmp_path):
        """Source tree detection should work with pyproject.toml and scholar_mcp."""
        from scholar.config import _is_source_tree

        (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n")
        (tmp_path / "scholar_mcp").mkdir()

        assert _is_source_tree(tmp_path)


class TestArxivRequest:
    """Test arXiv API request utility (mocked network)."""

    def test_arxiv_request_builds_url(self, monkeypatch):
        """Verify the URL is built correctly."""
        captured_urls = []

        class MockResponse:
            def read(self):
                return b"<xml>test</xml>"

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        class MockOpener:
            def open(self, req, timeout=None):
                captured_urls.append(req.full_url)
                return MockResponse()

        import urllib.request

        monkeypatch.setattr(urllib.request, "build_opener", lambda *a: MockOpener())

        from scholar.config import arxiv_request

        result = arxiv_request("all:transformer", max_results=5)

        assert "<xml>test</xml>" in result
        assert "all%3Atransformer" in captured_urls[0]
        assert "max_results=5" in captured_urls[0]

    def test_arxiv_request_retry_on_failure(self, monkeypatch):
        """Verify retries happen on network error."""
        call_count = [0]

        class MockOpener:
            def open(self, req, timeout=None):
                call_count[0] += 1
                if call_count[0] < 3:
                    raise ConnectionError("network down")

                class R:
                    def read(self):
                        return b"<ok/>"

                    def __enter__(self):
                        return self

                    def __exit__(self, *a):
                        pass

                return R()

        import urllib.request

        monkeypatch.setattr(urllib.request, "build_opener", lambda *a: MockOpener())
        monkeypatch.setenv("SCHOLAR_ARXIV_RETRIES", "3")
        monkeypatch.setenv("SCHOLAR_ARXIV_TIMEOUT", "1")

        from scholar.config import arxiv_request

        result = arxiv_request("ti:test", max_results=1)
        assert "<ok/>" in result
        assert call_count[0] == 3

    def test_arxiv_request_all_retries_fail(self, monkeypatch):
        """Verify exception when all retries fail."""

        class MockOpener:
            def open(self, req, timeout=None):
                raise ConnectionError("always fails")

        import urllib.request

        monkeypatch.setattr(urllib.request, "build_opener", lambda *a: MockOpener())
        monkeypatch.setenv("SCHOLAR_ARXIV_RETRIES", "2")
        monkeypatch.setenv("SCHOLAR_ARXIV_TIMEOUT", "1")

        from scholar.config import arxiv_request

        with pytest.raises(Exception, match="已重试"):
            arxiv_request("ti:test", max_results=1)
