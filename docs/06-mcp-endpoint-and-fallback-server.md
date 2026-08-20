# 06 — MCP endpoint and custom wrapper server

> Deep dive on the pattern's Knowledge Base retrieval surface: how the native MCP endpoint works, how to check it's enabled on your Search service, and how the optional custom MCP server wrapper adds defense-in-depth on top of it. Referenced from [03-deployment.md Phase 3-4](03-deployment.md#phase-3--knowledge-base--mcp-endpoint) and [docs/00-reproduce-this-demo.md Parts C-D](00-reproduce-this-demo.md) — read this before running those phases if anything is unclear.

---

## 1 — Prerequisites checklist

Work through these in order:

### 1. Index populated

`idx-documents` must have documents ([03-deployment.md § Phase 2](03-deployment.md#phase-2--ingestion-data-source-skillset-index-indexer) complete) before a Knowledge Base is useful to create.

### 2. Chat model deployed

The Foundry account must have a chat-completion deployment (`chat`, e.g. `gpt-5-mini`) for the Knowledge Base's query planning — provisioned by `infra/modules/foundry.bicep` in Phase 1.

---

## 2 — Understanding the native MCP endpoint

Azure AI Search's **Knowledge Bases** (agentic retrieval) can expose their `retrieve` operation directly as an MCP server — a Streamable HTTP endpoint implementing the Model Context Protocol's JSON-RPC 2.0 transport, so any MCP client can call it with zero custom code.

**Conceptual shape** (verify exact path/API version at deployment time — see § 3):

```
POST https://{search-service}.search.windows.net/knowledgebases/{knowledgeBaseName}/mcp?api-version={api-version}
Authorization: Bearer {entra-id-token-scoped-to-https://search.azure.com/.default}     # recommended; api-key also accepted
Content-Type: application/json
```

The endpoint speaks MCP's standard lifecycle (`initialize`, `tools/list`, `tools/call`) and exposes a single tool that performs agentic retrieval against the configured index(es), returning grounded text with per-chunk citations.

> **This is a real, documented Azure AI Search capability** — Microsoft Learn states: *"each knowledge base is a standalone MCP server that exposes the `knowledge_base_retrieve` tool. Any MCP-compatible client, including Foundry Agent Service, GitHub Copilot, Claude, and Cursor, can invoke this tool."* The exact API version and regional rollout still move quickly (agentic retrieval was renamed from "Knowledge Agents" to "Knowledge Bases" in 2026), so always re-verify against the current Azure AI Search REST API reference before a customer-facing build — but do not treat the endpoint itself as speculative.

## 3 — Checking availability

`post_deploy_search.py --check-mcp-endpoint` (see [scripts/post_deploy_search.py](../scripts/post_deploy_search.py)) does the following:

1. Sends an MCP `initialize` request to `POST {searchEndpoint}/knowledgebases/{knowledgeBaseName}/mcp?api-version=2026-05-01-preview`
2. If it receives a valid MCP `initialize` response → records `mcpEndpointAvailability: "native"` in `demo-ids.local.json`
3. If it receives a 404, `FeatureNotEnabled`, or an unrecognized-path error → records `mcpEndpointAvailability: "wrapper-required"` and prints the custom wrapper deployment instructions

```powershell
python post_deploy_search.py --ids-file ../demo-ids.local.json --check-mcp-endpoint
```

If you get an ambiguous result, cross-check manually:

```powershell
# Confirm the Knowledge Base itself exists and responds to a direct retrieve call
python post_deploy_search.py --ids-file ../demo-ids.local.json --test-retrieve --query "test question your corpus can answer"
```

If the direct `retrieve` call works but the `/mcp` path 404s, the Knowledge Base is fine — only the MCP surface specifically isn't enabled yet on your service/region/API version. This is exactly the case the optional custom wrapper server exists for.

## 4 — The optional custom wrapper server

`scripts/mcp_fallback_server.py` is a small Python MCP server (built on the official `mcp` SDK's `FastMCP` helper) that exposes one tool with the same contract as the native endpoint. It is **not required** to get a working demo — it's a defense-in-depth option for token-lifecycle management, custom pre/post-processing of results, IP allowlisting via Container Apps networking, or as an extension point for future multi-tool federation:

```python
async def retrieve_documents(query: str, top_k: int = 5) -> str:
    """Search the indexed document corpus and return grounded, cited results."""

# Registered with a configurable name/description so one server serves any corpus:
mcp.add_tool(retrieve_documents, name=MCP_TOOL_NAME, description=MCP_TOOL_DESCRIPTION)
```

`MCP_TOOL_NAME` / `MCP_TOOL_DESCRIPTION` come from the `corpus` block in `demo-ids.local.json` (passed through as Container App env vars by `deploy_mcp_server.ps1`). **Set the description to reflect the actual corpus** — GitHub Copilot decides whether to invoke the tool from that text, so a generic description gets skipped for domain questions. See [01-architecture.md § Adapting this pattern to another document corpus](01-architecture.md#adapting-this-pattern-to-another-document-corpus).

Internally, it:

1. First tries the AI Search Knowledge Base's direct `retrieve` REST operation (`POST /knowledgebases/{name}/retrieve?api-version=...`) — this is a stable (non-MCP) surface, so it works even where the `/mcp` path doesn't
2. If no Knowledge Base is provisioned at all, falls back further to a plain hybrid + semantic query directly against `idx-documents` (`POST /indexes('{name}')/docs/search?api-version=...` with `queryType: semantic` and a vector query)
3. Formats the result as grounded text with inline citations (`[sourceDocument — sectionH1 > sectionH2 > sectionH3]`)

This gives the pattern **two levels of redundancy**: native MCP → Knowledge Base retrieve via custom wrapper → raw hybrid search via custom wrapper. A working demo is possible even on a Search service with only baseline Basic-tier capabilities.

Run it locally over stdio for single-developer testing:

```powershell
cd scripts
python mcp_fallback_server.py --transport stdio
```

Or over Streamable HTTP (what Container Apps hosting uses):

```powershell
python mcp_fallback_server.py --transport streamable-http --port 8080
```

## 5 — Deploying the custom wrapper server to Azure Container Apps

```powershell
cd scripts
./deploy_mcp_server.ps1 -IdsFile ../demo-ids.local.json
```

This script:

1. Builds the container image from `scripts/` via `az acr build` (no local Docker required)
2. Deploys/updates the Container App in the environment provisioned by `infra/modules/containerapp.bicep` (only created when `deploy.ps1 -DeployFallbackServer` was run in Phase 1/Part D)
3. Wires the Search endpoint + Key Vault reference for the admin/query key as Container App secrets — never as plaintext env vars
4. Writes the resulting Container App FQDN into `demo-ids.local.json`

## Verification

```powershell
# From any machine with network access to the Container App
curl -X POST https://<container-app-fqdn>/mcp `
  -H "Content-Type: application/json" `
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

A healthy response lists your configured tool (`corpus.mcpToolName` — `retrieve_technical_docs` in the shipped example). If this fails, see [05-troubleshooting.md § 4](05-troubleshooting.md#4--mcp-endpoint-native-or-fallback).

## Troubleshooting

| Symptom | Root cause | Fix |
|---|---|---|
| `/mcp` path returns 404 on the native endpoint | MCP surface not enabled on this Search service/tier/region/API version | Deploy the optional custom wrapper; periodically re-check native availability |
| Custom wrapper's Knowledge Base call fails with 401 | Search admin key rotated or Key Vault reference misconfigured | Re-check the Container App's Key Vault secret reference and the key's current value |
| Custom wrapper falls all the way through to raw hybrid search unexpectedly | Knowledge Base wasn't created, or its name doesn't match `knowledgeBaseName` in `demo-ids.local.json` | Re-run `post_deploy_search.py --create-knowledge-base` and confirm the name matches |
| Both native and wrapper return different citations for the same question | One is querying a stale index snapshot or a differently-configured Knowledge Base | Confirm both point at the same `searchIndexName` / `knowledgeBaseName`; re-run the indexer if the corpus changed recently |

---

*Last updated: 2026-08-18*
