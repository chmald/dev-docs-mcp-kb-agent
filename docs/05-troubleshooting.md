# 05 — Troubleshooting

Common failure modes and fixes for the Developer Docs MCP Knowledge Base pattern. Organized by where the symptom appears.

## Quick triage table

| Symptom | Most likely root cause | Section |
|---|---|---|
| Bicep deploy fails on the Foundry/Cognitive Services module | Model not available in the target region, or quota exhausted | § 1 |
| Indexer run shows failed documents | PDF is scanned/image-only with no extractable layer, or exceeds size/page limits | § 2 |
| Indexer fails on every document (`file_data` missing) | Indexer lacks `allowSkillsetToReadFileData: true` | § 2 |
| Indexer stops after ~20 documents ("Time Out") | Skillset has no billable AI Services attachment | § 2 |
| Chunks missing `sectionH1` / `sectionH2` | Split skill isn't reading the Layout skill's structured output correctly, or the doc has no headings at that depth | § 2 |
| Citations come back with empty source data | `includeReferenceSourceData` not set, or field missing from the knowledge source's `sourceDataFields` | § 2 |
| Knowledge Base `retrieve` call returns empty/low-quality results | Semantic ranker not enabled, or query planning model not deployed | § 3 |
| MCP endpoint check returns 404 / `FeatureNotEnabled` | Native Knowledge Base MCP surface not available on this Search tier/region/API version | § 3, § 4 |
| Fallback Container App fails to start | Missing Key Vault reference, wrong managed identity role assignment | § 4 |
| VS Code doesn't show the MCP server as connected | `.vscode/mcp.json` syntax error, wrong endpoint URL, or auth header missing | § 5 |
| Copilot Chat never calls the tool | Tool description too vague, or GitHub Copilot's agent mode / MCP support not enabled | § 5 |
| Answers cite the wrong page or fabricate content | Chunking split a table/definition across chunks, or golden-set threshold not yet tuned | § 6 |

---

## 1 — Foundation (Bicep deploy)

**Symptom: `foundry.bicep` deployment fails with a model-capacity or region error.**
Check `az cognitiveservices account list-models --name <account> --resource-group <rg>` for `text-embedding-3-large` and your chosen chat model in the target region. If unavailable, redeploy in a Tier-1/2 region from [02-prerequisites.md § 8](./02-prerequisites.md#8--regional--preview-feature-availability).

**Symptom: `AuthOptions must be null if DisableLocalAuth is true` on the Search module.**
Don't set both `authOptions` and `disableLocalAuth: true` in `search.bicep` — they're mutually exclusive on the AI Search ARM API. Pick one auth model.

## 2 — Ingestion (indexer / skillset)

**Symptom: indexer reports failed documents.**
Most common cause: a scanned/image-only PDF with no text layer that the Layout model can't structure usefully, or a PDF exceeding AI Search's per-document size/page limits. Check the indexer execution history for the specific error per document; consider pre-splitting oversized manuals.

**Symptom: indexer fails on every document with a missing `file_data` / input error.**
The Document Layout skill reads `/document/file_data`, which only exists when the indexer sets `"allowSkillsetToReadFileData": true` in `parameters.configuration`. Confirm `create_indexer` in `scripts/post_deploy_search.py` still sets it (alongside `"parsingMode": "default"`), then re-create the indexer and re-run.

**Symptom: indexer stops after ~20 documents with a "Time Out" message.**
The Layout skill is billable and the skillset has no AI Services attachment, so it's running on the free daily enrichment allowance. Confirm the skillset's `cognitiveServices` block is present and its `subdomainUrl` points at your Foundry resource, and that the Search service's managed identity holds **Cognitive Services User** on it.

**Symptom: chunks are missing `sectionH1` / `sectionH2` / `sectionH3`.**
First check whether the source document actually has headings at that depth — the Layout skill's `markdownHeaderDepth` is set to `h3`, so deeper headings collapse and documents with no heading structure produce empty values. If headings clearly exist, confirm the index projection mappings still point at `/document/markdownDocument/*/sections/h1` (etc.) in `scripts/post_deploy_search.py` — projections that reference a nonexistent path write nulls silently rather than erroring.

**Symptom: citations come back with empty source data.**
`references[].sourceData` is `null` unless the retrieve request sets `includeReferenceSourceData: true` in `knowledgeSourceParams`, AND the knowledge source declares the field in `searchIndexParameters.sourceDataFields`. Both are required — check `retrieve_body` and `create_knowledge_source`.

## 3 — Knowledge Base / retrieval quality

**Symptom: `retrieve` calls return empty or clearly irrelevant results.**
- Confirm semantic ranker is enabled on the Search service (a per-service setting — see [02-prerequisites.md § 3](./02-prerequisites.md#3--azure-ai-search))
- Confirm the Knowledge Base's query-planning chat model deployment exists and is reachable
- Re-run a manual hybrid query directly against `idx-documents` in Search Explorer to isolate whether the problem is indexing or agent configuration

## 4 — MCP endpoint (native or fallback)

**Symptom: native MCP endpoint check returns 404 or a feature-not-enabled error.**
This is expected on Search services/regions where the MCP endpoint hasn't rolled out yet, or where the API version has moved on since this pattern was last verified. Proceed with the optional custom wrapper server (Phase 4) — this is exactly what it's for. Re-check availability periodically; see [docs/06](./06-mcp-endpoint-and-fallback-server.md) for the current verification command.

**Symptom: fallback Container App fails to start / crash-loops.**
Check `az containerapp logs show`. Most common causes: a Key Vault reference the Container App's managed identity can't read (missing `Key Vault Secrets User` role), or a missing environment variable for the Search endpoint/key.

## 5 — GitHub Copilot / VS Code MCP client

**Symptom: VS Code doesn't show the MCP server as connected.**
Validate `.vscode/mcp.json` against the schema in [docs/07-github-copilot-mcp-client-setup.md](./07-github-copilot-mcp-client-setup.md) — a common mistake is a trailing comma or wrong `type` value. Reload the VS Code window after any change.

**Symptom: Copilot Chat never invokes the tool even though it's connected.**
Make the MCP tool's description more specific to the corpus domain — set `corpus.mcpToolDescription` in `demo-ids.local.json` (no code edit required) and redeploy the wrapper. Name the document types *and* the question types it answers (e.g. "Search the hardware datasheet and firmware reference documentation for register values, pin configuration, and timing characteristics") rather than a generic "search documents". Confirm GitHub Copilot's agent mode is enabled for the workspace.

## 6 — Answer quality

**Symptom: answers cite the wrong page, or invent content not in the corpus.**
Almost always a chunking issue — a register table or multi-step procedure split across two chunks loses context. Reduce chunk size or increase overlap in the Split skill config, re-run the indexer, and re-test against the golden set ([04-testing.md § C](./04-testing.md#c--quality-golden-set)).

## 7 — Cost and quota

**Symptom: unexpected AI Search or Azure OpenAI cost.**
Check the indexer schedule isn't re-processing the full corpus more often than needed; check the fallback MCP server's Container App scale-to-zero setting is enabled for low-traffic POC use.

## 8 — Diagnostic toolbox

### 8.1 Check logs in this order

1. AI Search indexer execution history (portal → Search service → Indexers → the indexer → execution history)
2. `az containerapp logs show` for the fallback MCP server (if deployed)
3. VS Code's "MCP: Show Output" command for the client-side connection log

### 8.2 Useful queries

```powershell
# Indexer status
python scripts/post_deploy_search.py --ids-file demo-ids.local.json --indexer-status

# Manual retrieve call against the Knowledge Base
python scripts/post_deploy_search.py --ids-file demo-ids.local.json --test-retrieve --query "your test question"
```

## 9 — When to escalate

| Issue | Escalate to |
|---|---|
| Azure AI Search Knowledge Base / MCP behavior differs from this pattern's documentation | Re-check Microsoft Learn / release notes first — this surface evolved quickly (renamed from "Knowledge Agents" in 2026) and may have moved again since this pattern was last verified, not necessarily a bug |
| Persistent quota denial after a support request | Azure support ticket via the portal |
| GitHub Copilot agent-mode / MCP client behavior issues unrelated to this pattern's server | GitHub Copilot support / VS Code issue tracker |

---

*Last updated: 2026-08-18*
