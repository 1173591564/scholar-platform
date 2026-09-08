"""
Unit Tests — workspace initialization + WORKSPACE_DIR.

Tests init-workspace command output directory creation
and WORKSPACE_DIR path resolution in dev mode.
"""


class TestWorkspaceDir:
    """Test WORKSPACE_DIR path resolution."""

    def test_default_equals_scholar_home_in_dev(self):
        """In dev mode, WORKSPACE_DIR defaults to SCHOLAR_HOME."""
        from scholar import config
        if not config.IS_FROZEN:
            assert config.WORKSPACE_DIR == config.SCHOLAR_HOME

    def test_env_var_overrides_workspace_dir(self, monkeypatch, tmp_path):
        """SCHOLAR_WORKSPACE env var overrides WORKSPACE_DIR."""
        import importlib
        monkeypatch.setenv("SCHOLAR_WORKSPACE", str(tmp_path))

        # Need to reload config to pick up env change
        from scholar import config as cfg_module
        importlib.reload(cfg_module)

        assert cfg_module.WORKSPACE_DIR == tmp_path
        assert cfg_module.DRAFTS_DIR == tmp_path / "output" / "drafts"
        assert cfg_module.LOGS_DIR == tmp_path / "output" / "logs"

    def test_project_logs_dir_uses_workspace(self, monkeypatch, tmp_path):
        """project_logs_dir() derives from WORKSPACE_DIR."""
        import importlib
        monkeypatch.setenv("SCHOLAR_WORKSPACE", str(tmp_path))

        from scholar import config as cfg_module
        importlib.reload(cfg_module)

        log_dir = cfg_module.project_logs_dir()
        assert str(log_dir).startswith(str(tmp_path))


class TestInitWorkspace:
    """Test config.init_workspace() behavior."""

    def test_creates_output_dirs(self, monkeypatch, tmp_path):
        """init_workspace() creates output dirs under WORKSPACE_DIR."""
        import importlib
        monkeypatch.setenv("SCHOLAR_WORKSPACE", str(tmp_path))

        from scholar import config as cfg_module
        importlib.reload(cfg_module)

        result = cfg_module.init_workspace()

        # Verify dirs exist (may be created by import-time mkdir or init_workspace)
        assert (tmp_path / "output" / "drafts").is_dir()
        assert (tmp_path / "output" / "notes").is_dir()
        assert (tmp_path / "output" / "logs").is_dir()
        assert result["workspace"] == str(tmp_path)
        assert "already_exists" in result

    def test_idempotent_second_call(self, monkeypatch, tmp_path):
        """Second call is idempotent — already_exists=True."""
        import importlib
        monkeypatch.setenv("SCHOLAR_WORKSPACE", str(tmp_path))

        from scholar import config as cfg_module
        importlib.reload(cfg_module)

        result1 = cfg_module.init_workspace()
        result2 = cfg_module.init_workspace()

        assert result2["already_exists"]  # all dirs already there

    def test_returns_parsed_dir_paths(self, monkeypatch, tmp_path):
        """init_workspace() returns parsed_dir, drafts_dir, etc."""
        import importlib
        monkeypatch.setenv("SCHOLAR_WORKSPACE", str(tmp_path))

        from scholar import config as cfg_module
        importlib.reload(cfg_module)

        result = cfg_module.init_workspace()
        assert "parsed_dir" in result
        assert "drafts_dir" in result
        assert "notes_dir" in result
        assert "logs_dir" in result
        assert "scholar_home" in result


class TestSanitizeProjectName:
    """Test sanitize_project_name helper."""

    def test_spaces_to_underscore(self):
        from scholar.config import sanitize_project_name
        assert sanitize_project_name("My Project") == "My_Project"

    def test_special_chars_removed(self):
        from scholar.config import sanitize_project_name
        result = sanitize_project_name("test!@#project")
        assert "!" not in result
        assert "@" not in result

    def test_trim_empty(self):
        from scholar.config import sanitize_project_name
        assert sanitize_project_name("___") == "default"
