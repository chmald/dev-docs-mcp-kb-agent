# Changelog — Developer Docs MCP Knowledge Base Pattern

Change history for this pattern. Entries are listed newest-first.

---

## 2026-10-02

### Visual refresh — official icons, status pills, legends, nine new diagrams, docs on the visual standard (v1.2.1)

| Change | Why |
|---|---|
| **All eight diagrams moved to the official Microsoft Azure Architecture Icons (V24)**, embedded as data URIs (`iconKey` + embed step) instead of draw.io's bundled `img/lib/azure2` library; Foundry, Foundry Models, Document Intelligence and AI Search now use their current icons | The bundled library predates the Foundry icons, and embedded icons render identically offline, in the VS Code extension and in the PNG export |
| **Icon-inside-tile for every service and script tile**, emoji glyphs removed; layer containers carry a header icon | Edges attach to tile borders and never cross a label; one icon size per diagram |
| **Status pills + legend on every diagram** — GA, PREVIEW, DEFAULT, OPTIONAL, OPT-IN (walkthrough: PASS / FAIL). PREVIEW marks Tier CU semantic chunking and the knowledge base / native MCP endpoint (`2026-05-01-preview`), matching docs/08 › Verify before you quote | A customer can see at a glance what is preview, what is the default path and what can be skipped |
| **Accent bar, Segoe UI throughout, footer credit** (pattern · diagram · version · icon source) | Slide-ready, consistent look across all eight |
| README "Diagrams" note explains the icons and pills; the README assets row lists all eight diagrams; docs/01 ties the PREVIEW pills to the docs/08 status table | The docs described four diagrams and didn't explain the new visual vocabulary |
| **Docs retrofitted to the visual doc standard** (same day): every page now has a breadcrumb, a hero row of official product icons, status badges, an *At a glance* table, icons in service tables, GitHub alert callouts, step cards, collapsible long blocks, a Next link and a footer. `docs/11` was expanded into a full run-of-show with step cards and a "do not show" list | Docs were text and plain tables; the reference demo standard makes them skimmable and forwardable |
| **Nine new diagrams**: service-catalog poster (README), prerequisites map (02), manual deployment steps (03b), testing matrix (04), three-page troubleshooting decision tree (05), native MCP endpoint vs wrapper (06), Copilot MCP client flow (07), Tier CU vs Tier DI+ comparison (08), numbers-that-matter infographic (09) | Every doc now has a diagram for the flow, decision or comparison it describes |
| **Honest status badges**: `live-tested` only for hybrid ingestion, unified index, knowledge-base retrieval and the native MCP endpoint (Aug 2026 clean-room rebuilds); `static-only` for `azd up`, repo export and the walkthrough | Badges must never claim a live run that didn't happen |
| `docs/assets/icons/` (official icons + attribution) and `docs/assets/badges/` (local SVG badges); `scripts/lint_doc_visuals.py` + `tests/test_doc_visuals.py` | Assets render offline; the lint keeps the standard from regressing |
| Fixed: `scripts/export_diagrams.py` passed a 0-based `--page-index` (draw.io expects 1-based), which broke multi-page export; `scripts/check_doc_links.py` now strips inline HTML from headings before slugging (icon-led headings) | Found while adding the three-page decision tree |
| Fixed doc drift on models: docs/01 and docs/02 now match the Bicep and docs/12 — four deployments (`embedding`, `chat` `gpt-5-mini`, `vision` `gpt-4.1`, `sol` `gpt-5.6-sol`); figures use `gpt-4.1`, not the frontier model | docs/01 and docs/02 still described the pre-v1.2 model set |

Geometry, edges and wording of every diagram are unchanged; PNGs re-exported and visually reviewed
(`scripts/export_diagrams.py docs/assets --check` reports 0 stale).

---

## 2026-09-30

### One-command `azd up`, configuration reference, all visuals as draw.io PNGs (v1.2.0)

| Change | Why |
|---|---|
| **`azd up`** — `azure.yaml`, `infra/azd.bicep` (subscription scope: resource group + the shared `main.bicep`), `infra/azd.parameters.json`, and `infra/hooks/` (preprovision: environment-name, tenant/subscription and soft-delete guards; postprovision: writes `demo-ids.local.json`, stores the Search key, optionally ingests `DEMO_CORPUS_DIR`) | A demo should stand up with one command. azd, `deploy.ps1` and the manual path still produce identical resources and names |
| **All four model deployments in Bicep** — `vision` (`gpt-4.1`) and `sol` (`gpt-5.6-sol`) join `embedding` and `chat`, serialized on the account; `deployHybridModels` / `DEPLOY_HYBRID_MODELS=false` opts out | They were two manual CLI commands after every deploy — the most-skipped step |
| **`infra/hooks/common.ps1`** — `deploy.ps1` and the azd hooks now share the soft-delete guard, `Write-DemoIds` (also records `visionDeployment` / `frontierDeployment` / `frontierModel`, and no longer copies the template's `_template` flag) and the Key Vault secret step | One implementation, so the two paths can't drift |
| **New [docs/12-configuration-reference.md](./docs/12-configuration-reference.md)** — every azd variable, hook knob, Bicep output, `deploy.ps1` parameter, `demo-ids.local.json` key and runtime environment variable, with default, consumer and recipes | Configuration was spread across Bicep, scripts and code defaults |
| **`tests/test_configuration.py`** (10 tests) | Fails when a Bicep parameter isn't settable through azd, a parameter value isn't a quoted substitution, a hook reads an output nothing produces, or any azd variable / output / script setting is missing from docs/12 |
| **Remaining text diagrams replaced with draw.io + PNG** — deployed topology (docs/00), repository package layout (docs/10, text tree kept in a collapsed block), plus new `azd up` flow (docs/03) and configuration flow (docs/12) diagrams; eight diagrams total | Visuals are what a wider audience reads first |
| `.gitignore` excludes `.azure/` | azd environments hold subscription/tenant IDs and resolved endpoints |

**Verified against current documentation (2026-09-30):** `azure.yaml` `infra.provider/path/module`
(azd deploys `<path>/<module>.bicep` with `<module>.parameters.json`); infrastructure-only
templates (no `services:`) are valid and `azd up` simply provisions; subscription-scoped main
template is the standard pattern, while resource-group-scoped templates are **beta** — hence
`azd.bicep` at subscription scope; parameter files are parsed as JSON before substitution, so
bool/int values stay quoted (`"${VAR=default}"`); azd provides `AZURE_PRINCIPAL_ID` and
`AZURE_PRINCIPAL_TYPE` (`User` / `ServicePrincipal`); Bicep output names are written to
`.azure/<env>/.env` as-is and every hook receives them as environment variables; hook keys
`shell`, `run`, `continueOnError`, `interactive`; `azd down --purge` purges soft-deleted Key
Vault and Cognitive Services accounts; `azd provision --preview` is the what-if.

Not yet run end to end against a subscription — `azure.yaml` parses (`azd show`), both
templates compile, the hooks parse and their guards were exercised locally, and the shared
`Write-DemoIds` helper was tested against object and hashtable inputs.

### Repository export, scripted walkthrough, oversized-upload guard, visuals (v1.1.0)

Driven by a gap between what the pattern delivered and what the originating ask actually was:
teams wanted vendor PDFs turned into **repository content** (Markdown, diagrams, image files)
that coding assistants read from the working tree, not only a search index.

| Change | Why |
|---|---|
| **New `scripts/export_repo_corpus.py`** + [docs/10](./docs/10-repo-corpus-export.md) | PDF → page-cited Markdown sections, original figure crops, AI-drafted Mermaid for flow/block/state diagrams, corpus + per-document READMEs with Mermaid visuals, `manifest.json` with source and file hashes, and a Copilot-instructions snippet. Analyze once (billable, cached by file hash), export offline as often as needed. Split parts are stitched back into one document with **original** page numbers |
| **Opt-in structured extractors** (`corpus.exportExtractors`: `registers`, `pins`, `electrical`) | Register tables become JSON + a Mermaid bit-field diagram + a Markdown table; pin and electrical tables become JSON with parsed min/typ/max. Off by default so the pattern stays corpus-neutral; a reusability guard test enforces that generic output carries no hardware vocabulary |
| **New `scripts/demo_walkthrough.py`** + `samples/walkthrough.example.json` + [docs/11](./docs/11-customer-walkthrough.md) | Section E of the test plan was manual. It is now a scripted, self-checking walkthrough with expected sources and content kinds (the figure step must be answered from an `image-description` row), a `--present` talk track that makes no Azure calls, and a Markdown report |
| **`hybrid_ingest.py --upload` refuses oversized documents without `--split` or `--no-split`** | Routing a > 300-page manual to Tier DI+ is a quality and reliability decision (docs/09), so it must be explicit. `--plan` warns; `--upload` stops before uploading anything |
| **Visuals** — four draw.io diagrams (reference architecture, hybrid routing, repository export, customer walkthrough) built on the Azure icon set, each committed as `.drawio` source **plus** an exported PNG that the docs embed; all hand-authored Mermaid removed from README and docs/01, 08, 10, 11; new `scripts/export_diagrams.py` re-exports PNGs via the draw.io desktop CLI and `--check` flags a PNG older than its source | Wider audiences grasp the design from a real architecture picture before reading prose, and a PNG renders identically in GitHub, Azure DevOps, VS Code, email and slides. The architecture diagram was also stale (single ingestion tier, `gpt-4o-mini`, "preview" MCP caveat) and now shows the hybrid tiers, model deployments and the repository-export lane |
| **docs/04 § F** — install requirements before running tests; test count 52 → 80, with a per-file coverage table | Without the `mcp` SDK, `test_mcp_fallback_server.py` fails at *collection*, which reads like a broken suite |

**Verified against current documentation (2026-09-30):** Document Intelligence v4.0 GA
(`2024-11-30`) analyze path, `outputContentFormat=markdown`, `output=figures` and the
`analyzeResults/{resultId}/figures/{figureId}` PNG endpoint; figure IDs follow an
*undocumented* `{page}.{index}` convention (handled defensively); `stringIndexType` defaults to
`textElements`, so the exporter sends `unicodeCodePoint` to keep offsets aligned with Python
strings; S0 limits 2,000 pages / 500 MB (F0 analyzes only the first 2 pages); Entra auth scope
`https://cognitiveservices.azure.com/.default` with **Cognitive Services User**; prebuilt-layout
list price $10 / 1,000 pages, and figure output is not a billed add-on. Mermaid (used only in
the exporter's *generated* output): the bit-field diagram's documented keyword is `packet`
(v11.0+); `packet-beta` is still accepted and was kept for compatibility because GitHub does
not publish its Mermaid version, and every generated diagram has a Markdown-table twin.

Not yet exercised against a live Document Intelligence resource — the exporter's Azure calls
are verified against the REST reference, and the offline stage is covered by tests. Run
`--analyze` on the sample corpus before the first customer use.

---

## 2026-08-24

### Cross-platform docs, consolidated findings reference (v1.0.9)

**All documentation now works on Windows, Linux, and macOS.** Nothing in the pattern was
Windows-specific — `deploy.ps1` runs under PowerShell 7, which is cross-platform, and the
Python scripts and `az` commands are platform-neutral — but every command block in the docs was
written PowerShell-first and would not paste into bash.

| Change | Effect |
|---|---|
| Joined **83** PowerShell backtick line-continuations into single-line commands | Multi-line continuation is the single biggest source of shell incompatibility (backtick vs backslash). Single-line commands paste cleanly into PowerShell, bash, and zsh |
| Relabelled **44** shell-agnostic blocks from `powershell` to `bash` | Blocks now advertise what they actually are; 55 bash vs 10 PowerShell, and the remaining PowerShell blocks are genuinely PowerShell-only |
| Replaced PowerShell-object diagnostics with `curl` / `az --query` | Tier-provenance checks, MCP probes, quota checks, orphan detection and indexer tracking-state inspection now run anywhere |
| Added dual assignment blocks in `03b-manual-deployment.md` | Only variable *assignment* differs between shells — `$Rg` is *referenced* identically — so a single dual block per section covers the whole manual path |
| Added **§ 0 Tooling and shell conventions** to `02-prerequisites.md` | Documents that virtual-environment activation is the only genuinely shell-specific step; after activation every command is identical everywhere |

Verified: every markdown fence balanced, all internal links and anchors resolve, and the
convention is stated explicitly so future edits stay portable.

### New: docs/09 — Findings and lessons

Consolidates everything two clean-room rebuilds surfaced into one reference organised **by
symptom**, because the recurring theme was that the error message named the wrong cause:

- **Failures that lie to you** — a vision "30s timeout" that was actually request-rate
  throttling (real latency 6.6–7.5s); a 404 that was a missing `api-version`; a
  `DeploymentIdNotFound` for a deployment that existed
- **Silent failures** — `allowProjectManagement` stripped from the compiled template while
  Bicep warned correctly and the warning was wrongly suppressed; Content Understanding figure
  descriptions reporting success while producing zero output; reasoning models returning empty
  content when the token budget is exhausted; orphaned index rows after a corpus change
- **Teardown and rebuild** — soft-deleted Cognitive Services and in-flight AI Search deletes
- **Ingestion at scale** — `maxFailedItems: 0` letting one flaky document block four healthy
  ones; why `--reset` is the only reliable retry; why split size is driven by figure density
  rather than page count
- **Corrections to earlier guidance** — six documented claims that measurement later disproved,
  recorded deliberately rather than quietly overwritten

Headline lesson: **a pattern is not customer-ready until it has been built from zero, twice.**
This one was authored, accuracy-reviewed against Microsoft Learn, and API-contract validated —
three passes — and the first real deployment still hit nine defects, six of them hard blockers.

---

## 2026-08-21

### Docs realigned to the hybrid, cost model added, and a clean-room rebuild (v1.0.8)

Two pieces of work: bringing every document in line with what actually deploys, then **deleting
the whole environment and rebuilding from those documents** to prove the flow works from zero.
The rebuild found nine friction points, three of them real bugs.

**Documentation.** `01-architecture` and `00-reproduce` rewritten around the hybrid router;
`03-deployment` Phase 2 replaced with hybrid ingestion; `04-testing` rewritten to add
**chunk-quality** and **tier-provenance** categories (a golden set that only checks *which
document* came back cannot detect a degraded passage — that is how the original build passed
4/4 while silently discarding every figure); `03b-manual` gained the Foundry project step;
retired resource names swept out of 02/05/06/07. Added
[`scripts/check_doc_links.py`](scripts/check_doc_links.py), which caught 8 genuinely broken
cross-references the restructure introduced.

**`docs/08` is now the "why both services" doc**, with a three-way comparison on the validated
corpus (2 documents, 1,025 pages):

| | DI Layout only | CU only | Hybrid |
|---|---|---|---|
| Pages ingested | 1,025 (100%) | **119 (11.6%)** | **1,025 (100%)** |
| Figures usable | **0** | native | **334 rows** |
| One-time cost | ≈$10.36 | ≈$0.73 | ≈$16–19 |

**The hybrid is not a cost play** — on this corpus it costs *more* than DI-only, because 88% of
pages sit in one 906-page document that must route to Document Intelligence anyway. It is the
only configuration that produces a **complete, usable** corpus. Stated plainly so nobody
positions it wrongly.

**Cost model added** with verified list prices and per-line sources, plus two caveats that
materially change a quote: the Content Understanding meter is selected by **file type, not
analyzer** (digital $0.01/1,000 pages vs image-based $5.00/1,000 — a 500× spread), and figure
verbalization bills on **two** meters. The vision-token line is published explicitly as an
*estimate*, because Microsoft currently documents no image-to-token formula for the GPT-5
family.

**Clean-room rebuild findings:**

| # | Finding | Resolution |
|---|---|---|
| 1 | `pypdf` missing from `requirements.txt` | Added |
| 2 | Cognitive Services **soft-delete** blocks redeploy (`FlagMustBeSetForRestore`) | `deploy.ps1` detects it and offers to purge |
| 3-4 | **`allowProjectManagement` was silently stripped** from the compiled template on `accounts@2024-10-01`. The `BCP037` warning was correct and had been wrongly suppressed; the previous build only worked because the project had been created by hand in the portal | Bumped to `accounts@2025-06-01`; verified the property survives compilation and the project is created automatically |
| 5 | `deploy.ps1` next-steps pointed at the retired ingestion script | Now prints the hybrid flow |
| 6 | Storage network denial surfaced as a raw stack trace | Actionable message referencing the troubleshooting section |
| 7 | A brand-new model deployment fails three different-looking ways while the control plane reports `Succeeded` | Documented as one root cause with a verification step |
| 8 | **Content Understanding's figure descriptions failed consistently** (`FigureUnderstandingSkipped`) across both a reasoning and a non-reasoning model, with both deployments verified serving. In the *earlier* build the same condition degraded to a silent warning — the tier reported success while producing **zero** figure descriptions | Dropped CU's preview figure feature entirely |
| 9 | **Figures now use the GA `ChatCompletionSkill` on _both_ tiers** | One figure mechanism instead of two, GA instead of preview; CU-tier figure rows went **0 → 78**. Also removes CU's model allowlist as a design constraint and drops the second frontier deployment |

**Model selection corrected — the earlier guidance in this changelog was wrong.** The
`ChatCompletionSkill` 30-second timeout has a **total-failure** mode: one slow figure failed an
entire 906-page document, producing zero rows. `gpt-5.6-sol` benchmarked *faster* than
`gpt-4.1-mini` on a single page (10.7s vs 17.2s) — but a one-page benchmark measures the mean,
while a hard ceiling punishes the tail. The vision skill now runs the **non-reasoning
`gpt-4.1`**; frontier `gpt-5.6-sol` stays on knowledge-base query planning, where there is no
timeout pressure. Generalisable lesson: **when a service imposes a hard per-call timeout with a
total-failure mode, select on latency _variance_, not mean latency.**

**Also found:** vision-heavy ingestion of very large documents is *fragile*, not merely slow —
hundreds of sequential vision calls hit transient upstream 500s, and because AI Search treats a
document as one unit a late failure can cost the whole document's enrichment. Re-running
without `--reset` resumes from the checkpoint. This is a second, independent reason to split
oversized PDFs beyond Content Understanding's 300-page limit.

**Verified on the rebuilt environment:** Bicep deploys Storage, Key Vault, Foundry **+ project**,
and AI Search; both tiers ingest; the native MCP endpoint answers with references spanning both
documents. Model deployments reduced to four: `embedding`, `chat`, `sol`, `vision`.

---

## 2026-08-20 (hybrid)

### Hybrid extraction routing — use both tiers where each excels (v1.0.7)

Added [`scripts/hybrid_ingest.py`](scripts/hybrid_ingest.py) and a
[Don't choose — route](docs/08-extraction-tier-comparison.md#dont-choose--route-the-hybrid-tier)
section to `docs/08`. Rather than picking a single extraction tier, the pattern now routes
**per document** and lands both tiers in **one unified index behind one knowledge base**, so
the MCP client sees a single consistent corpus.

```
page count  ──  ≤300 pages ──►  Tier CU   Content Understanding, semantic chunking
   (free)   └─  >300 pages ──►  Tier DI+  Document Layout + Split + GenAI Prompt
                                          image verbalization
```

**The routing is deliberately inverted relative to the sibling
`document-intelligence-vs-content-understanding` demo.** There, Document Intelligence is the
cheap/fast tier 1 and Content Understanding is the expensive escalation, triggered by a
per-field confidence score. Here Content Understanding is **both cheaper and better**, so
Document Intelligence is not tier 1 — it is the *capability fallback* for documents Content
Understanding structurally cannot accept. The trigger is a hard input constraint (page count)
known **before** any spend, not a confidence signal discovered after it, so no document is
ever processed twice.

**Each tier covers the other's gap:**

| Weakness | Covered by |
|---|---|
| CU rejects files > 300 pages | Tier DI+ has no page ceiling |
| DI Layout discards figures (empty `<figure></figure>`) | Tier DI+ adds a GenAI Prompt vision skill that verbalizes figures into their own searchable rows |
| DI Layout's heading path misattributes ~21.7% | Unified index stores `sectionLabel` = **deepest heading only, never a path** |
| CU emits no heading path | CU rows cite by page range (`locationMetadata` is text-mode only, so DI+ rows cannot) |

**Free citation fix.** Verified against chunk content that the Document Layout heading defect
is *not* a wrong heading — a chunk labelled `H2='5.7.1 tselect' → H3='5.7.2 tdata1'` genuinely
is the `tdata1` section. The **deepest** heading is correct; only the implied ancestry is
fabricated. Storing the deepest heading and never rendering a path removes the misattribution
at zero cost.

Every row carries `extractionTier` and `contentKind` provenance, so tier quality can be
audited after the fact and image-description rows distinguished from text chunks.

**New constraints found live while building this** (all fixed in the script, all easy to
misdiagnose):

| Constraint | Symptom if you get it wrong |
|---|---|
| Two index projection selectors into one index need **different** `parentKeyFieldName`s | Skillset rejected outright |
| Index projection `mappings` do **not** accept `='literal'` expressions (that syntax is skill *inputs* only) | Whole run fails: `Parsing failure: unexpected '='`. Use a `ConditionalSkill` to materialise constants |
| The vision skill's `api-version` query parameter is **mandatory** (the Microsoft sample omits it) | 404, surfaced as the generic *"Web Api skill response is invalid"* |
| `extractionOptions: ["locationMetadata"]` is **rejected** with `outputFormat: "markdown"` | Skillset rejected — location metadata is text-mode only |
| ChatCompletionSkill timeout is **fixed at 30s** and no longer configurable (`timeout` removed in Search REST API `2026-04-01`) | Looks like "reasoning models are too slow" — but the real trap is the **token budget**. Reasoning tokens are charged against the completion budget, so at the default the model spends it all reasoning and returns **empty content with no error**. Give headroom + low effort and a frontier model fits comfortably — see the benchmark below |

**Also worth budgeting for:** verbalizing every figure across a 906-page specification runs
for tens of minutes — one vision call per figure. Splitting the document and routing it to
Tier CU is usually the better trade.

**Frontier model benchmark (corrects an earlier conclusion in this entry's first revision).**
The image-verbalization skill is where diagram comprehension actually happens, so it is the
wrong place to economise. Measured on a real register-diagram page from the corpus:

| Deployment | maxTokens | reasoning_effort | Latency | Output |
|---|---|---|---|---|
| `gpt-5.6-sol` (frontier) | 800 | default | 11.3s | **0 chars — silent failure** |
| `gpt-5.6-sol` (frontier) | 4000 | default | 14.3s | 1,005 chars |
| **`gpt-5.6-sol` (frontier)** | **4000** | **low** | **10.7s** | **785 chars** ✅ |
| `gpt-5.4` | 4000 | low | 14.4s | 1,089 chars |
| `gpt-4.1-mini` | 800 | n/a | 17.2s | 1,296 chars |

The frontier model at low effort is **faster than the small model**, so the pattern now
defaults to `gpt-5.6-sol` with `commonModelParameters.maxTokens: 4000` and
`extraParameters.reasoning_effort: "low"` (both confirmed supported on
`ChatCompletionSkill`; `reasoning_effort: "minimal"` is rejected with HTTP 400). The earlier
"use a fast non-reasoning model" guidance was wrong about the cause — it was never latency,
it was an exhausted completion budget returning empty content silently.

**Frontier models across all three call sites (2026-08-21).** The pattern runs models in three
places with **three different ceilings**, so "are we on frontier?" is three questions:

| Component | Model | Constraint |
|---|---|---|
| DI+ image verbalizer (`ChatCompletionSkill`) | **`gpt-5.6-sol`** | no allowlist — current frontier |
| CU figure descriptions (`ContentUnderstandingSkill`) | **`gpt-5.5`** | CU enforces its **own allowlist that lags the Foundry catalog** |
| Knowledge-base query planning | **`gpt-5.6-sol`** | runs on every MCP call |

Content Understanding rejects newer models outright and enumerates the current ceiling in the
error, which is the fastest way to discover it. Upgrading the verbalizer from `gpt-4.1-mini`
to `gpt-5.6-sol` took image-description rows **167 → 334** on the same document, zero empty,
190 capturing explicit bit/field/register structure and 20 capturing RISC-V `WARL`/`WLRL`
semantics.

Two further corrections:
- **`commonModelParameters.maxTokens` is unusable with reasoning models** — it serializes to
  the legacy `max_tokens`, which every GPT-5-family model rejects (*"Use
  'max_completion_tokens' instead"*). Pass budget and effort through `extraParameters`, which
  forwards keys verbatim: `{ "max_completion_tokens": 4000, "reasoning_effort": "low" }`.
- **A brand-new model deployment isn't immediately resolvable.** Content Understanding
  returned `DeploymentIdNotFound` for a deployment already reported `Succeeded` by the control
  plane. Wait a few minutes and re-run.

**Final state.** The baseline and `-cu` comparison pipelines are torn down;
`idx-documents-hybrid` + `kb-hybrid` is the single surviving pipeline (**4,109 rows** — 3,882
Tier DI+, 227 Tier CU, of which **334 image-description**). `.vscode/mcp.json` repointed at
`kb-hybrid` and verified over the native MCP endpoint with a query key: one question now
returns evidence from **both** tiers at once.

**Verified end to end.** The full corpus now sits in one index: **2,953 rows** — 2,721 from
Tier DI+ (`riscv-spec.pdf`, 906 pages, which Tier CU cannot ingest at all) and 232 from Tier
CU, including **167 `image-description` rows** recovered from figures plain Document Layout
discards. Tier DI+ ran ~45 minutes with zero failures. Asking *"What is the bit width of the
mtime register and which bits does it span?"* — answerable only from a figure, in a document
CU rejected — returns the verbalized bit-field diagram (*"64-bit wide register labeled mtime
spanning bits 63 to 0"*) alongside the prose section `3.2.1. Machine Timer (mtime and
mtimecmp) Registers`. **Neither tier alone can answer it.**

---

## 2026-08-20 (later)

### Extraction-tier A/B: Content Understanding vs. Document Intelligence Layout (v1.0.6)

Added [`scripts/compare_extraction_tiers.py`](scripts/compare_extraction_tiers.py) and
[`docs/08-extraction-tier-comparison.md`](docs/08-extraction-tier-comparison.md) — a harness that stands a
**Content Understanding** ingestion tier up beside the existing Document Layout + Split tier
on the same Search service (every object suffixed `-cu`), ingests the same blobs, and scores
both. Prompted by an audit of the live index that found the baseline's extraction quality was
materially worse than the successful golden-set run implied.

**Baseline defects found by auditing the deployed index** (RISC-V corpus, 1,449 chunks):

| Defect | Measurement |
|---|---|
| Tables split across chunk boundaries | 27% of table-bearing chunks |
| Figures extracted as empty `<figure></figure>` | 36% of figure-bearing chunks |
| Heading path asserts the **wrong parent** | 41% of determinable H2→H3 pairs |
| h4+ headings never captured as citation metadata | 14.6% of chunks (`markdownHeaderDepth: h3`) |
| Chunk size variance | 10 to 8,013 chars; 193 under 300, 47 over 6,000 |

The heading defect is the serious one: the Document Layout skill's `sections` dictionary holds
*the most recently seen heading at each level*, not a validated ancestor chain, so it will
confidently cite a **sibling** section as the parent. Microsoft does not document it as an
ancestor path — do not treat it as one.

**Measured result on the document both tiers ingested** (RISC-V debug specification):

| Metric | DI Layout + Split | Content Understanding |
|---|---|---|
| Tables intact | 76.7% (56/73) | **100%** (132/132) |
| Chunks carrying an image description | 0 | **21 (8.9%)** |
| Empty `<figure>` tags | 19 of 44 | n/a — none emitted |
| Citation misattribution | 21.7% wrong parent | structurally impossible (page range) |
| Chunk chars (avg / min / max) | 2077 / 34 / 6116 | 1338 / **204** / **2317** |
| Retrieval golden set | 2/2 | 2/2 |
| List price / 1,000 pages | $10.00 | **$5.00** |

Content Understanding wins every quality axis at half the list price. Notably it **verbalizes
register bit-field diagrams** — it recovered `sbversion` / `sbbusyerror` / `sbbusy` /
`sbreadonaddr` / `sbaccess` / `sbautoincrement` with bit widths from the `sbcs` register image
the baseline discarded. Microsoft documents generic figure description and makes **no** claim
about bit-field diagrams specifically, so this is an observed result on a real corpus, not a
guarantee — and coverage was only 8.9% of chunks, so it is a substantial improvement over
zero rather than full diagram coverage.

**The finding that outranks the scorecard: Content Understanding enforces a 300-page-per-file
limit.** It rejected the 906-page RISC-V ISA specification outright
(`InputPageCountExceeded`), which Document Layout ingested without complaint. For
hardware/firmware corpora — where 500–1,500 page reference manuals are normal — this is a
gating constraint, not a footnote. `docs/08` documents the split / hybrid / stay-put options.

**Also fixed in this entry:**
- **`allowProjectManagement: true` + a Foundry project are now created by Bicep.** The first
  live deployment required creating a project by hand in the portal before the Search service
  would work against the knowledge base; the template never created one.
- Harness methodology guards, each of which was a real bug that produced plausible-but-wrong
  numbers: audit scoped to documents **both** tiers ingested (otherwise a rejected document
  confounds every metric); golden questions filtered to shared sources; response parsing that
  handles **both** knowledge-base shapes (`/retrieve` prose + `references` array **and** the
  extractiveData JSON array); per-tier knowledge-source routing (pointing a knowledge base at
  a source it doesn't own returns HTTP 200 with no results, which reads as a retrieval
  failure); and table-split detection that counts orphaned table *tails* — chunks holding
  closing markup with no `<table>` opener, which is precisely the chunk that lost its header
  row and which the first version of the metric missed entirely.
- **`modelName` must name the model actually behind `modelDeployment`.** Using the
  documentation sample's `gpt-4.1` against a `gpt-5-mini` deployment produced **zero** figure
  descriptions, with the skillset accepted and the indexer reporting success.

Cross-referenced with the sibling `document-intelligence-vs-content-understanding` demo, which
covers the *field extraction* axis (confidence, template drift, tiered routing); this covers
the *RAG chunk quality* axis.

**Test suite: 40 → 52.**

---

## 2026-08-20

### First live deployment — dogfood run (v1.0.5)

**The pattern had never actually been deployed.** Every prior revision was documentation- and static-analysis-verified only. This entry records the first end-to-end run against a real Azure subscription, which reached a working GitHub-Copilot-consumable MCP endpoint — and found **nine defects** on the way, six of them hard build blockers.

Corpus used: the RISC-V ISA specification + RISC-V debug specification (CC-BY, redistributable), 1,449 indexed chunks across 2 documents.

| # | Defect | Impact if unfixed |
|---|---|---|
| 1 | **`infra/deploy.ps1` ignored deployment failure** — `az deployment group create` returned non-zero, but the script continued, wrote a garbage `demo-ids.local.json` from the failed output, and printed "Deployment complete." | Silent false success. The operator believes the platform is up; every downstream step fails with confusing secondary errors. |
| 2 | **Skillset `subdomainUrl` used the Document Intelligence endpoint** (`<name>.cognitiveservices.azure.com/`) | HTTP 400 `'SubdomainUrl' parameter is not well-formed` — **skillset cannot be created at all**. A `kind: AIServices` account must be referenced by its **AI Foundry** subdomain (`<name>.services.ai.azure.com`), with no trailing slash. Bicep now emits `aiServicesSubdomainUrl` and the script derives it from `foundryResource`. |
| 3 | **Knowledge base shipped `outputMode: answerSynthesis`** | **Breaks the pattern's headline path.** The native MCP tool accepts *only* a `queries` array — it cannot pass `includeReferenceSourceData` — so under `answerSynthesis` the MCP client receives a synthesised non-answer ("I cannot access external documents") and the grounded passages never arrive. Now defaults to `extractiveData`, which returns ranked passages with `ref_id` + source document + heading path. Overridable via `knowledgeBaseOutputMode`. |
| 4 | **`--check-mcp-endpoint` called `resp.json()` on an SSE response** | **False negative on the pattern's core claim.** MCP Streamable HTTP replies `text/event-stream`; `resp.json()` raises "Expecting value: line 1 column 1", so a fully working native endpoint was reported unavailable — pushing operators to deploy the optional fallback Container App for no reason. Now parses `data:` lines and sends `Accept: application/json, text/event-stream`. |
| 5 | **`subprocess.run(["az", ...])` fails on Windows** | `FileNotFoundError: [WinError 2]` — the CLI is `az.cmd`, which `subprocess` will not resolve from a bare `"az"`. Now resolved via `shutil.which`. |
| 6 | **`get_admin_key` hard-depended on Key Vault data-plane reach** | In governed subscriptions, policy forces Key Vault `publicNetworkAccess: Disabled`, so the lookup fails **even with correct RBAC** and the whole build stops. Now falls back to `az search admin-key show` (control plane), and skips the vault entirely when `keyVault` is absent instead of raising `KeyError`. |
| 7 | **Search API errors were swallowed** — `raise_for_status()` discards the response body, where the actual reason for a 400 lives | Debugging blind. Added `raise_with_detail()`, which prints status + body before raising. This is what surfaced defect #2. |
| 8 | **`chatCapacity: 10` (10K TPM) too low for agentic retrieval** | HTTP 429 `exceeded rate limit` on the very first retrieval call — query planning plus answer generation exceeds 10K TPM immediately. Raised the default. |
| 9 | **`.gitignore` corpus guard was single-level** (`samples/*.pdf`) | A corpus dropped into a subfolder — `samples/corpus/`, which is the natural place to put it — was **not** ignored, defeating the pattern's own "never commit a copyrighted vendor manual" guard. Patterns are now recursive (`samples/**/*.pdf`) and cover the Office formats the Layout skill also reads. |

**Environment finding — governed subscriptions (not a code defect, but it will block a build).** In subscriptions with an Azure Policy that forces `publicNetworkAccess: Disabled` on Storage and Key Vault, the workstation cannot reach the blob data plane to upload the corpus, and explicit `az ... update --public-network-access Enabled` calls are silently reverted by the policy's `modify` effect. The working path is a **Network Security Perimeter** association: create an NSP profile with an inbound subscription rule (covers Search → Storage via managed identity) plus an inbound rule for the operator's public IP, associate the storage account, then set `publicNetworkAccess: SecuredByPerimeter` via REST (the `az storage account update` CLI does not expose that value). Propagation to the data plane takes roughly 2-5 minutes. Documented in `docs/05-troubleshooting.md`.

**Region finding.** `eastus2` — the pattern's default region — returned `InsufficientResourcesAvailable` for AI Search ("the region is currently out of the resources required to provision new services"). `eastus` succeeded. Region availability for AI Search should be checked before committing to the default.

**Validated end to end:**
- **Full chain proven, including the client.** `.vscode/mcp.json` loaded in VS Code, the MCP server connects, and retrieval works from the editor — Parts A–E of `docs/00-reproduce-this-demo.md` are all complete. Client-side prerequisite learned the hard way: the **GitHub Copilot Chat extension** (`github.copilot-chat`) must be installed; `ms-azuretools.vscode-azure-github-copilot` is a different extension and does not provide it. Added to `docs/02-prerequisites.md`.
- Native MCP endpoint **confirmed live and working** — `tools/list` returns `knowledge_base_retrieve`; the optional fallback Container App was **not needed**. The tool's input schema accepts exactly one argument, `queries` (array, 1 item, ≤400 chars each).
- Protocol-level verification: `initialize` returns HTTP 200 / `text/event-stream` / protocol `2025-06-18`; `tools/call` returns grounded references carrying source document + heading path; an unauthenticated request is correctly rejected with **401**.
- Both API-key and query-key auth work against the MCP endpoint. Entra ID bearer-token auth was **not** exercised.
- Golden set: 4/4 correct, with correct cross-document routing (debug questions → debug spec, ISA questions → ISA spec) and precise heading-path citations, e.g. `3.14.2. Debug Module Control (dmcontrol, at 0x10)`.

**Test suite: 29 → 40.** Eleven new regression tests, one per defect class above, plus overridability guards. Verified: pytest 40/40, `az bicep build` clean.

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

*Last updated: 2026-08-24*
