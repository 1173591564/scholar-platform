# Scholar Studio

English | [中文](README.zh.md)

Scholar Studio is a Python academic-research engine with a 48-command CLI, a
16-tool MCP server, XML-first evidence retrieval, PostgreSQL/pgvector
projections, academic writing support, experiment helpers, and optional Lean4
synchronization.

## Architecture

DeepSeek Harness is the independently distributed user client. Its academic mode owns the user-facing workflows, skills, plugins, local dashboard, and MCP integration. This repository is the server product: `scholar/` owns the academic data plane, `scholar_mcp/` adapts it to MCP, and `services/proxy-hub/` contains the Proxy Hub backend and its operator administration frontend.

Phase One keeps the direct authenticated DSH-to-Scholar path. Phase Two adds a Proxy Hub backend and a same-origin operator administration frontend in this repository without moving tenant policy into Scholar or Hub code into DSH. See [the architecture map](docs/architecture.md), [minimum Proxy Hub interface](docs/proxy-hub.md), and [management console design](docs/proxy-hub-console.md).

## Repository layout

| Path | Responsibility |
| --- | --- |
| `scholar/` | Academic domain logic, CLI, and packaged templates |
| `scholar_mcp/` | Fixed MCP tool surface and transports |
| `services/proxy-hub/` | Independent Proxy Hub backend and operator console |
| `infra/` | Deployment assets grouped by service |
| `tests/` | Scholar and MCP regression tests |
| `.scholar/` | Canonical shared IDE templates |
| `.qoder/`, `.claude/` | Generated IDE projections |

`scholar/templates/` is the package-distribution mirror of `.scholar/` plus package-only DSH assets. Run `make sync-templates` after editing canonical templates; CI enforces `make check-templates`.

## Installation

Scholar Studio requires Python 3.10 or newer.

```sh
python -m pip install .
scholar init
scholar doctor
```

`scholar init` installs fixed local rules and 15 skills under the configured Scholar home without overwriting user-modified files. The wheel contains code and templates, not the paper corpus.

## Corpus ownership

- A remote Streamable HTTP deployment owns its central versioned corpus, database, embeddings, and vector indexes.
- A local stdio deployment may use an independently distributed and verified data pack.
- Clients do not synchronize corpus files or vector indexes from the server.
- Remote clients do not need database or embedding-provider credentials.
- Immutable LaTeXML is the corpus authority; relational, vector, and graph
  data are rebuildable PostgreSQL projections selected by a serving snapshot.

See [Scholar v2 operations](docs/scholar-v2.md).

## Parsed artifacts

TeX parsing produces a schema-validated vNext artifact with parser lineage, source-file hashes, source-qualified metadata assertions, and structured warning/loss diagnostics. `save_parsed()` stores the vNext artifact under `parsed/vnext/` and writes an explicit legacy projection at the existing `parsed/<paper_id>.json` location, so current MCP and retrieval readers remain stable while evidence fields evolve.

The committed synthetic fixture corpus and golden artifacts cover nested inputs, missing inputs, multilingual text, formula-prefix collisions, uncited bibliography entries, and comma-formatted BibTeX authors.

## MCP server

Start local stdio transport:

```sh
python -m scholar_mcp
```

Start Streamable HTTP transport with authentication:

```sh
SCHOLAR_MCP_TRANSPORT=streamable-http \
SCHOLAR_MCP_HOST=127.0.0.1 \
SCHOLAR_MCP_PORT=8000 \
SCHOLAR_MCP_TOKEN='managed-secret' \
SCHOLAR_WORKSPACE_ISOLATION=shared \
python -m scholar_mcp
```

Non-loopback HTTP requires a Bearer token. Explicit loopback no-auth mode (`SCHOLAR_MCP_ALLOW_INSECURE_LOOPBACK=1`) is limited to local development or an SSH tunnel. Model-facing errors omit filesystem paths, credentials, database diagnostics, and provider details.
The authenticated private readiness endpoint reports only the active schema,
release, snapshot, sealed builds, and degraded projection capabilities.

The MCP server publishes exactly these 16 tools:

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

## DSH integration

Install the academic preset and headless patch:

```sh
scholar init-dsh
```

Configure direct remote operation without writing a literal token into YAML or process arguments:

```sh
printf '%s\n' "$SCHOLAR_REMOTE_TOKEN" \
  | scholar init-dsh \
      --remote https://scholar.example/mcp \
      --token-stdin
```

The generated DSH composition uses `@deepseek-ai/dsh-mcp-client`, `@deepseek-ai/dsh-scholar-native`, and `@deepseek-ai/dsh-skill-filesystem`. Local stdio startup remains strict. Remote startup keeps the composition loaded during a temporary outage and reconnects after the service recovers or a configured replacement credential is stored. The token is stored in DSH managed credentials and the composition contains only its reference.

## CLI

Use `scholar --help` for the authoritative 48-command catalog. Common operations include:

```sh
scholar stats
scholar search "retrieval augmented generation"
scholar vec-search "How do graph retrievers improve grounding?"
scholar info <paper_id>
scholar graph-stats
scholar sync
```

Semantic search reports provider, database, and projection unavailability
separately from a legitimate zero-result response.

## Development

```sh
make install-dev
docker compose -f infra/scholar/compose.yml up -d
make check
python -m pip wheel . --no-deps -w dist
```

The root `Makefile` also exposes focused checks for Scholar, the Proxy Hub backend, the Proxy Hub frontend, and generated templates.

Tests cover path containment, malformed paper data, parser golden artifacts, graph/index invalidation, authentication, MCP initialization, the 16-tool catalog, lexical and semantic search, scoped passage SQL against pgvector PostgreSQL, DSH clean-home installation, credential storage, permissions, existing-file preservation, and rollback.

## Phase boundary

Phase one is a direct authenticated client-to-Scholar-server product. Multi-tenant authorization, team policy, quotas, centralized audit, and corpus isolation are deferred to the Proxy Hub control plane.
