# Scholar Studio

[English](README.md) | 中文

Scholar Studio 是 Python 学术研究引擎，提供 48-command CLI、16-tool MCP server、15 个本地 research skill、lexical 与 semantic retrieval、内存 citation/concept graph、论文解析、学术写作支持、实验辅助与可选 Lean4 synchronization。

## 架构

DeepSeek Harness 是独立分发给用户的客户端，其学术模式负责用户侧工作流、skills、插件、本地 Dashboard 与 MCP 集成。本仓库是服务器端产品：`scholar/` 负责学术 data plane，`scholar_mcp/` 负责 MCP adapter，`services/proxy-hub/` 将同时包含 Proxy Hub backend 与 operator 管理前端。

第一阶段保留 authenticated DSH-to-Scholar direct path。第二阶段在本仓库新增 Proxy Hub backend 与同源 operator 管理前端，不把 tenant policy 放进 Scholar，也不把 Hub code 放进 DSH。详见[架构图](docs/architecture.md)、[最小 Proxy Hub 接口](docs/proxy-hub.md)与[管理控制台设计](docs/proxy-hub-console.md)。

## 仓库布局

| 路径 | 职责 |
| --- | --- |
| `scholar/` | 学术领域逻辑、CLI 与打包模板 |
| `scholar_mcp/` | 固定 MCP 工具面与 transport |
| `services/proxy-hub/` | 独立的 Proxy Hub backend 与 operator console |
| `infra/` | 按服务划分的部署资源 |
| `tests/` | Scholar 与 MCP 回归测试 |
| `.scholar/` | 共享 IDE 模板的唯一源 |
| `.qoder/`、`.claude/` | 生成的 IDE 投影 |

`scholar/templates/` 是 `.scholar/` 的 package-distribution 镜像，并额外包含 package-only DSH assets。修改源模板后运行 `make sync-templates`；CI 通过 `make check-templates` 防止漂移。

## 安装

Scholar Studio 需要 Python 3.10 或更高版本。

```sh
python -m pip install .
scholar init
scholar doctor
```

`scholar init` 会在配置的 Scholar home 下安装固定本地 rule 与 15 个 skill，不覆盖用户修改的文件。Wheel 包含代码与 template，不包含论文 corpus。

## Corpus 所有权

- Remote Streamable HTTP deployment 拥有 central versioned corpus、database、embedding 与 vector index。
- Local stdio deployment 可以使用独立分发并校验的 data pack。
- Client 不从 server 同步 corpus file 或 vector index。
- Remote client 不需要 database 或 embedding-provider credential。

## 解析产物

TeX parser 生成通过 schema 校验的 vNext artifact，包含 parser lineage、source-file hash、带来源的 metadata assertion，以及结构化 warning/loss diagnostic。`save_parsed()` 将 vNext artifact 保存到 `parsed/vnext/`，并在原有 `parsed/<paper_id>.json` 位置写入显式 legacy projection，从而让现有 MCP 与 retrieval reader 保持稳定，同时继续扩展 evidence 字段。

仓库内的合成 fixture corpus 与 golden artifact 覆盖 nested input、missing input、多语言文本、formula prefix collision、未引用 bibliography entry，以及逗号格式的 BibTeX author。

## MCP server

启动 local stdio transport：

```sh
python -m scholar_mcp
```

启动带 authentication 的 Streamable HTTP transport：

```sh
SCHOLAR_MCP_TRANSPORT=streamable-http \
SCHOLAR_MCP_HOST=127.0.0.1 \
SCHOLAR_MCP_PORT=8000 \
SCHOLAR_MCP_TOKEN='managed-secret' \
python -m scholar_mcp
```

Non-loopback HTTP 必须使用 Bearer token。显式 loopback no-auth 模式（`SCHOLAR_MCP_ALLOW_INSECURE_LOOPBACK=1`）仅用于本地开发或 SSH tunnel。Model-facing error 不包含 filesystem path、credential、database diagnostic 或 provider detail。

MCP server 精确发布以下 16 个工具：

1. `scholar_search`
2. `scholar_vec_search`
3. `scholar_info`
4. `scholar_section`
5. `scholar_passages`
6. `scholar_cite_network`
7. `scholar_graph_query`
8. `scholar_lineage`
9. `scholar_graph_stats`
10. `scholar_list_papers`
11. `scholar_arxiv_search`
12. `read_parsed_paper`
13. `scholar_read_output_file`
14. `read_skill`
15. `scholar_auto_notes`
16. `scholar_interests`

## DSH 集成

安装 academic preset 与 headless patch：

```sh
scholar init-dsh
```

配置 direct remote operation，且不将 literal token 写入 YAML 或 process argument：

```sh
printf '%s\n' "$SCHOLAR_REMOTE_TOKEN" \
  | scholar init-dsh \
      --remote https://scholar.example/mcp \
      --token-stdin
```

生成的 DSH composition 使用 `@deepseek-ai/dsh-mcp-client`、`@deepseek-ai/dsh-scholar-native` 与 `@deepseek-ai/dsh-skill-filesystem`。本地 stdio 启动仍为严格模式；远程启动在临时停机期间保持 composition 加载，并在服务恢复或保存已配置的替换 credential 后重连。Token 存入 DSH managed credential，composition 只包含其 reference。

## CLI

使用 `scholar --help` 查看权威 48-command catalog。常用操作包括：

```sh
scholar stats
scholar search "retrieval augmented generation"
scholar vec-search "How do graph retrievers improve grounding?"
scholar info <paper_id>
scholar graph-stats
scholar sync
```

Semantic search 会将 provider、database 与 index unavailable 分别报告，不与合法 zero-result response 混淆。Graph 与 vector cache 会在 authoritative corpus metadata 变化时刷新。

## 开发

```sh
make install-dev
docker compose -f infra/scholar/compose.yml up -d
make check
python -m pip wheel . --no-deps -w dist
```

根目录 `Makefile` 也提供 Scholar、Proxy Hub backend、Proxy Hub frontend 与生成模板的独立检查入口。

Tests 覆盖 path containment、malformed paper data、parser golden artifact、graph/index invalidation、authentication、MCP initialization、16-tool catalog、lexical 与 semantic search、pgvector PostgreSQL scoped passage SQL、DSH clean-home installation、credential storage、permission、existing-file preservation 与 rollback。

## 阶段边界

第一阶段是 direct authenticated client-to-Scholar-server product。Multi-tenant authorization、team policy、quota、centralized audit 与 corpus isolation 延期到 Proxy Hub control plane。
