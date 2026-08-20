# Changelog — Developer Docs MCP Knowledge Base Pattern

Change history for this pattern. Entries are listed newest-first.

---

## 2026-08-18

### Azure AI Search API-contract corrections (v1.0.4)

A validation pass over `scripts/post_deploy_search.py` — six issues found by static analysis, then all confirmed plus three more by a cited Microsoft Learn review. **The pipeline as previously written would not have worked end to end.** Every fix below is verified against current documentation.

| # | Bug | Impact if unfixed |
|---|---|---|
| 1 | **`parent_id` field missing from the index** while the skillset set `parentKeyFieldName: "parent_id"` | Index projections fail — no chunks land |
| 2 | **Key field lacked `analyzer: "keyword"` + `searchable`** | Default analyzer tokenizes chunk IDs; key lookups and projection dedupe misbehave silently |
| 3 | **Semantic config named a non-searchable field** (`sourceDocument`) **and used `contentFields`** instead of `prioritizedContentFields` | HTTP 400 — index creation rejected outright |
| 4 | **Indexer missing `allowSkillsetToReadFileData: true`** (and `parsingMode`) | `/document/file_data` never exists; Layout skill fails on the first document |
| 5 | **Skillset missing the `cognitiveServices` attachment** | Billable Layout skill stops after 20 enrichments/run/day with a "Time Out" |
| 6 | **Index projections mapped invented paths** `/markdownDocument/*/pageNumber` and `/*/sectionHeading` | Projections write silent nulls — citations always empty |
| 7 | **Knowledge Base payload used `targetIndexes` + `defaultRerankerThreshold`** | Neither property exists; agentic retrieval now needs a separate **knowledge source** object created first |
| 8 | **Retrieve call omitted `knowledgeSourceParams`** | `references[].sourceData` returns `null`; all citation hydration reads from an empty object |
| 9 | Data source connection string missing the documented trailing `/` | Cosmetic; corrected to the canonical form |

**Schema change — citations are now heading paths, not page numbers.** The Document Layout skill in markdown mode emits `content` + `sections` (h1–h6) + `ordinal_position` per section; **it does not emit page numbers**. The index's `pageNumber`/`sectionHeading` fields were therefore unfillable. They are replaced by `sectionH1` / `sectionH2` / `sectionH3` mapped from `sections/h1|h2|h3` (mirroring Microsoft's canonical semantic-chunking sample), and citations now render as `document — Peripherals > Timer Registers`. Heading paths are also more stable than page numbers across document revisions. `docs/01-architecture.md` documents the `outputFormat: "text"` + `locationMetadata` alternative for engagements where page-level citation is a hard requirement. The dead `lastIndexed` field (declared, never mapped) was dropped.

**Also fixed:** the embedding skill now sets `dimensions` (must match the index vector width); the Split skill declares `azureOpenAITokenizerParameters` (`cl100k_base`); `deploy_mcp_server.ps1` was still setting `KNOWLEDGE_AGENT_NAME=$($ids.knowledgeAgentName)` — both sides dead since the rename, which would have silently degraded the wrapper to raw hybrid search.

**Test suite: 19 → 29.** Ten new API-contract regression tests, one per bug class above — including one that asserts the Layout skill's invented output paths never come back. Notably, the previous reusability guard asserted the *exact* index field set and therefore **locked in bug #1**; it now asserts the corrected set, and a dedicated test verifies `parent_id` exists, is `Edm.String`, is filterable, and matches the skillset's `parentKeyFieldName`.

Verified: pytest 29/29, all scripts compile, `demo-ids.template.json` valid, all markdown links + anchors resolve.

### Corpus-neutral refactor — same pipeline, any document domain (v1.0.3)

Review found the pipeline was *structurally* generic but *cosmetically* pinned to the hardware/firmware example: dev-docs-flavored resource defaults, a hardcoded MCP tool name and hardware-specific tool description, hardcoded chunking and embedding dimensions, and a PDF-only uploader. Retargeting it at another corpus (HR policies, contracts, research papers) would have meant editing code in four files. Now it's a config change.

**One config block, `corpus` in `demo-ids.local.json`, is the only corpus-specific surface:**

| Setting | Drives |
|---|---|
| `displayName` | Skillset description |
| `mcpToolName` / `mcpToolDescription` | The MCP tool's advertised identity — **the highest-impact setting**, since GitHub Copilot decides whether to invoke a tool from its description |
| `chunkSizeTokens` / `chunkOverlapTokens` | Split skill sizing (dense tables vs. long-form prose want different values) |
| `sourceFileExtensions` | Which files `upload_documents.py` uploads |

**Changes:**
- **Generic resource defaults**: `idx-dev-docs`→`idx-documents`, `kb-dev-docs`→`kb-documents`, `skillset-dev-docs`→`skillset-documents`, `ds-dev-docs-blob`→`ds-documents-blob`, `ixr-dev-docs`→`ixr-documents`. All still overridable per deployment.
- **Index-scoped config names are now fixed generic constants** (`vector-profile`, `hnsw-algorithm`, `vectorizer-embedding`, `semantic-config`). These are scoped *within* an index so they never collide across corpora — hardcoding a neutral constant removes the "renamed the index but forgot the semantic config" class of bug. Fixed a live instance of exactly that: `mcp_fallback_server.py` was querying the hardcoded `semantic-dev-docs` config, which would break for any customized index.
- **MCP tool identity is now configurable** — the server registers via `mcp.add_tool(fn, name=MCP_TOOL_NAME, description=MCP_TOOL_DESCRIPTION)` instead of a decorator with a baked-in hardware-specific docstring. `deploy_mcp_server.ps1` passes both through from the `corpus` block as Container App env vars.
- **`embeddingDimensions` is configurable** (default 3072) — a hardcoded value fails index creation for anyone deploying `text-embedding-3-small` (1536) or another model.
- **Uploader is no longer PDF-only** — `corpus.sourceFileExtensions` drives it, so Office formats and images (which the Layout skill already reads) work without a code edit.
- **Fixed a stale-rename bug**: `deploy_mcp_server.ps1` was still setting `KNOWLEDGE_AGENT_NAME=$($ids.knowledgeAgentName)` — both the env var and the ids field were dead after the 2026-08-18 Knowledge Base rename, so the deployed wrapper would have silently fallen back to raw hybrid search instead of using the Knowledge Base.
- **Reusability guards added to the test suite** (12 → 19 tests): the index schema is asserted to contain no domain terms and exactly the neutral field set; embedding dimensions and chunk sizing are asserted configurable; the MCP tool name/description are asserted configurable and neutral-by-default; the wrapper's semantic-config name is asserted to match the index builder's constant; and an end-to-end test retargets the whole pipeline at an "HR policies" corpus and asserts nothing dev-docs-flavored survives in the emitted payloads.
- **Docs**: new [Adapting this pattern to another document corpus](docs/01-architecture.md#adapting-this-pattern-to-another-document-corpus) section (what to change, what stays fixed, and when to add domain fields); `samples/README.md` rewritten corpus-neutral with file-type and chunk-tuning guidance; README, troubleshooting, and MCP-client docs updated to point at the config settings rather than code edits.

The hardware/firmware framing remains throughout the docs as the shipped **example** corpus — it's now explicitly labeled as an example rather than reading as a constraint. Re-verified: pytest 19/19, `az bicep build` clean, all markdown links/anchors resolve.

### Structural + Microsoft Learn accuracy correction (v1.0.2)

A second-pass review found the docs/code split the wrong way (5 core docs at the repo root, deep-dives in `docs/`) and, more seriously, that several of the pattern's core technical claims had drifted from or never matched current Microsoft documentation. Corrected both, and added the manual deployment path this pattern was missing.

**Documentation restructure:**
- Moved `01-architecture.md` through `05-troubleshooting.md` from the repo root into `docs/`, alongside the existing `docs/00`, `docs/06`, `docs/07`, and `docs/assets/` — the repo root now holds only `README.md` plus ADO/IaC/code scaffolding. Rewrote every cross-reference (root ↔ docs ↔ samples ↔ tests/fixtures) accordingly; verified programmatically that every link and `#anchor` resolves.
- Added `docs/03b-manual-deployment.md` — a complete Azure Portal + imperative `az` CLI alternative to the Bicep path in `03-deployment.md`, for customers who can't or won't use IaC. Produces the same resource names/shapes/RBAC as `infra/main.bicep`; Phases 2/3/5 point back to the Bicep doc since those phases are already infrastructure-agnostic.

**Microsoft Learn accuracy corrections** (via a cited research pass against current Azure documentation):
- **Renamed "Knowledge Agent" → "Knowledge Base"** everywhere (docs, code identifiers, resource names `ka-dev-docs`→`kb-documents`, env vars, CLI flags) — Microsoft renamed the underlying Azure AI Search agentic-retrieval concept in 2026.
- **Fixed the native MCP endpoint** — the pattern's original URL shape (`/agents('{name}')/mcp`, API version `2025-08-01-preview`) was fabricated/outdated. Corrected to the actual documented endpoint: `/knowledgebases/{name}/mcp?api-version=2026-05-01-preview`, exposing tool `knowledge_base_retrieve`. The native endpoint is confirmed **real and GA/preview per Microsoft Learn**, not a speculative feature — reframed the custom Python MCP server from a "required fallback" to an **optional wrapper** (defense-in-depth: token lifecycle, custom processing, network controls) throughout every doc and code comment.
- **Lowered the AI Search tier floor** from "Standard S1 minimum" to **Basic minimum** (Standard S1+ still recommended for production scale/concurrency) — semantic ranker and Knowledge Bases both run on Basic. Updated the Bicep default (`infra/modules/search.bicep`, `infra/main.bicep`) and the POC cost estimate (~$120-175/mo on Basic vs. ~$300-350/mo on Standard S1).
- **Replaced the deprecating `gpt-4o-mini`** (Microsoft retires it 2026-10-01) **with `gpt-5-mini`** (version `2025-08-07`, GA) as the default query-planning chat model, across Bicep defaults, scripts, and docs. Added a model-currency callout pointing at the current Foundry model catalog.
- **Added the Entra ID bearer-token auth option** (`https://search.azure.com/.default` scope + `Search Index Data Reader` RBAC) as Microsoft's recommended production auth for the native endpoint, alongside the existing API-key option, in `01-architecture.md` and the `.vscode/mcp.json` examples in `docs/07`. Noted the VS Code Agent Host portability caveat for `${input:...}`-based secrets.
- Confirmed accurate and left unchanged: the Document Intelligence Layout skill (`#Microsoft.Skills.Util.DocumentIntelligenceLayoutSkill`), integrated vectorization, the `Microsoft.CognitiveServices/accounts` kind `AIServices` Foundry resource shape, and the VS Code `.vscode/mcp.json` schema itself.

Re-verified after all changes: `az bicep build` compiles clean, and the pytest suite still passes 12/12 — no functional regressions from either the restructure or the terminology/endpoint corrections.

See `Decisions/_Process/2026-08-18 - dev-docs-mcp-knowledge-agent-accuracy-and-structure-fix.md` (vault) for the full decision record.

### Template-completeness pass (no architectural change)

Audited `docs/` against the demo-pattern-authoring template and the sibling `secure-mcp-gateway-landing-zone` pattern. Found and fixed three gaps:

- Added `docs/assets/dev-docs-mcp-knowledge-agent-architecture.drawio` — a presentation-ready companion to the inline Mermaid diagram in `01-architecture.md` (which was already complete; only the customer-deck-ready version was missing). Cross-referenced from `01-architecture.md` and the README file index.
- `tests/fixtures/` was an empty directory — git doesn't track empty folders, so it would have silently disappeared on first ADO check-in even though `04-testing.md` documents it as part of the layout. Added `tests/fixtures/README.md` explaining the "bring your own PDF" placeholder.
- `.gitignore` had no guard against committing a real (possibly copyrighted) test datasheet dropped into `tests/fixtures/` or `samples/`. Added `*.pdf` exclusions for both.

Verified alongside the fix: all cross-references in the 6 root docs + 3 `docs/` files resolve cleanly (no dead links), and the pytest suite still passes 12/12 (`tests/test_post_deploy_search.py`, `tests/test_mcp_fallback_server.py`) — no code drift since 2026-08-13.

---

## 2026-08-13

### v1 — Locked architecture decisions

Initial pattern authored: Blob Storage → AI Search indexer/skillset (Document Intelligence Layout skill + integrated vectorization) → AI Search Knowledge Base (agentic retrieval) → dual MCP exposure (native Knowledge Base MCP endpoint, primary; thin custom Python MCP server on Azure Container Apps, fallback) → GitHub Copilot in VS Code as the sole v1 consumption surface. Bicep IaC (Storage, Foundry multi-service account, Search, Key Vault, RBAC, optional Container App), two Python setup scripts, ADO pipeline, and pytest smoke tests.

Originating engagement: Insulet — Document Intelligence Developer Agent (Discovery stage). Built as the technical evaluation vehicle for whether a Document Intelligence/RAG-based shareable developer agent is warranted versus native GitHub Copilot alone, per the 2026-08-12 discovery discussion.

Honesty note carried through the docs: the native AI Search Knowledge Base → MCP endpoint surface is a fast-moving preview capability at authoring time — every reference to its REST path/API version is flagged "verify at deployment time," and the fallback custom MCP server exists specifically so the pattern still works if the native surface isn't enabled yet on a given Search service/region.

---

*Last updated: 2026-08-18*
