"""dsh_ops.py — dsh (deepseek-harness) 接入命令。

`scholar init-dsh` 把 Scholar Studio 以原生插件挂进 dsh，双通道：

1. **用户级预设** ~/.dsh/.agent-presets/academic/（本命令的核心产物）
   standard 预设全量行 + scholar 三层插件，Web UI「自定义」组直接出现
   「学术模式」；preset.yml 仅 name/description/order 元数据，trust=user。
2. **headless patch** ~/.dsh/profiles/headless/cordis.patch.yml 的 >>> scholar <<<
   段——headless bundle 无 preset roster，CLI one-shot 只能走 profile patch
   （与其它实验段共存，幂等可回滚）。

三层插件（两通道同构）：
  1. mcp-scholar      dsh-mcp-client：stdio 挂 `python -m scholar_mcp`（16 工具阅读阶梯）
                      SCHOLAR_HOME=本 CLI 解析的知识库根；SCHOLAR_WORKSPACE=学术工作区
                      （patch 用 !!js process.cwd() 随启动目录；预设按会话挂载、
                      组装每进程只装载一次，cwd 语义失效——改为 init-dsh 时静态烘焙）
  2. scholar-skills   skill-filesystem 实例 → <SCHOLAR_HOME>/.scholar/skills（15 技能）
  3. scholar-native   dsh 原生 package：人格 + 文献环境注入

用法：
  scholar init-dsh                # 安装/刷新（自动探测 dev/global 形态）
  scholar init-dsh --check        # 前置校验 + 打印当前段
  scholar init-dsh --uninstall    # 卸载（删 patch 段 + 预设目录）
"""

import json
import os
import re
import shutil
import sys
import ipaddress
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlparse, urlsplit
from urllib.error import HTTPError as _HTTPError
from urllib.error import URLError as _URLError
from urllib.request import Request as _Request
from urllib.request import urlopen as _urlopen

import typer
import yaml
from rich.console import Console
from rich.panel import Panel

from scholar import config
from scholar._shared import app

console = Console()

MARKER = "# >>> scholar"
END_MARKER = "# <<< scholar"
DEFAULT_REMOTE_TOKEN_REF = "SCHOLAR_REMOTE_TOKEN"
DEFAULT_GATEWAY_URL = "http://47.108.198.147:8081/v1/mcp/scholar"
_CREDENTIAL_REF = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _y(p) -> str:
    return str(p).replace("\\", "/")


def _patch_path(dsh_home: Path, profile: str) -> Path:
    return dsh_home / "profiles" / profile / "cordis.patch.yml"


def _detect_dev_tree(scholar_home: Path) -> bool:
    """源码树（editable/源码运行）判定：scholar 包与 .scholar/ 同根。"""
    return (Path(scholar_home) / "scholar" / "__init__.py").exists() and (
        Path(scholar_home) / "scholar_mcp"
    ).exists()


def _preset_dir(dsh_home: Path) -> Path:
    return dsh_home / ".agent-presets" / "academic"


def _preset_base_template() -> Path:
    """预设基座：dsh standard 预设的全量插件行（复制时点见文件头注释）。"""
    return (
        Path(__file__).resolve().parent.parent
        / "templates"
        / "dsh"
        / "preset"
        / "base.agent.cordis.yml"
    )


def _mcp_scholar_row(
    python_cmd: str,
    scholar_home: Path,
    workspace: Path,
    dev_tree: bool,
    remote_url: str | None,
    indent: str,
    token_ref: str | None = None,
    allow_insecure_http: bool = False,
) -> str:
    """mcp-scholar 插件行。remote_url 非空时走 streamable-http（服务器集中部署，
    数据零分发；token_ref 由 dsh credentials 服务逐请求解析；初始失败保持后台重连），
    否则使用启动失败即报错的 stdio 本地子进程。indent 为行缩进前缀
    （patch 4 空格、预设 0 空格）。"""
    i = indent
    if remote_url:
        if token_ref is None:
            raise ValueError("remote Scholar requires a Bearer credential reference")
        bearer = f"{i}    bearerTokenEnv: {token_ref}\n"
        insecure = (
            f"{i}    allowInsecureHttp: true\n" if allow_insecure_http else ""
        )
        return f"""{i}- id: mcp-scholar
{i}  name: '@deepseek-ai/dsh-mcp-client'
{i}  config:
{i}    serverName: scholar
{i}    transport: streamable-http
{i}    url: "{remote_url}"
{bearer}{insecure}{i}    failOnStartupError: false
"""
    env_lines = [
        f"{i}    env:",
        f'{i}      SCHOLAR_HOME: "{_y(scholar_home)}"',
        f'{i}      SCHOLAR_WORKSPACE: "{_y(workspace)}"',
    ]
    if dev_tree:
        env_lines.append(f'{i}      PYTHONPATH: "{_y(scholar_home)}"')
    return f"""{i}- id: mcp-scholar
{i}  name: '@deepseek-ai/dsh-mcp-client'
{i}  config:
{i}    serverName: scholar
{i}    transport: stdio
{i}    command: "{_y(python_cmd)}"
{i}    args: ['-m', 'scholar_mcp']
{chr(10).join(env_lines)}
{i}    failOnStartupError: true
"""


def _build_patch_block(
    scholar_home: Path,
    python_cmd: str,
    dev_tree: bool,
    workspace: Path | None = None,
    remote_url: str | None = None,
    token_ref: str | None = None,
    allow_insecure_http: bool = False,
) -> str:
    if workspace is None:
        workspace = scholar_home
    if remote_url:
        mcp_row = _mcp_scholar_row(
            python_cmd,
            scholar_home,
            workspace,
            dev_tree,
            remote_url,
            "    ",
            token_ref=token_ref,
            allow_insecure_http=allow_insecure_http,
        )
        skills_dir = Path(scholar_home) / ".scholar" / "skills"
        return f"""{MARKER}
- insert:
{mcp_row.rstrip()}
    - id: scholar-skills
      name: '@deepseek-ai/dsh-skill-filesystem'
      config:
        providerName: scholar
        includeDefaultRoots: false
        customSkillDirs:
          - "{_y(skills_dir)}"
    - id: scholar-native
      name: '@deepseek-ai/dsh-scholar-native'
      config:
        scholarHome: "{_y(scholar_home)}"
{END_MARKER}"""
    env_lines = [
        "        env:",
        f'          SCHOLAR_HOME: "{_y(scholar_home)}"',
        "          SCHOLAR_WORKSPACE: !!js process.cwd()",
    ]
    if dev_tree:
        env_lines.append(f'          PYTHONPATH: "{_y(scholar_home)}"')
    skills_dir = Path(scholar_home) / ".scholar" / "skills"
    return f"""{MARKER}
- insert:
    - id: mcp-scholar
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: scholar
        transport: stdio
        command: "{_y(python_cmd)}"
        args: ['-m', 'scholar_mcp']
{chr(10).join(env_lines)}
        failOnStartupError: true
    - id: scholar-skills
      name: '@deepseek-ai/dsh-skill-filesystem'
      config:
        providerName: scholar
        includeDefaultRoots: false
        customSkillDirs:
          - "{_y(skills_dir)}"
    - id: scholar-native
      name: '@deepseek-ai/dsh-scholar-native'
      config:
        scholarHome: "{_y(scholar_home)}"
{END_MARKER}"""


def _preflight(scholar_home: Path, python_cmd: str, remote: bool = False):
    problems = []
    skills_dir = Path(scholar_home) / ".scholar" / "skills"
    console.print(f"[preflight] python={python_cmd}")
    console.print(f"[preflight] SCHOLAR_HOME={scholar_home}")
    if remote:
        console.print("[preflight] 模式=remote（MCP over HTTP，本地无论文数据）")
    else:
        try:
            import importlib

            importlib.import_module("scholar_mcp")
            console.print(
                "[preflight] [OK] scholar_mcp 可导入（MCP server 将随 dsh 启动）"
            )
        except Exception as e:  # pragma: no cover
            problems.append(f"scholar_mcp 不可导入: {e}")
    if skills_dir.exists():
        n = len([d for d in skills_dir.iterdir() if not d.name.startswith(".")])
        console.print(f"[preflight] [OK] 技能目录: {n} 个技能 @ {skills_dir}")
        if n == 0:
            problems.append(f"技能目录为空（先 scholar init）: {skills_dir}")
    else:
        problems.append(
            f"技能目录不存在（先 scholar init 或 --scholar-home）: {skills_dir}"
        )
    parsed = Path(scholar_home) / "output" / "parsed"
    if parsed.exists():
        console.print(
            f"[preflight] [OK] parsed 目录: {len(list(parsed.glob('*.json')))} 篇（动态层索引源）"
        )
    else:
        console.print(
            "[preflight] [WARN] parsed 目录缺失——动态层注入将为空，工具层不受影响"
        )
    return problems


def _ensure_rules(scholar_home: Path) -> list[str]:
    """把包内 rules 模板（templates/dsh/rules/）落到 <scholar_home>/.scholar/rules/，
    copy-if-missing——绝不覆盖用户自定义。返回动作列表。"""
    actions = []
    src_dir = Path(__file__).resolve().parent.parent / "templates" / "dsh" / "rules"
    if not src_dir.exists():
        return actions
    dst_dir = Path(scholar_home) / ".scholar" / "rules"
    dst_dir.mkdir(parents=True, exist_ok=True)
    for f in src_dir.iterdir():
        if not f.is_file():
            continue
        dst = dst_dir / f.name
        if not dst.exists():
            shutil.copyfile(f, dst)
            actions.append(f"rule installed: {dst.name}")
    return actions


def _ensure_scholar_assets(
    scholar_home: Path, *, include_runtime_dirs: bool = True
) -> list[str]:
    """Install missing packaged Scholar assets without replacing user files."""
    actions = []
    src_dir = Path(__file__).resolve().parent.parent / "templates"
    dst_dir = Path(scholar_home) / ".scholar"
    for src in src_dir.rglob("*"):
        if not src.is_file() or "dsh" in src.relative_to(src_dir).parts:
            continue
        dst = dst_dir / src.relative_to(src_dir)
        if dst.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        actions.append(f"asset installed: {dst.relative_to(dst_dir)}")
    if include_runtime_dirs:
        for relative in (
            Path("data/papers"),
            Path("output/parsed"),
            Path("output/notes"),
            Path("output/drafts"),
            Path("output/bib"),
            Path("output/experiments"),
            Path("output/datasets"),
            Path("output/pdfs"),
            Path("output/digests"),
            Path("output/logs"),
            Path("LEAN"),
        ):
            target = scholar_home / relative
            if target.exists():
                continue
            target.mkdir(parents=True)
            actions.append(f"directory created: {relative}")
    return actions


def _validated_remote_url(value: str, *, allow_http: bool = False) -> str:
    try:
        parsed = urlparse(value)
        hostname = parsed.hostname or ""
        port = parsed.port
    except ValueError as error:
        raise typer.BadParameter("remote MCP URL has an invalid port") from error
    if not parsed.scheme or not hostname:
        raise typer.BadParameter("remote MCP URL must be absolute")
    if parsed.username is not None or parsed.password is not None:
        raise typer.BadParameter("remote MCP URL must not contain userinfo")
    if parsed.fragment:
        raise typer.BadParameter("remote MCP URL must not contain a fragment")
    if port is not None and not 1 <= port <= 65535:
        raise typer.BadParameter("remote MCP URL has an invalid port")
    if parsed.scheme == "https" and hostname:
        return value
    if parsed.scheme == "http":
        try:
            if ipaddress.ip_address(hostname).is_loopback:
                return value
        except ValueError:
            pass
        if allow_http:
            console.print(
                "[yellow]警告：网关为 HTTP。production 模式的 Proxy Hub "
                "会强制 HTTPS origin，届时请改用 https 地址重跑。[/]"
            )
            return value
    raise typer.BadParameter(
        "remote MCP URL must use HTTPS, or HTTP on a numeric loopback address for an SSH tunnel"
    )


def _validated_credential_ref(value: str) -> str:
    if not _CREDENTIAL_REF.fullmatch(value):
        raise typer.BadParameter(
            "token reference must be a POSIX environment-variable name"
        )
    return value


def _store_credential(dsh_home: Path, ref: str, value: str) -> Path:
    """Merge one value into the managed dsh credential document."""
    if not value:
        raise typer.BadParameter("Bearer token from stdin is empty")
    directory = Path(dsh_home)
    path = directory / ".credentials.yaml"
    directory.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        directory.chmod(0o700)
    document = {}
    if path.exists():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        if loaded is not None and not isinstance(loaded, dict):
            raise typer.BadParameter(
                f"credentials document is not a YAML mapping: {path}"
            )
        document = loaded or {}
        if not all(
            isinstance(key, str)
            and _CREDENTIAL_REF.fullmatch(key)
            and isinstance(item, str)
            and item
            for key, item in document.items()
        ):
            raise typer.BadParameter(
                f"credentials document contains an invalid entry: {path}"
            )
    document[ref] = value
    with NamedTemporaryFile(
        "w", encoding="utf-8", dir=directory, prefix=".credentials.", delete=False
    ) as handle:
        yaml.safe_dump(document, handle, allow_unicode=True, sort_keys=True)
        temporary = Path(handle.name)
    try:
        if os.name != "nt":
            temporary.chmod(0o600)
        os.replace(temporary, path)
        if os.name != "nt":
            path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def _build_preset_rows(
    scholar_home: Path,
    workspace: Path,
    python_cmd: str,
    dev_tree: bool,
    remote_url: str | None = None,
    token_ref: str | None = None,
    allow_insecure_http: bool = False,
) -> str:
    """预设组装的 scholar 段——结构与 headless patch 同构，两处差异：
    SCHOLAR_WORKSPACE 静态烘焙（预设组装每进程装载一次，cwd 语义失效）；
    结构与 standard 预设的裸 skill-filesystem 行保持一致（登记进分层
    registry，不发布进程级服务，无需 isolate realm）。"""
    mcp_row = _mcp_scholar_row(
        python_cmd,
        scholar_home,
        workspace,
        dev_tree,
        remote_url,
        "",
        token_ref=token_ref,
        allow_insecure_http=allow_insecure_http,
    )
    skills_dir = Path(scholar_home) / ".scholar" / "skills"
    return f"""# ── scholar（由 `scholar init-dsh` 生成/刷新，勿手改）──────────────────────
{mcp_row.rstrip()}

- id: scholar-skills
  name: '@deepseek-ai/dsh-skill-filesystem'
  config:
    providerName: scholar
    includeDefaultRoots: false
    customSkillDirs:
      - "{_y(skills_dir)}"

- id: scholar-native
  name: '@deepseek-ai/dsh-scholar-native'
  config:
    scholarHome: "{_y(scholar_home)}"
"""


def _write_preset(
    dsh_home: Path,
    scholar_home: Path,
    workspace: Path,
    python_cmd: str,
    dev_tree: bool,
    remote_url: str | None = None,
    token_ref: str | None = None,
    allow_insecure_http: bool = False,
) -> str:
    """写用户级 academic 预设（standard 基座 + scholar 段，整文件重写、幂等）。"""
    base = _preset_base_template().read_text(encoding="utf-8")
    rows = _build_preset_rows(
        scholar_home,
        workspace,
        python_cmd,
        dev_tree,
        remote_url=remote_url,
        token_ref=token_ref,
        allow_insecure_http=allow_insecure_http,
    )
    pdir = _preset_dir(dsh_home)
    pdir.mkdir(parents=True, exist_ok=True)
    header = (
        "# The `academic` agent preset — generated by `scholar init-dsh`.\n"
        "# Base: dsh `standard` preset rows (verbatim copy, refresh on init-dsh)\n"
        "# + scholar rows appended below. Regenerated wholesale on every\n"
        "# `scholar init-dsh`; hand edits will be lost.\n\n"
    )
    (pdir / "agent.cordis.yml").write_text(
        header + base.rstrip() + "\n\n" + rows, encoding="utf-8"
    )
    meta = (
        Path(__file__).resolve().parent.parent
        / "templates"
        / "dsh"
        / "preset"
        / "preset.yml"
    )
    shutil.copyfile(meta, pdir / "preset.yml")
    return str(pdir)


def _write_segment(patch: Path, block: str) -> str:
    """幂等写入 >>> scholar <<< 段，返回动作描述。"""
    patch.parent.mkdir(parents=True, exist_ok=True)
    prev = patch.read_text(encoding="utf-8") if patch.exists() else ""
    if MARKER in prev:
        seg = re.compile(re.escape(MARKER) + r"[\s\S]*?" + re.escape(END_MARKER), re.M)
        patch.write_text(seg.sub(block.strip(), prev), encoding="utf-8")
        return "refreshed"
    stripped = re.sub(r"^\s*\[\]\s*\n?", "", prev).rstrip()
    patch.write_text(
        (stripped + "\n\n" + block + "\n") if stripped else (block + "\n"),
        encoding="utf-8",
    )
    return "appended"


def _remove_segment(patch: Path):
    if not patch.exists() or MARKER not in patch.read_text(encoding="utf-8"):
        console.print("[uninstall] 无 scholar 段，skip")
        return
    prev = patch.read_text(encoding="utf-8")
    seg = re.compile(
        r"\n?" + re.escape(MARKER) + r"[\s\S]*?" + re.escape(END_MARKER) + r"\n?", re.M
    )
    patch.write_text(
        seg.sub("\n", prev).replace("\n\n\n", "\n\n").lstrip("\n"),
        encoding="utf-8",
    )
    console.print(f"[uninstall] removed scholar block @ {patch}")


def _remove_preset(dsh_home: Path):
    pdir = _preset_dir(dsh_home)
    if not pdir.exists():
        return
    shutil.rmtree(pdir)
    console.print(f"[uninstall] removed preset dir @ {pdir}")


@app.command()
def init_dsh(
    uninstall: bool = typer.Option(
        False, "--uninstall", help="移除 >>> scholar <<< 段与 academic 预设目录"
    ),
    check: bool = typer.Option(False, "--check", help="仅校验并打印当前段，不写入"),
    profile: str = typer.Option("headless", help="dsh profile（CLI one-shot 用）"),
    dsh_home: Path = typer.Option(None, "--dsh-home", help="dsh 配置根（默认 ~/.dsh）"),
    scholar_home: Path = typer.Option(
        None, "--scholar-home", help="知识库根（默认本 CLI 解析值）"
    ),
    workspace: Path = typer.Option(
        None,
        "--workspace",
        help="学术工作区（论文/笔记根；默认当前目录；预设静态烘焙）",
    ),
    python_cmd: Path = typer.Option(
        None, "--python", help="MCP server 解释器（默认本 CLI 的 sys.executable）"
    ),
    remote: str = typer.Option(
        None,
        "--remote",
        help="服务器集中模式：scholar_mcp 的 streamable-http URL "
        "（HTTPS，或 SSH 隧道 http://127.0.0.1:9845/mcp）。"
        "本地无论文数据、无需 PG 凭据；服务器由管理员部署（scholar_mcp + 数据私有）",
    ),
    token_env: str = typer.Option(
        DEFAULT_REMOTE_TOKEN_REF,
        "--token-env",
        help="dsh credentials 中保存 Bearer token 的引用名",
    ),
    token_stdin: bool = typer.Option(
        False,
        "--token-stdin",
        help="从标准输入读取 Bearer token 并写入 dsh 的 owner-only credentials 文件",
    ),
    allow_insecure_http: bool = typer.Option(
        False,
        "--allow-insecure-http",
        help="开发环境显式允许公网 HTTP Scholar endpoint",
    ),
):
    """把 Scholar Studio 挂进 dsh（学术模式预设 + headless one-shot patch）。"""
    dsh_home = Path(dsh_home) if dsh_home else Path.home() / ".dsh"
    scholar_home = Path(scholar_home) if scholar_home else Path(config.SCHOLAR_HOME)
    workspace = Path(workspace) if workspace else Path.cwd()
    py = str(python_cmd) if python_cmd else sys.executable
    patch = _patch_path(dsh_home, profile)
    dev_tree = _detect_dev_tree(scholar_home)

    if uninstall:
        _remove_segment(patch)
        _remove_preset(dsh_home)
        return

    remote = (
        _validated_remote_url(remote, allow_http=allow_insecure_http)
        if remote
        else None
    )
    token_ref = _validated_credential_ref(token_env) if remote else None
    if token_stdin and not remote:
        raise typer.BadParameter("--token-stdin requires --remote")
    if allow_insecure_http and not remote:
        raise typer.BadParameter("--allow-insecure-http requires --remote")
    if check and token_stdin:
        raise typer.BadParameter("--check cannot store a credential")

    if not check:
        for action in _ensure_scholar_assets(
            scholar_home, include_runtime_dirs=not bool(remote)
        ):
            console.print(f"[green][OK][/] {action}")
    problems = _preflight(scholar_home, py, remote=bool(remote))
    if problems:
        console.print("\n[red]前置校验未通过:[/]")
        for p in problems:
            console.print(f"  - {p}")
        raise typer.Exit(1)

    block = _build_patch_block(
        scholar_home,
        py,
        dev_tree,
        workspace=workspace,
        remote_url=remote,
        token_ref=token_ref,
        allow_insecure_http=allow_insecure_http,
    )
    if check:
        console.print(Panel(block, title=f"{patch} (preview)", border_style="cyan"))
        if patch.exists() and MARKER in patch.read_text(encoding="utf-8"):
            console.print("[green]已安装（如需刷新请去掉 --check）[/]")
        else:
            console.print("[yellow]未安装（去掉 --check 执行写入）[/]")
        return

    token_value = sys.stdin.readline().rstrip("\r\n") if token_stdin else None
    patch_previous = patch.read_bytes() if patch.exists() else None
    preset_dir = _preset_dir(dsh_home)
    preset_previous = (
        {
            item.relative_to(preset_dir): item.read_bytes()
            for item in preset_dir.rglob("*")
            if item.is_file()
        }
        if preset_dir.exists()
        else None
    )
    try:
        action = _write_segment(patch, block)
        pdir = _write_preset(
            dsh_home,
            scholar_home,
            workspace,
            py,
            dev_tree,
            remote_url=remote,
            token_ref=token_ref,
            allow_insecure_http=allow_insecure_http,
        )
        rules_actions = _ensure_rules(scholar_home)
        if token_value is not None:
            _store_credential(dsh_home, token_ref, token_value)
    except Exception:
        if patch_previous is None:
            patch.unlink(missing_ok=True)
        else:
            patch.parent.mkdir(parents=True, exist_ok=True)
            patch.write_bytes(patch_previous)
        shutil.rmtree(preset_dir, ignore_errors=True)
        if preset_previous is not None:
            for relative, content in preset_previous.items():
                target = preset_dir / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
        raise

    console.print(f"[green][OK][/] {action} scholar block @ {patch}")
    console.print(f"[green][OK][/] academic preset written @ {pdir}")
    for r in rules_actions:
        console.print(f"[green][OK][/] {r}")
    if token_value is not None:
        console.print("[green][OK][/] Bearer credential stored")
    console.print("\n验证：")
    console.print('  dsh --profile headless "用 scholar 工具查一下知识库规模"')
    console.print("  Web UI → 设置 → Agent 预设 → 自定义 →「学术模式」开新会话")
    if remote:
        console.print(f"\n[bold]remote 模式[/]：MCP 端点 = {remote}")
        console.print("隧道模式示例：ssh -N -L 9845:127.0.0.1:9845 server-47")
        console.print(f"Bearer 鉴权引用：{token_ref}")
    console.print("\n卸载：")
    console.print("  scholar init-dsh --uninstall")


def _exchange_capability(
    gateway_url: str, code: str, timeout: int = 20
) -> tuple[str, str]:
    """用一次性 enrolment 兑换码换取 Proxy Hub capability（session_token）。"""
    parts = urlsplit(gateway_url)
    origin = f"{parts.scheme}://{parts.netloc}"
    req = _Request(
        origin + "/v1/session",
        data=json.dumps(
            {"enrolment_token": code, "session_label": "scholar-gateway-login"}
        ).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with _urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except _HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("error", {})
            message = "{}: {}".format(
                detail.get("code", f"http_{exc.code}"), detail.get("message", "")
            )
        except Exception:
            message = f"HTTP {exc.code}"
        raise typer.BadParameter(f"兑换失败：{message}") from exc
    except _URLError as exc:
        raise typer.BadParameter(
            f"无法连接 Proxy Hub（{origin}）：{exc.reason}"
        ) from exc
    token = body.get("session_token")
    if not token:
        raise typer.BadParameter("Proxy Hub 响应缺少 session_token")
    expires = body.get("expires_at", "")
    return token, expires


def _validated_token(value: str) -> str:
    """Reject an empty Scholar Token before server validation."""
    token = value.strip()
    if not token:
        raise typer.BadParameter("Token 为空")
    return token


def _validate_token(gateway_url: str, token: str, timeout: int = 20) -> dict:
    """Validate a Token against Proxy Hub before managed persistence."""
    parts = urlsplit(gateway_url)
    origin = f"{parts.scheme}://{parts.netloc}"
    req = _Request(
        origin + "/v1/me",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="GET",
    )
    try:
        with _urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except _HTTPError as exc:
        if exc.code in {401, 403}:
            raise typer.BadParameter(
                "Token 无效或已撤销，请向管理员申请新 Token"
            ) from exc
        raise typer.BadParameter(
            f"Proxy Hub 暂时不可用（HTTP {exc.code}）；原 Managed Credential 未修改，请重试"
        ) from exc
    except (_URLError, TimeoutError) as exc:
        reason = getattr(exc, "reason", exc)
        raise typer.BadParameter(
            f"无法连接 Proxy Hub（{origin}）：{reason}；原 Managed Credential 未修改，请重试"
        ) from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise typer.BadParameter(
            "Proxy Hub /v1/me 返回无效响应；原 Managed Credential 未修改，请重试"
        ) from exc
    if not isinstance(body, dict) or not isinstance(body.get("name"), str):
        raise typer.BadParameter(
            "Proxy Hub /v1/me 响应缺少 Token 名称；原 Managed Credential 未修改，请重试"
        )
    return body


def _requires_insecure_http(url: str) -> bool:
    """Return whether a remote URL needs DSH's development HTTP override."""
    parsed = urlparse(url)
    return parsed.scheme == "http" and not (
        parsed.hostname
        and (
            parsed.hostname == "::1"
            or (
                _is_ipv4_loopback(parsed.hostname)
            )
        )
    )


def _is_ipv4_loopback(hostname: str) -> bool:
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


@app.command(name="gateway-login")
def gateway_login(
    code: str = typer.Option(
        None, "--code", help="Proxy Hub 一次性 enrolment 兑换码（管理员发放）"
    ),
    code_stdin: bool = typer.Option(False, "--code-stdin", help="从标准输入读取兑换码"),
    api_key_stdin: bool = typer.Option(
        False,
        "--api-key-stdin",
        help="从标准输入读取 Scholar Token（推荐）",
    ),
    gateway: str = typer.Option(
        None,
        "--gateway",
        help="Proxy Hub 网关 MCP URL（默认 http://47.108.198.147:8081/v1/mcp/scholar）",
    ),
    dsh_home: Path = typer.Option(None, "--dsh-home", help="dsh 配置根（默认 ~/.dsh）"),
    scholar_home: Path = typer.Option(None, "--scholar-home", help="知识库根"),
    workspace: Path = typer.Option(None, "--workspace", help="学术工作区"),
    python_cmd: Path = typer.Option(None, "--python", help="MCP server 解释器"),
    profile: str = typer.Option("headless", help="dsh profile"),
    token_env: str = typer.Option(
        DEFAULT_REMOTE_TOKEN_REF,
        "--token-env",
        help="Token 或 capability 存入 DSH Managed Credential 的引用名",
    ),
):
    """验证并保存 Scholar Token，写入 Proxy Hub 学者模式配置。

    默认隐藏读取管理员签发的 Token。--api-key-stdin 保留为兼容选项；
    --code 与 --code-stdin 保留用于旧 enrolment/capability 流程。
    """
    dsh_home = Path(dsh_home) if dsh_home else Path.home() / ".dsh"
    scholar_home = Path(scholar_home) if scholar_home else Path(config.SCHOLAR_HOME)
    workspace = Path(workspace) if workspace else Path.cwd()
    py = str(python_cmd) if python_cmd else sys.executable
    patch = _patch_path(dsh_home, profile)
    dev_tree = _detect_dev_tree(scholar_home)
    token_ref = _validated_credential_ref(token_env)
    if code_stdin and code:
        raise typer.BadParameter("--code 与 --code-stdin 二选一")
    if api_key_stdin and (code_stdin or code):
        raise typer.BadParameter(
            "--api-key-stdin 不能与 --code 或 --code-stdin 同时使用"
        )
    legacy_capability = code_stdin or bool(code)
    if not legacy_capability and gateway is not None:
        raise typer.BadParameter("--gateway 仅用于兼容的 enrolment/capability 流程")
    gateway_url = _validated_remote_url(
        gateway or DEFAULT_GATEWAY_URL,
        allow_http=not legacy_capability or gateway is None,
    )
    if legacy_capability:
        code_value = sys.stdin.readline().rstrip("\r\n") if code_stdin else code.strip()
        if not code_value:
            raise typer.BadParameter("兑换码为空")
        console.print("[cyan]正在兑换 capability ...[/]")
        token_value, expires_at = _exchange_capability(gateway_url, code_value)
        console.print(
            f"[green][OK][/] capability 已签发（到期：{expires_at or '见 Hub'}）"
        )
    else:
        supplied_key = (
            sys.stdin.readline().rstrip("\r\n")
            if api_key_stdin
            else typer.prompt("请输入 Scholar Token", hide_input=True)
        )
        token_value = _validated_token(supplied_key)
        console.print("[cyan]正在验证 Token ...[/]")
        identity = _validate_token(gateway_url, token_value)
        console.print(
            f"[green][OK][/] Token 已验证（Token 名称：{identity['name']}）"
        )

    for action in _ensure_scholar_assets(scholar_home, include_runtime_dirs=False):
        console.print(f"[green][OK][/] {action}")
    problems = _preflight(scholar_home, py, remote=True)
    if problems:
        console.print("\n[red]前置校验未通过:[/]")
        for p in problems:
            console.print(f"  - {p}")
        raise typer.Exit(1)

    block = _build_patch_block(
        scholar_home,
        py,
        dev_tree,
        workspace=workspace,
        remote_url=gateway_url,
        token_ref=token_ref,
        allow_insecure_http=_requires_insecure_http(gateway_url),
    )
    patch_previous = patch.read_bytes() if patch.exists() else None
    preset_dir = _preset_dir(dsh_home)
    preset_previous = (
        {
            item.relative_to(preset_dir): item.read_bytes()
            for item in preset_dir.rglob("*")
            if item.is_file()
        }
        if preset_dir.exists()
        else None
    )
    try:
        action = _write_segment(patch, block)
        pdir = _write_preset(
            dsh_home,
            scholar_home,
            workspace,
            py,
            dev_tree,
            remote_url=gateway_url,
            token_ref=token_ref,
            allow_insecure_http=_requires_insecure_http(gateway_url),
        )
        rules_actions = _ensure_rules(scholar_home)
        _store_credential(dsh_home, token_ref, token_value)
    except Exception:
        if patch_previous is None:
            patch.unlink(missing_ok=True)
        else:
            patch.write_bytes(patch_previous)
        shutil.rmtree(preset_dir, ignore_errors=True)
        if preset_previous is not None:
            for relative, content in preset_previous.items():
                target = preset_dir / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
        raise

    console.print(f"[green][OK][/] {action} scholar block @ {patch}")
    console.print(f"[green][OK][/] academic preset written @ {pdir}")
    for r in rules_actions:
        console.print(f"[green][OK][/] {r}")
    if legacy_capability:
        console.print(
            "[green][OK][/] capability credential stored"
            "（到期自动失效后重跑本命令换新）"
        )
    else:
        console.print("[green][OK][/] Scholar Token 已保存为 Managed Credential")
    console.print(f"\n[bold]Proxy Hub 网关模式[/]：MCP 端点 = {gateway_url}")
    console.print(f"Bearer 凭据引用：{token_ref}")
    console.print("\n验证：")
    console.print('  dsh --profile headless "用 scholar 工具查一下知识库规模"')
    console.print("  Web UI → 设置 → Agent 预设 → 自定义 →「学术模式」开新会话")
