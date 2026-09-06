"""test_dsh_ops.py — init-dsh 的 patch 段管理与 rules 分发逻辑。"""

import io
import json
from pathlib import Path

import typer
import yaml
from click import unstyle
from typer.testing import CliRunner

from scholar.commands import dsh_ops

BLOCK = dsh_ops.MARKER + "\n- insert:\n    - id: x\n" + dsh_ops.END_MARKER


def invoke_init_dsh(args, input=None):
    app = typer.Typer()
    app.command()(dsh_ops.init_dsh)
    return CliRunner().invoke(app, args, input=input)


def invoke_gateway_login(args, input=None):
    app = typer.Typer()
    app.command()(dsh_ops.gateway_login)
    return CliRunner().invoke(app, args, input=input)


class _FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_gateway_login_exchanges_and_stores_capability(tmp_path, monkeypatch):
    scholar_home = tmp_path / "scholar"
    dsh_home = tmp_path / "dsh"
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["data"] = json.loads(req.data.decode("utf-8"))
        return _FakeResponse(
            {"session_token": "cap-token", "expires_at": "2026-10-01T00:00:00+00:00"}
        )

    monkeypatch.setattr(dsh_ops, "_urlopen", fake_urlopen)
    result = invoke_gateway_login(
        [
            "--code",
            "enrol-code-1",
            "--gateway",
            "https://hub.example.test/v1/mcp/scholar",
            "--scholar-home",
            str(scholar_home),
            "--dsh-home",
            str(dsh_home),
            "--workspace",
            str(tmp_path / "workspace"),
        ]
    )
    assert result.exit_code == 0, result.output
    assert captured["url"] == "https://hub.example.test/v1/session"
    assert captured["data"] == {
        "enrolment_token": "enrol-code-1",
        "session_label": "scholar-gateway-login",
    }
    credentials = dsh_home / ".credentials.yaml"
    document = yaml.safe_load(credentials.read_text(encoding="utf-8"))
    assert document[dsh_ops.DEFAULT_REMOTE_TOKEN_REF] == "cap-token"
    config_text = (dsh_home / "profiles" / "headless" / "cordis.patch.yml").read_text(
        encoding="utf-8"
    )
    assert "hub.example.test" in config_text
    assert "cap-token" not in config_text + result.output
    assert "2026-10-01" in result.output


def test_gateway_login_validates_and_stores_direct_token(
    tmp_path, monkeypatch
):
    scholar_home = tmp_path / "scholar"
    dsh_home = tmp_path / "dsh"
    token = "opaque-token-not-in-argv"
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["authorization"] = req.headers["Authorization"]
        return _FakeResponse(
            {
                "name": "Literature Group",
                "scholar_available": True,
            }
        )

    monkeypatch.setattr(dsh_ops, "_urlopen", fake_urlopen)
    monkeypatch.setattr(
        dsh_ops,
        "DEFAULT_GATEWAY_URL",
        "https://hub.example.test/v1/mcp/scholar",
    )
    result = invoke_gateway_login(
        [
            "--api-key-stdin",
            "--scholar-home",
            str(scholar_home),
            "--dsh-home",
            str(dsh_home),
            "--workspace",
            str(tmp_path / "workspace"),
        ],
        input=f"{token}\n",
    )
    assert result.exit_code == 0, result.output
    assert captured == {
        "url": "https://hub.example.test/v1/me",
        "authorization": f"Bearer {token}",
    }
    document = yaml.safe_load(
        (dsh_home / ".credentials.yaml").read_text(encoding="utf-8")
    )
    assert document[dsh_ops.DEFAULT_REMOTE_TOKEN_REF] == token
    config_text = (
        dsh_home / "profiles" / "headless" / "cordis.patch.yml"
    ).read_text(encoding="utf-8")
    assert token not in config_text + result.output
    assert "Token 名称：Literature Group" in result.output
    assert "Scholar Token 已保存为 Managed Credential" in result.output


def test_gateway_login_rejects_invalid_token_before_persistence(tmp_path, monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise dsh_ops._HTTPError(
            req.full_url,
            401,
            "Unauthorized",
            {},
            io.BytesIO(b'{"detail":"invalid credential"}'),
        )

    monkeypatch.setattr(dsh_ops, "_urlopen", fake_urlopen)
    monkeypatch.setattr(
        dsh_ops,
        "DEFAULT_GATEWAY_URL",
        "https://hub.example.test/v1/mcp/scholar",
    )
    result = invoke_gateway_login(
        [
            "--api-key-stdin",
            "--scholar-home",
            str(tmp_path / "scholar"),
            "--dsh-home",
            str(tmp_path / "dsh"),
        ],
        input="not-a-scholar-key\n",
    )
    assert result.exit_code != 0
    assert "Token 无效或已撤销" in result.output
    assert not (tmp_path / "dsh" / ".credentials.yaml").exists()


def test_gateway_login_keeps_existing_credential_on_transient_failure(
    tmp_path, monkeypatch
):
    dsh_home = tmp_path / "dsh"
    dsh_ops._store_credential(
        dsh_home,
        dsh_ops.DEFAULT_REMOTE_TOKEN_REF,
        "existing-token",
    )

    def fake_urlopen(req, timeout=None):
        raise dsh_ops._HTTPError(
            req.full_url,
            503,
            "Unavailable",
            {},
            io.BytesIO(b'{"detail":"backend unavailable"}'),
        )

    monkeypatch.setattr(dsh_ops, "_urlopen", fake_urlopen)
    monkeypatch.setattr(
        dsh_ops,
        "DEFAULT_GATEWAY_URL",
        "https://hub.example.test/v1/mcp/scholar",
    )
    result = invoke_gateway_login(
        [
            "--api-key-stdin",
            "--scholar-home",
            str(tmp_path / "scholar"),
            "--dsh-home",
            str(dsh_home),
        ],
        input="replacement-token\n",
    )
    assert result.exit_code != 0
    assert "原 Managed Credential" in result.output
    assert "未修改，请重试" in result.output
    document = yaml.safe_load(
        (dsh_home / ".credentials.yaml").read_text(encoding="utf-8")
    )
    assert document[dsh_ops.DEFAULT_REMOTE_TOKEN_REF] == "existing-token"


def test_gateway_login_rejects_custom_gateway_for_primary_token_flow(tmp_path):
    result = invoke_gateway_login(
        [
            "--api-key-stdin",
            "--gateway",
            "https://hub.example.test/v1/mcp/scholar",
            "--scholar-home",
            str(tmp_path / "scholar"),
            "--dsh-home",
            str(tmp_path / "dsh"),
        ],
        input="token\n",
    )
    assert result.exit_code != 0
    assert "--gateway 仅用于兼容" in unstyle(result.output)


def test_gateway_login_surfaces_hub_error(tmp_path, monkeypatch):
    scholar_home = tmp_path / "scholar"
    dsh_home = tmp_path / "dsh"

    def fake_urlopen(req, timeout=None):
        raise dsh_ops._HTTPError(
            req.full_url,
            401,
            "Unauthorized",
            {},
            io.BytesIO(b'{"error":{"code":"invalid_credential","message":"bad code"}}'),
        )

    monkeypatch.setattr(dsh_ops, "_urlopen", fake_urlopen)
    result = invoke_gateway_login(
        [
            "--code",
            "enrol-code-1",
            "--gateway",
            "https://hub.example.test/v1/mcp/scholar",
            "--scholar-home",
            str(scholar_home),
            "--dsh-home",
            str(dsh_home),
        ]
    )
    assert result.exit_code != 0
    assert "invalid_credential" in result.output
    assert not (dsh_home / ".credentials.yaml").exists()


def test_write_segment_idempotent(tmp_path):
    patch = tmp_path / "cordis.patch.yml"
    a1 = dsh_ops._write_segment(patch, BLOCK)
    a2 = dsh_ops._write_segment(patch, BLOCK)
    assert (a1, a2) == ("appended", "refreshed")
    text = patch.read_text(encoding="utf-8")
    assert text.count(dsh_ops.MARKER) == 1
    assert text.count(dsh_ops.END_MARKER) == 1


def test_write_segment_preserves_other_blocks(tmp_path):
    patch = tmp_path / "cordis.patch.yml"
    patch.write_text(
        "# >>> other\n- insert:\n    - id: y\n# <<< other\n", encoding="utf-8"
    )
    dsh_ops._write_segment(patch, BLOCK)
    text = patch.read_text(encoding="utf-8")
    assert "# >>> other" in text and "# <<< other" in text
    assert text.count(dsh_ops.MARKER) == 1


def test_remove_segment(tmp_path):
    patch = tmp_path / "cordis.patch.yml"
    dsh_ops._write_segment(patch, BLOCK)
    dsh_ops._remove_segment(patch)
    assert dsh_ops.MARKER not in patch.read_text(encoding="utf-8")
    # 再删一次不报错
    dsh_ops._remove_segment(patch)


def test_write_segment_strips_empty_array_placeholder(tmp_path):
    patch = tmp_path / "cordis.patch.yml"
    patch.write_text("[]\n", encoding="utf-8")
    dsh_ops._write_segment(patch, BLOCK)
    text = patch.read_text(encoding="utf-8")
    assert "[]" not in text and dsh_ops.MARKER in text


def test_ensure_rules_copy_if_missing(tmp_path, monkeypatch):
    # 伪造包布局：tmp/commands/dsh_ops.py → tmp/templates/dsh/rules/
    fake_src = tmp_path / "templates" / "dsh" / "rules"
    fake_src.mkdir(parents=True)
    (fake_src / "identity.md").write_text("researcher-profile", encoding="utf-8")
    (fake_src / "academic.md").write_text("norms", encoding="utf-8")
    ops_file = tmp_path / "commands" / "dsh_ops.py"
    ops_file.parent.mkdir(parents=True)
    ops_file.write_text("# stub", encoding="utf-8")
    monkeypatch.setattr(dsh_ops, "__file__", str(ops_file))

    scholar_home = tmp_path / "home"
    actions = dsh_ops._ensure_rules(scholar_home)
    assert len(actions) == 2
    dst = scholar_home / ".scholar" / "rules" / "identity.md"
    assert dst.read_text(encoding="utf-8") == "researcher-profile"

    # 用户自定义不覆盖
    dst.write_text("user-customized", encoding="utf-8")
    (fake_src / "identity.md").write_text("template-updated", encoding="utf-8")
    actions2 = dsh_ops._ensure_rules(scholar_home)
    assert actions2 == []
    assert dst.read_text(encoding="utf-8") == "user-customized"


def test_ensure_assets_creates_local_runtime_dirs_without_overwriting(tmp_path):
    scholar_home = tmp_path / "scholar"
    skills = scholar_home / ".scholar" / "skills"
    skills.mkdir(parents=True)
    customized = skills / "academic-research" / "SKILL.md"
    customized.parent.mkdir()
    customized.write_text("customized", encoding="utf-8")
    dsh_ops._ensure_scholar_assets(scholar_home)
    assert customized.read_text(encoding="utf-8") == "customized"
    assert (scholar_home / "output" / "parsed").is_dir()
    assert (scholar_home / "data" / "papers").is_dir()


def test_init_dsh_remote_clean_home_stores_special_token_safely(tmp_path):
    scholar_home = tmp_path / "scholar"
    dsh_home = tmp_path / "dsh"
    token = "token: with # [special] chars"
    result = invoke_init_dsh(
        [
            "--remote",
            "https://scholar.example.test/mcp",
            "--token-stdin",
            "--scholar-home",
            str(scholar_home),
            "--dsh-home",
            str(dsh_home),
            "--workspace",
            str(tmp_path / "workspace"),
        ],
        input=f"{token}\n",
    )
    assert result.exit_code == 0, result.output
    credentials = dsh_home / ".credentials.yaml"
    document = yaml.safe_load(credentials.read_text(encoding="utf-8"))
    config_text = (dsh_home / "profiles" / "headless" / "cordis.patch.yml").read_text(
        encoding="utf-8"
    )
    preset_text = (
        dsh_home / ".agent-presets" / "academic" / "agent.cordis.yml"
    ).read_text(encoding="utf-8")
    assert document[dsh_ops.DEFAULT_REMOTE_TOKEN_REF] == token
    assert token not in result.output + config_text + preset_text
    assert len(list((scholar_home / ".scholar" / "skills").iterdir())) == 15
    if dsh_ops.os.name != "nt":
        assert credentials.stat().st_mode & 0o777 == 0o600
        assert dsh_home.stat().st_mode & 0o777 == 0o700


def test_init_dsh_prepares_http_onboarding_without_storing_token(tmp_path):
    scholar_home = tmp_path / "scholar"
    dsh_home = tmp_path / "dsh"
    result = invoke_init_dsh(
        [
            "--remote",
            dsh_ops.DEFAULT_GATEWAY_URL,
            "--allow-insecure-http",
            "--scholar-home",
            str(scholar_home),
            "--dsh-home",
            str(dsh_home),
            "--workspace",
            str(tmp_path / "workspace"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert not (dsh_home / ".credentials.yaml").exists()
    preset_text = (
        dsh_home / ".agent-presets" / "academic" / "agent.cordis.yml"
    ).read_text(encoding="utf-8")
    assert dsh_ops.DEFAULT_GATEWAY_URL in preset_text
    assert "bearerTokenEnv: SCHOLAR_REMOTE_TOKEN" in preset_text
    assert "allowInsecureHttp: true" in preset_text


def test_init_dsh_rejects_public_http_without_explicit_opt_in(tmp_path):
    result = invoke_init_dsh(
        [
            "--remote",
            dsh_ops.DEFAULT_GATEWAY_URL,
            "--scholar-home",
            str(tmp_path / "scholar"),
            "--dsh-home",
            str(tmp_path / "dsh"),
        ],
    )
    assert result.exit_code != 0
    assert "must use HTTPS" in result.output


def test_init_dsh_check_does_not_provision_clean_home(tmp_path):
    scholar_home = tmp_path / "scholar"
    dsh_home = tmp_path / "dsh"
    result = invoke_init_dsh(
        [
            "--check",
            "--remote",
            "https://scholar.example.test/mcp",
            "--scholar-home",
            str(scholar_home),
            "--dsh-home",
            str(dsh_home),
        ],
    )
    assert result.exit_code == 1
    assert not scholar_home.exists()
    assert not dsh_home.exists()


def test_init_dsh_restores_config_when_credentials_are_malformed(tmp_path):
    scholar_home = tmp_path / "scholar"
    dsh_home = tmp_path / "dsh"
    patch = dsh_home / "profiles" / "headless" / "cordis.patch.yml"
    patch.parent.mkdir(parents=True)
    patch.write_text("# existing\n", encoding="utf-8")
    preset = dsh_home / ".agent-presets" / "academic" / "custom.txt"
    preset.parent.mkdir(parents=True)
    preset.write_text("existing", encoding="utf-8")
    credentials = dsh_home / ".credentials.yaml"
    credentials.write_text("[invalid]\n", encoding="utf-8")
    result = invoke_init_dsh(
        [
            "--remote",
            "https://scholar.example.test/mcp",
            "--token-stdin",
            "--scholar-home",
            str(scholar_home),
            "--dsh-home",
            str(dsh_home),
        ],
        input="secret\n",
    )
    assert result.exit_code != 0
    assert patch.read_text(encoding="utf-8") == "# existing\n"
    assert preset.read_text(encoding="utf-8") == "existing"
    assert credentials.read_text(encoding="utf-8") == "[invalid]\n"


def test_detect_dev_tree(tmp_path):
    assert dsh_ops._detect_dev_tree(tmp_path) is False
    (tmp_path / "scholar").mkdir()
    (tmp_path / "scholar" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "scholar_mcp").mkdir()
    assert dsh_ops._detect_dev_tree(tmp_path) is True


# ── academic 用户级预设 ────────────────────────────────────────────────────

ARGS = dict(
    scholar_home=Path("C:/papers/.scholar-studio"),
    workspace=Path("C:/papers/ws"),
    python_cmd="C:/py/python.exe",
    dev_tree=False,
)  # type: dict


def test_write_preset_creates_dir_and_files(tmp_path):
    out = dsh_ops._write_preset(tmp_path, **ARGS)
    pdir = tmp_path / ".agent-presets" / "academic"
    assert Path(out) == pdir
    comp = (pdir / "agent.cordis.yml").read_text(encoding="utf-8")
    meta = (pdir / "preset.yml").read_text(encoding="utf-8")
    # standard 基座在位
    assert "id: tool-bash" in comp and "id: persona" in comp
    # scholar 段在位，且静态烘焙
    assert "id: mcp-scholar" in comp and "id: scholar-native" in comp
    assert '"C:/py/python.exe"' in comp
    assert "C:/papers/ws" in comp
    assert "process.cwd()" not in comp.split("scholar（由")[1]
    # 元数据
    assert "学术模式" in meta and "order: 5" in meta


def test_write_preset_idempotent_wholesale_rewrite(tmp_path):
    dsh_ops._write_preset(tmp_path, **ARGS)
    pdir = tmp_path / ".agent-presets" / "academic"
    comp = pdir / "agent.cordis.yml"
    (comp).write_text("garbage", encoding="utf-8")
    dsh_ops._write_preset(tmp_path, **ARGS)
    assert "id: mcp-scholar" in comp.read_text(encoding="utf-8")
    # 幂等：无重复段
    text = comp.read_text(encoding="utf-8")
    assert text.count("id: mcp-scholar") == 1


def test_write_preset_dev_tree_adds_pythonpath(tmp_path):
    dsh_ops._write_preset(tmp_path, **{**ARGS, "dev_tree": True})
    comp = (tmp_path / ".agent-presets" / "academic" / "agent.cordis.yml").read_text(
        encoding="utf-8"
    )
    scholar_seg = comp.split("scholar（由")[1]
    assert "PYTHONPATH" in scholar_seg
    # 基座段不受污染
    assert comp.count("PYTHONPATH") == 1


def test_remove_preset(tmp_path):
    dsh_ops._write_preset(tmp_path, **ARGS)
    pdir = tmp_path / ".agent-presets" / "academic"
    assert pdir.exists()
    dsh_ops._remove_preset(tmp_path)
    assert not pdir.exists()
    dsh_ops._remove_preset(tmp_path)  # 再删不报错


# ── remote 模式（MCP over HTTP，数据零分发）────────────────────────────────


def test_remote_rows_use_streamable_http():
    row = dsh_ops._mcp_scholar_row(
        "C:/py/python.exe",
        ARGS["scholar_home"],
        ARGS["workspace"],
        False,
        "http://127.0.0.1:9845/mcp",
        "",
        token_ref="SCHOLAR_REMOTE_TOKEN",
    )
    assert "streamable-http" in row and "url:" in row
    assert "command" not in row and "SCHOLAR_HOME" not in row


def test_remote_rows_with_credential_reference():
    row = dsh_ops._mcp_scholar_row(
        "C:/py/python.exe",
        ARGS["scholar_home"],
        ARGS["workspace"],
        False,
        "https://scholar.example.test/mcp",
        "",
        token_ref="SCHOLAR_REMOTE_TOKEN",
    )
    assert "bearerTokenEnv: SCHOLAR_REMOTE_TOKEN" in row
    assert "Authorization" not in row
    assert "failOnStartupError: false" in row
    import pytest

    with pytest.raises(ValueError, match="requires a Bearer credential reference"):
        dsh_ops._mcp_scholar_row(
            "C:/py/python.exe",
            ARGS["scholar_home"],
            ARGS["workspace"],
            False,
            "https://scholar.example.test/mcp",
            "",
        )


def test_remote_rows_mark_public_http_as_development_only():
    row = dsh_ops._mcp_scholar_row(
        "C:/py/python.exe",
        ARGS["scholar_home"],
        ARGS["workspace"],
        False,
        "http://192.0.2.10/mcp",
        "",
        token_ref="SCHOLAR_REMOTE_TOKEN",
        allow_insecure_http=True,
    )
    assert "allowInsecureHttp: true" in row


def test_write_preset_remote(tmp_path):
    dsh_ops._write_preset(
        tmp_path,
        **{
            **ARGS,
            "remote_url": "http://127.0.0.1:9845/mcp",
            "token_ref": "SCHOLAR_REMOTE_TOKEN",
        },
    )
    comp = (tmp_path / ".agent-presets" / "academic" / "agent.cordis.yml").read_text(
        encoding="utf-8"
    )
    scholar_seg = comp.split("scholar（由")[1]
    assert "streamable-http" in scholar_seg
    assert "stdio" not in scholar_seg
    assert "failOnStartupError: false" in scholar_seg
    # 技能与人格插件仍本地（wheel 自带，与数据无关）
    assert "scholar-skills" in scholar_seg and "scholar-native" in scholar_seg


def test_build_patch_block_remote():
    block = dsh_ops._build_patch_block(
        ARGS["scholar_home"],
        "C:/py/python.exe",
        False,
        workspace=ARGS["workspace"],
        remote_url="http://127.0.0.1:9845/mcp",
        token_ref="SCHOLAR_REMOTE_TOKEN",
    )
    assert "streamable-http" in block and "stdio" not in block
    assert "failOnStartupError: false" in block
    assert block.count(dsh_ops.MARKER) == 1
    assert "name: '@deepseek-ai/dsh-scholar-native'" in block


def test_remote_url_requires_https_or_loopback_http():
    assert (
        dsh_ops._validated_remote_url("https://scholar.example.test/mcp")
        == "https://scholar.example.test/mcp"
    )
    assert (
        dsh_ops._validated_remote_url("http://127.0.0.1:9845/mcp")
        == "http://127.0.0.1:9845/mcp"
    )
    import pytest

    with pytest.raises(Exception, match="must use HTTPS"):
        dsh_ops._validated_remote_url("http://192.0.2.10:9845/mcp")
    with pytest.raises(Exception, match="numeric loopback"):
        dsh_ops._validated_remote_url("http://localhost:9845/mcp")
    with pytest.raises(Exception, match="userinfo"):
        dsh_ops._validated_remote_url("https://user:secret@scholar.example.test/mcp")
    with pytest.raises(Exception, match="fragment"):
        dsh_ops._validated_remote_url("https://scholar.example.test/mcp#fragment")


def test_store_credential_supports_special_characters_and_private_mode(tmp_path):
    token = 'quote:" backslash:\\ newline-not-present !@#$%^&*()'
    path = dsh_ops._store_credential(tmp_path, "SCHOLAR_REMOTE_TOKEN", token)
    import yaml

    assert yaml.safe_load(path.read_text(encoding="utf-8")) == {
        "SCHOLAR_REMOTE_TOKEN": token
    }
    if dsh_ops.os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
        assert tmp_path.stat().st_mode & 0o777 == 0o700


def test_ensure_scholar_assets_populates_clean_home(tmp_path):
    actions = dsh_ops._ensure_scholar_assets(tmp_path)
    skills = tmp_path / ".scholar" / "skills"
    assert len([path for path in skills.iterdir() if path.is_dir()]) == 15
    assert actions
    custom = skills / "paper-deep-dive" / "SKILL.md"
    custom.write_text("custom", encoding="utf-8")
    dsh_ops._ensure_scholar_assets(tmp_path)
    assert custom.read_text(encoding="utf-8") == "custom"


def test_stdio_rows_are_mandatory():
    row = dsh_ops._mcp_scholar_row(
        "python",
        ARGS["scholar_home"],
        ARGS["workspace"],
        False,
        None,
        "",
    )
    assert "failOnStartupError: true" in row
