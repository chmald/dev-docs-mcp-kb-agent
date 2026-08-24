# 08 — Extraction tier: Document Intelligence Layout vs. Content Understanding

> **Short answer for a technical-manual corpus:** don't pick one — **route per document**.
> Content Understanding is measurably better *and* cheaper on everything that matters for
> datasheets, but it rejects any file over **300 pages**, and reference manuals routinely
> exceed that. So send ≤300-page documents to Content Understanding and oversized ones to
> Document Layout **plus a vision skill that fills in its figure blindness**, and land both in
> one index behind one MCP endpoint. See [Don't choose — route](#dont-choose--route-the-hybrid-tier).

This is the decision guide for *which extraction tier this pattern should use*. It is backed
by a comparison you can re-run against your own corpus with
[`scripts/compare_extraction_tiers.py`](../scripts/compare_extraction_tiers.py) — the numbers
below came from that harness, not from a slide.

**Scope note.** This compares **RAG chunk quality**. For the *field extraction* comparison
(per-field confidence, template drift, tiered DI→CU→OCR routing, migration effort), see the
sibling `document-intelligence-vs-content-understanding` demo — a different axis of the same
DI-vs-CU question, and complementary to this one.

---

## Run it yourself

```bash
cd scripts
python compare_extraction_tiers.py --ids-file ../demo-ids.local.json --build-cu   # stand up Tier B
python compare_extraction_tiers.py --ids-file ../demo-ids.local.json --status     # poll ingestion
python compare_extraction_tiers.py --ids-file ../demo-ids.local.json --report     # scorecard -> out/
python compare_extraction_tiers.py --ids-file ../demo-ids.local.json --teardown   # remove Tier B only
```

Tier B is created alongside the baseline with a `-cu` suffix on every object, so both tiers
coexist on one Search service and the baseline is never disturbed.

**Methodology guardrail:** the harness audits only the documents **both** tiers successfully
ingested, and asks only golden-set questions whose expected source exists in both. Without
that, a document one tier rejected silently confounds every metric — and in our own run, it
would have, because Content Understanding rejected the largest document outright.

---

## One service, or both? The decision in one table

This is the question a customer actually asks. It is answered here with measurements from a
real corpus — **2 documents, 1,025 pages** (a 119-page debug specification and a 906-page ISA
specification), which is a realistic shape for hardware/firmware documentation.

| | **DI Layout only** | **Content Understanding only** | **Hybrid (both)** |
|---|---|---|---|
| **Documents ingested** | 2 of 2 | **1 of 2** ❌ | **2 of 2** ✅ |
| **Pages ingested** | 1,025 (100%) | **119 (11.6%)** ❌ | **1,025 (100%)** ✅ |
| Tables intact | 77% | **100%** | **100%** on CU-routed, 77% on DI-routed |
| Figures usable | **0** — empty `<figure></figure>` | described natively | **334 image-description rows** ✅ |
| Citation correctness | heading path, **21.7% wrong parent** | page range, cannot misattribute | deepest-heading + page range, **no misattribution** ✅ |
| Chunk size consistency | 34–6,116 chars | 204–2,317 chars | consistent per tier |
| Relative ingestion cost | higher per page | **~half** per page | **CU price on most pages**, DI only where required |
| **Answers a figure-only question?** | **No** | **Only if the doc is ≤300 pages** | **Yes** ✅ |

### Why each single-service option fails

**Content Understanding only — fails on coverage.** It is the better and cheaper service, but
it **hard-rejects any file over 300 pages**:

```
InputPageCountExceeded: The input file has 906 pages,
which exceeds the maximum allowed page count of 300.
```

On this corpus that is not a rounding error — it is **88% of the pages missing**. A developer
asking about the ISA specification gets nothing at all. For hardware documentation, where
500–1,500 page reference manuals are the norm rather than the exception, CU-only is not a
viable single choice unless you commit to splitting every large document first.

**Document Intelligence only — fails on content.** It ingests everything, so coverage looks
fine on a dashboard, and that is exactly what makes it dangerous: the corpus is *present* but
**blind to diagrams**. Every register bit-field diagram, pin map, and timing chart arrives as
an empty `<figure></figure>` tag. It also splits ~23% of tables (losing the header row, so the
surviving cells lose meaning) and attributes 21.7% of citations to the wrong parent section.

Nothing errors. The golden set still passes. You discover the gap when an engineer asks which
bits a register field occupies and the assistant cannot answer — or worse, answers from
surrounding prose.

**Hybrid — each service covers the other's failure.**

| Failure | Covered by |
|---|---|
| CU rejects >300-page files | Tier DI+ has no page ceiling |
| DI discards figures | Tier DI+ adds a vision skill that verbalizes them into searchable rows |
| DI splits tables and misattributes headings | Everything ≤300 pages goes to CU instead; DI-routed chunks cite by deepest heading only |
| DI costs more per page | CU handles every document it can, at roughly half the price |

### The proof, in one query

*"What is the bit width of the mtime register and which bits does it span?"* — answerable only
from a **figure**, inside a **906-page document Content Understanding rejects outright**:

```
[riscv-spec.pdf] tier=di-layout-verbalized  kind=image-description
[riscv-spec.pdf] tier=di-layout-verbalized  kind=text
```
> *"This figure is a register bit-field diagram. It shows a 64-bit wide register labeled
> `mtime` spanning bits 63 to 0."*

…returned alongside the prose section `3.2.1. Machine Timer (mtime and mtimecmp) Registers`.

- **CU-only** never sees the document.
- **DI-only** ingests it and throws the diagram away.
- **Hybrid** answers it.

### How to position this

> *"We're not hedging between two services. Content Understanding is better and cheaper, so it
> handles every document it can — and it handles most of them. Document Intelligence covers
> the large reference manuals it structurally can't accept, and we add a vision step there so
> those documents aren't blind to diagrams. The routing decision is free — it's page count,
> read locally before anything is uploaded — so no document is ever processed twice, and you
> pay the cheaper service's price on the majority of your corpus."*

The honest caveat to state alongside it: **the tier mix, and therefore the cost, depends
entirely on the customer's page-count distribution.** Run `--plan` against their real corpus
before quoting — it takes seconds and calls no service.

---

## Measured results — RISC-V debug specification (the document both tiers ingested)

| Metric | Tier A · DI Layout + Split | Tier B · Content Understanding | Winner |
|---|---|---|---|
| Chunks produced | 149 | 236 | — |
| **Tables intact (not split)** | **76.7%** (56/73) | **100%** (132/132) | **B** |
| Figure representation | `<figure>` tags | inlined prose description | — |
| **Chunks carrying an image description** | **0** (0%) | **21** (8.9%) | **B** |
| Empty `<figure></figure>` tags | **19 of 44** | n/a — no tags emitted | **B** |
| Citation style | heading path | page range | — |
| **Citation can misattribute?** | **YES** — 5 wrong-parent of 23 determinable (21.7%) | **NO** | **B** |
| Chunk chars (avg / min / max) | 2077 / 34 / 6116 | 1338 / **204** / **2317** | **B** |
| Low-context chunks (<300 chars) | 17 | **3** | **B** |
| Retrieval golden set (top source) | 2/2 | 2/2 | tie |
| List price / 1,000 pages | $10.00 | **$5.00** | **B** |

Content Understanding wins on every quality axis measured, at half the list price — and
retrieval accuracy was a tie, because the golden set only asks *which document*, which both
tiers get right. **The quality differences show up in the passage a coding assistant actually
receives, not in whether the right file was found.**

---

## The finding that outranks the scorecard

Content Understanding **rejected the 906-page RISC-V ISA specification outright**:

```
InputPageCountExceeded: The input file has 906 pages,
which exceeds the maximum allowed page count of 300.
```

Document Intelligence Layout ingested that same file without complaint (1,300 chunks). For a
hardware/firmware corpus this is decisive, not incidental — SoC reference manuals, MCU
datasheets and ISA specs are routinely 500–1,500 pages.

**Do this before choosing a tier:**

```bash
# The router already does this -- it is the fastest way to see the split.
python scripts/hybrid_ingest.py --ids-file demo-ids.local.json --plan --source-dir <corpus>
```

Or count pages directly with the same library the router uses (`pip install pypdf`):

```bash
python -c "import pathlib,sys; from pypdf import PdfReader; [print(f'{p.name}: {len(PdfReader(str(p)).pages)} pages') for p in sorted(pathlib.Path(sys.argv[1]).glob('*.pdf'))]" <corpus>
```

If a meaningful share of the corpus exceeds 300 pages, you have three options:

| Option | What it costs you | When it's right |
|---|---|---|
| **Split oversized PDFs** into ≤300-page parts — `hybrid_ingest.py --split` | A pre-processing step; page numbers stay traceable because part filenames carry the source range | **Best overall.** Keeps CU's quality across the whole corpus *and* makes vision failures cheap — see below |
| **Hybrid**: CU for ≤300-page docs, DI Layout for the rest | Two skillsets, two indexes, mixed citation styles in one answer | Pragmatic when only a few documents are oversized and splitting is unacceptable |
| **Stay on DI Layout** | Split tables, empty figures, misattributing citations | Only if oversized documents dominate and splitting is unacceptable |

> **Splitting fixes a second, independent problem.** Figure verbalization issues one vision
> call per figure, so a 900-page document creates a burst large enough to exhaust the vision
> deployment's requests-per-minute ceiling — which surfaces as a *misleading 30-second
> timeout* — and long runs additionally hit transient upstream 500s. Because AI Search treats
> a document as one unit, a late failure discards the entire document's enrichment. On the
> validated corpus, splitting the 906-page specification moved **all 906 pages onto the
> higher-quality Content Understanding tier** and eliminated the failure class entirely.

---

## Don't choose — route. The hybrid tier

Neither tier wins outright, so the pattern ships a **router** rather than a default:
[`scripts/hybrid_ingest.py`](../scripts/hybrid_ingest.py).

```
                      page count (free, local metadata)
                                   │
                ┌──────────────────┴──────────────────┐
          ≤ 300 pages                            > 300 pages
                │                                     │
        ┌───────▼────────┐                   ┌────────▼─────────┐
        │  Tier CU       │                   │  Tier DI+        │
        │  Content       │                   │  DI Layout       │
        │  Understanding │                   │  + Split         │
        │  semantic      │                   │  + GenAI Prompt  │
        │  chunking      │                   │    image         │
        │                │                   │    verbalization │
        └───────┬────────┘                   └────────┬─────────┘
                └──────────────────┬──────────────────┘
                                   ▼
                    ONE unified index (extractionTier tag)
                                   ▼
                    ONE knowledge base → ONE MCP endpoint
```

```bash
python hybrid_ingest.py --ids-file ../demo-ids.local.json --plan   --source-dir ../samples/corpus
python hybrid_ingest.py --ids-file ../demo-ids.local.json --upload --source-dir ../samples/corpus
python hybrid_ingest.py --ids-file ../demo-ids.local.json --build
```

`--plan` costs nothing and calls no service — page count is local metadata:

```
Document                                       Pages  Tier                       Why
riscv-debug-specification.pdf                    119  content-understanding      119 pages <= 300 limit
riscv-spec.pdf                                   906  di-layout-verbalized       906 pages exceeds the 300-page limit
```

### The routing is inverted versus the sibling demo — and that matters

The sibling `document-intelligence-vs-content-understanding` demo cascades
**DI → CU → OCR**, escalating on a per-field *confidence score*: Document Intelligence is the
cheap, fast tier 1, and Content Understanding is the expensive escalation.

Here that would be exactly backwards. Content Understanding is **both cheaper and higher
quality**, so Document Intelligence is not tier 1 at all — it is the **capability fallback**
for documents Content Understanding structurally cannot accept.

| | Sibling demo (field extraction) | This pattern (RAG chunking) |
|---|---|---|
| Tier 1 | Document Intelligence (cheap/fast) | **Content Understanding** (cheaper *and* better) |
| Escalation tier | Content Understanding (expensive) | **Document Layout + verbalization** (capability fallback) |
| Trigger | per-field confidence, discovered *after* spend | **page count**, known *before* any spend |
| Cost of routing | pay tier 1, then pay tier 2 on escalation | pay once — the decision is free |

That second-to-last row is the real advantage: a confidence cascade must run the cheap tier
on every document before it learns it needs the expensive one. A constraint router knows the
answer from local file metadata, so no document is ever processed twice.

### How each tier covers the other's gap

| Weakness | Covered by |
|---|---|
| CU rejects files > 300 pages | Tier DI+ has no page ceiling |
| DI Layout discards figures (empty `<figure></figure>`) | Tier DI+ adds a **GenAI Prompt vision skill** that verbalizes extracted images into their own searchable rows — the capability CU gives natively |
| DI Layout's heading path misattributes ~21.7% | The unified index stores `sectionLabel` = **deepest heading only, never a path** (see below) |
| CU emits no heading path | CU rows cite by **page range**, which DI+ rows cannot provide (`locationMetadata` is text-mode only) |

### The free citation fix

The Document Layout heading defect is **not** that the heading is wrong — it's that rendering
`h1 > h2 > h3` asserts an ancestry that doesn't exist. Verified against content: a chunk
labelled `H2='5.7.1 tselect' → H3='5.7.2 tdata1'` genuinely *is* the `tdata1` section. The
**deepest** heading is correct; only the implied parent is fabricated.

So the unified index stores a single `sectionLabel` (deepest heading) and never renders a
path. That removes the misattribution at zero cost and no quality loss.

### Provenance is a first-class field

Every row carries `extractionTier` and `contentKind`, so you can audit quality per tier after
the fact, filter a tier out if it disappoints, and tell text chunks apart from
image-description rows. The MCP client sees one consistent knowledge base and never knows
there were two pipelines.

### Verified end to end (2026-08-20)

Built and run against the full corpus. Result — **2,953 rows in one index**:

| | Rows | Source |
|---|---|---|
| Tier DI+ (`di-layout-verbalized`) | 2,721 | `riscv-spec.pdf` — 906 pages, **which Tier CU cannot ingest at all** |
| Tier CU (`content-understanding`) | 232 | `riscv-debug-specification.pdf` — 119 pages |
| of which `contentKind: image-description` | **167** | figures recovered that plain Document Layout discards |

Tier DI+ completed in roughly **45 minutes** with **zero failures** — one vision call per
extracted figure across 906 pages.

**The payoff, in one query.** Asking *"What is the bit width of the mtime register and which
bits does it span?"* — answerable only from a **figure**, inside a **document Content
Understanding rejected outright** — the hybrid knowledge base returned:

```
[riscv-spec.pdf] tier=di-layout-verbalized  kind=image-description
[riscv-spec.pdf] tier=di-layout-verbalized  kind=text
[riscv-spec.pdf] tier=di-layout-verbalized  kind=image-description
```

> *"This figure is a register bit-field diagram. It shows a 64-bit wide register labeled
> `mtime` spanning bits 63 to 0. Field details: Field name: mtime, Bit width: 64 bits
> (bits 63 down to 0)"*

…returned alongside the prose section `3.2.1. Machine Timer (mtime and mtimecmp) Registers`.

**Neither tier alone can answer that question.** Content Understanding never sees the
document; Document Layout on its own throws the diagram away. That is the case for routing
rather than choosing.

### Gotchas specific to the hybrid (all hit live, all fixed in the script)

- **Two projection selectors into one index need *different* `parentKeyFieldName`s.** Sharing
  one is rejected outright. Image-description rows use `image_parent_id`.
- **Index projection `mappings` do not accept `='literal'` expressions** — that syntax is for
  skill *inputs*. Using it fails the entire run with `Parsing failure: unexpected '='`.
  Materialise constants with a `ConditionalSkill` and project the resulting path.
- **The vision skill's `api-version` query parameter is mandatory.** Omitting it (as the
  Microsoft sample does) returns 404, surfaced as the generic
  *"Web Api skill response is invalid"*.
- **Use a frontier vision model — and give it token headroom.** The ChatCompletionSkill
  request timeout is **fixed at 30 seconds** and is no longer configurable (`timeout` was
  removed from the schema in Search REST API `2026-04-01`). That makes reasoning models look
  unusable. They aren't — the real trap is the **token budget**: reasoning tokens are charged
  against the completion budget, so at the skill's small default the model spends everything
  on reasoning and returns **empty content with no error**. Measured on a real
  register-diagram page from this corpus:

  | Deployment | Budget | Effort | Latency | Output |
  |---|---|---|---|---|
  | `gpt-5.6-sol` (frontier) | 800 | default | 11.3s | **0 chars — silent failure** |
  | `gpt-5.6-sol` (frontier) | 4000 | default | 14.3s | 1,005 chars |
  | **`gpt-5.6-sol` (frontier)** | **4000** | **low** | **10.7s** | **785 chars** ✅ |
  | `gpt-5.4` | 4000 | low | 14.4s | 1,089 chars |
  | `gpt-4.1-mini` | 800 | n/a | 17.2s | 1,296 chars |

  Note the frontier model at low effort is **faster than `gpt-4.1-mini`**, so there is no
  reason to drop to a smaller model here — this skill is where diagram comprehension actually
  happens. Set both on the skill (`reasoning_effort` also needs an API version that supports
  it — this pattern uses `2025-04-01-preview`):

  ```jsonc
  "commonModelParameters": { "maxTokens": 4000 },
  "extraParameters":       { "reasoning_effort": "low" }
  ```

  `reasoning_effort: "minimal"` is rejected (HTTP 400) — `low` is the floor.
- **Provision request-rate headroom for the vision model — this is the #1 cause of ingestion
  failures.** The `ChatCompletionSkill` timeout is fixed at 30 seconds *and*
  `degreeOfParallelism` was removed from the skill schema in Search REST API `2026-04-01`, so
  **you cannot throttle concurrency from the AI Search side**. AI Search fires figure calls
  concurrently; if the deployment's requests-per-minute ceiling is lower than that burst,
  requests queue, individual calls blow the 30s limit, and the document fails.

  The failure is misleading — it reports as a *timeout*, so it looks like the model is too
  slow. It isn't. Measured per-call latency for `gpt-4.1` on a real register-diagram page at
  `detail: high` was **6.6–7.5s**, comfortably inside the limit. The timeouts only appear
  under ingestion concurrency.
  An Azure OpenAI deployment's **request/minute limit scales with its capacity** — capacity
  400 gives 400 requests/min. Size it against how many figures a single document contains,
  not against average token throughput:

  ```bash
  az cognitiveservices account deployment show -n <foundry> -g <rg> --deployment-name vision --query "{cap:sku.capacity, limits:properties.rateLimits[].{key:key,count:count}}" -o json
  ```

  If you cannot raise capacity far enough (subscription quota caps it), **split the document** —
  fewer figures per document means a smaller concurrent burst. That is the same remedy as
  Content Understanding's 300-page limit, for a completely independent reason.

- **Verbalizing a large document is slow.** One vision call per extracted figure across a
  906-page specification runs for tens of minutes. Budget for it, or split the document and
  route it to Tier CU instead.
- **Vision-heavy ingestion of very large documents is *fragile*, not just slow.** Hundreds of
  sequential vision calls hit transient upstream failures in practice:
  `Web Api skill response is invalid ... InternalServerError: upstream connect error or
  disconnect/reset before headers`. AI Search treats a document as one unit, so a late failure
  can cost the whole document's enrichment even though earlier chunks were already projected.

  The indexers set `maxFailedItems: 10` so a flaky document does not halt the run — the AI
  Search default of `0` stops everything on the first failure, leaving every remaining document
  unprocessed. **Retrying a failed document reliably requires `--reset`**: change tracking
  treats an attempted-and-failed document as seen, so a plain re-run reports
  `processed=0 failed=0`, and re-uploading the blob is timing-sensitive enough to silently
  no-op. Budget for the fact that a retry re-bills the corpus.

  This is a **second, independent reason to split oversized PDFs** into ≤300-page parts — not
  only to clear Content Understanding's 300-page limit, but because smaller units make vision
  failures cheap and recoverable instead of catastrophic. Treat "one enormous PDF" as an
  ingestion anti-pattern regardless of which tier handles it.

---

### Tables — 76.7% → 100%
A table split across a chunk boundary loses its header row. The surviving cells are digits
with no column meaning, which is *worse than omitting them* — a model will still try to
answer from them. CU recognises cross-page tables as a single unit; the baseline's fixed
1500-token Split skill has no idea a table is there.

### Figures — the one that matters most for datasheets
The baseline emits a literal `<figure></figure>` and leaves it empty: the diagram was
detected, then discarded. **19 of 44** figure-bearing chunks were empty shells.

Content Understanding emits no `<figure>` tag at all — it inlines a description. On a
register bit-field diagram it produced:

> *"The image shows a single horizontal axis with boxes containing the labels: `sbversion`,
> `sbbusyerror`, `sbbusy`, `sbreadonaddr`, `sbaccess`, `sbautoincrement` … small numeric
> values printed beneath or next to each box: 3, 6, 1, 1, 1, 3, 1"*

That is the `sbcs` register layout — field names **and** bit widths — recovered from an image
that the baseline threw away. This is the single strongest argument for CU on a hardware
corpus, and it is worth stressing that Microsoft's documentation makes **no specific claim**
about interpreting register bit-field diagrams; this is an observed result on a real corpus,
not a documented guarantee. Validate it on your own diagrams.

**Caveat, honestly stated:** only **8.9%** of chunks carried an image description. Figure
verbalization is real but not comprehensive — treat it as a substantial improvement over
zero, not as full diagram coverage.

### Citations — a genuine trade-off, and the baseline loses it
The baseline's heading path is more readable than a page range. It is also **wrong 21.7%** of
the time on determinable pairs, because the Document Layout skill's `sections` dictionary
holds *the most recent heading seen at each level*, not a validated ancestor chain. Observed:

```
H2='5.7.1. Trigger Select (tselect, at 0x7a0)' -> H3='5.7.2. Trigger Data 1 (tdata1, at 0x7a1)'
H2='5.7.8. Supervisor Context (scontext, at 0x5a8)' -> H3='5.7.9. Machine Context (mcontext, at 0x7a8)'
```

Those are **siblings** presented as parent→child. A confidently wrong citation is worse than
a coarse one: a page range is either present or absent, but it never asserts a false
hierarchy. Microsoft does not document `sections` as an ancestor path — do not treat it as one.

### Chunk consistency
The baseline produced chunks from **34 to 6,116** characters. A 34-character chunk is
retrieval noise that dilutes the ranker; a 6,116-character chunk buries the relevant sentence.
CU's semantic chunking stayed within **204–2,317**, respecting paragraph boundaries.

---

## Recommended configuration

Content Understanding replaces **both** the Document Layout skill and the Split skill —
per the skill reference: *"There's no need to use the Text Split skill in your skillset."*

```jsonc
{
  "@odata.type": "#Microsoft.Skills.Util.ContentUnderstandingSkill",
  "name": "content-understanding",
  "context": "/document",
  // MUST name the model actually behind modelDeployment. Naming a model you
  // haven't deployed produces NO figure descriptions, no error, and a
  // successful-looking indexer run.
  "modelName": "gpt-5-mini",
  "modelDeployment": "chat",
  "chunkingProperties": { "method": "semantic", "unit": "tokens", "maximumLength": 500 },
  "extractionOptions": ["images", "locationMetadata"],
  "inputs":  [ { "name": "file_data", "source": "/document/file_data" } ],
  "outputs": [ { "name": "text_sections", "targetName": "text_sections" } ]
}
```

Index the page range instead of heading fields:

```jsonc
{ "name": "pageNumberFrom", "source": "/document/text_sections/*/locationMetadata/pageNumberFrom" },
{ "name": "pageNumberTo",   "source": "/document/text_sections/*/locationMetadata/pageNumberTo" }
```

### Gotchas we hit, so you don't
- **`modelName` mismatch fails silently.** We first set the documented sample's `gpt-4.1`
  against a `gpt-5-mini` deployment. The skillset was accepted, the indexer reported success,
  and **zero** figures were described. Derive `modelName` from the deployment you actually made.
- **Semantic chunking requires `unit: "tokens"`** and forbids `overlapLength`.
- **300-page hard limit per file** — see above.
- **Narrower region list than Document Intelligence.** Confirm CU is available in your region.
- **Pointing a knowledge base at a knowledge source it doesn't own** returns HTTP 200 with an
  empty result, which reads like a retrieval failure. Match source to base per tier.

---

## Cost model

All prices **East US, pay-as-you-go list, retrieved 2026-08-21**, and they move — re-verify
before quoting. Sources are linked per line.

### Unit prices

| Service | Unit | Price | Source |
|---|---|---|---|
| Document Intelligence — **Layout** (prebuilt tier) | per 1,000 pages | **$10.00** | [pricing](https://azure.microsoft.com/pricing/details/ai-document-intelligence/) |
| Document Intelligence — free tier (F0) | pages/month | 500 free | same |
| Content Understanding — document, **Minimal** | per 1,000 pages | **$0.01** | [pricing](https://azure.microsoft.com/pricing/details/content-understanding/) |
| Content Understanding — document, **Basic** | per 1,000 pages | **$1.00** | same |
| Content Understanding — document, **Standard** | per 1,000 pages | **$5.00** | same |
| Content Understanding — contextualization (standard) | per 1M tokens | **$1.00** (1 page ≈ 1,000 tokens; 1 image ≈ 1,000 tokens) | [pricing explainer](https://learn.microsoft.com/azure/ai-services/content-understanding/pricing-explainer) |
| `gpt-5.6-sol` | per 1M tokens | **$5.00 in / $30.00 out** (cached in $0.50) | [OpenAI pricing](https://azure.microsoft.com/pricing/details/cognitive-services/openai-service/) |
| `gpt-5.5` | per 1M tokens | **$5.00 in / $30.00 out** | same |
| `text-embedding-3-large` | per 1M tokens | **$0.143** | same |
| AI Search **Basic** (1 SU) | per hour | **$0.101** (~$73.73/mo at 730 h) | [Search pricing](https://azure.microsoft.com/pricing/details/search/) |
| AI Search **Standard S1** | per hour | **$0.336** (~$245/mo) | same |
| Semantic ranker | per 1,000 requests | first **1,000/month free**, then **$1.00** | same |
| Blob Storage (Hot, LRS) | per GB/month | **$0.0208** | [Storage pricing](https://azure.microsoft.com/pricing/details/storage/blobs/) |

**Indexer/skillset execution carries no separate AI Search charge.** Built-in skills get 20
free documents per indexer per day; beyond that you pay only the underlying service rate.
Utility skills (Conditional, Split, Shaper, Document Extraction) are always free. **The
Content Understanding skill is the exception — it does not get the free daily allowance and
bills from document 1.**
([source](https://learn.microsoft.com/azure/search/cognitive-search-attach-cognitive-services))

### Two things you must not fudge when quoting

1. **The Content Understanding meter is chosen by _file type_, not by the analyzer you
   configure.** Microsoft's rule: *digital* documents (DOCX, XLSX, HTML, clean digital PDF)
   bill **Minimal ($0.01/1,000 pages)**; *image-based* documents needing layout analysis bill
   **Standard ($5.00/1,000 pages)** — a **500×** spread. Semantic chunking requires layout
   analysis, so scanned corpora land on Standard. **Ask whether the customer's corpus is
   scanned or born-digital before putting a number on a slide**, and model a blend if it's
   mixed.
2. **Figure verbalization is billed twice, on two different meters.** The Content
   Understanding contextualization meter charges ~1,000 tokens per image, *and* the Foundry
   model you point `modelDeployment` at charges its own input+output tokens. The same is true
   of the Tier DI+ vision skill (Foundry tokens only, since it isn't a CU call).

### Worked example — the validated corpus

**2 documents, 1,025 pages** (119-page debug spec + 906-page ISA spec), 334 figures
verbalized. One-time ingestion cost:

| | DI Layout only | CU only | **Hybrid** |
|---|---|---|---|
| Pages through DI Layout | 1,025 → **$10.25** | — | 906 → **$9.06** |
| Pages through CU (Standard) | — | 119 → **$0.60** | 119 → **$0.60** |
| CU contextualization | — | ~$0.12 | ~$0.12 |
| Vision tokens (334 figures) | — | — | **~$6–9** *(estimate — see caveat)* |
| Embeddings | ~$0.11 | ~$0.01 | ~$0.30 |
| **One-time total** | **≈ $10.36** | **≈ $0.73** | **≈ $16–19** |
| **Corpus actually covered** | 100% pages, **0 figures** | **11.6% of pages** | **100% pages + 334 figures** |

> ⚠️ **The vision line is an estimate, not a verified figure.** Microsoft does not currently
> publish an image-to-token formula for the GPT-5 family — the only worked example in the
> vision docs (`170 + 85` tokens) is tied to the legacy GPT-4 Turbo model and its own deep
> link is stale. The range above is derived from *measured* output/reasoning tokens
> (340–495 reasoning + ~70–300 content tokens per call) plus an assumed 1,000–2,000 input
> tokens per image. **Measure it on the customer's own corpus before quoting.**

### Read this before you position hybrid as "cheaper"

**On this corpus the hybrid costs slightly more than DI-only** — because 88% of the pages sit
in a single 906-page document that must route to Document Intelligence anyway, and the hybrid
adds vision calls on top. That is an honest and important result:

> **The hybrid is not primarily a cost play. It is the only configuration that produces a
> complete, usable corpus.** DI-only is marginally cheaper and silently blind to every
> diagram. CU-only is dramatically cheaper and silently missing 88% of the pages.

Where the hybrid *does* win on cost is a corpus weighted toward documents **under 300 pages**,
because every one of those pages moves from the $10 meter to the $5 (or $0.01) meter. The tier
mix is the whole cost story — which is why `--plan` exists and why it calls no service:

```bash
python hybrid_ingest.py --ids-file ../demo-ids.local.json --plan --source-dir <corpus>
```

Run that against the customer's real corpus, count pages per tier, and multiply. Anything else
is a guess.

### Monthly running cost

Ingestion is one-time; the standing cost is dominated by AI Search:

| Item | Monthly |
|---|---|
| AI Search Basic (1 SU) | **~$73.73** |
| Blob Storage (1 GB corpus, Hot) | ~$0.02 |
| Semantic ranker (under 1,000 queries) | $0.00 |
| Query-planning tokens (light demo use) | a few cents |
| **Typical demo total** | **~$75/month** |

Model deployments cost nothing at rest — you pay per token. Production sizing (Standard S1 at
~$245/month, replicas for availability) is a separate conversation.

**Re-ingestion is not free:** changing the skillset, the chunking strategy, or the extraction
tier means re-running the indexers and paying the per-page meters again.

---

## Model selection — match the model to the call site, not to a "use frontier" rule

The pattern runs models in **two** places, and they have opposite requirements:

| Call site | Model | Why |
|---|---|---|
| **Figure verbalization**, both tiers (`ChatCompletionSkill`) | **`gpt-4.1`** (non-reasoning) | Hard 30s per-call ceiling with a **total-failure** mode — predictability beats capability |
| **Knowledge Base query planning** | **`gpt-5.6-sol`** (frontier) | No timeout pressure — use the best available |
| Embeddings | `text-embedding-3-large` (3072 dims) | Must match the index vector width |

One frontier deployment plus one vision deployment. That's it.

### Why the vision skill is NOT a frontier reasoning model

The `ChatCompletionSkill` request timeout is **fixed at 30 seconds** and no longer
configurable (`timeout` was removed from the schema in Search REST API `2026-04-01`).
Critically, **one figure that exceeds it fails the entire document** — a 906-page
specification produced **zero** rows because a single image timed out.

Benchmarked on a real register-diagram page:

| Deployment | Budget | Effort | Latency | Output |
|---|---|---|---|---|
| `gpt-5.6-sol` | 800 | default | 11.3s | **0 chars — silent failure** |
| `gpt-5.6-sol` | 4000 | default | 14.3s | 1,005 chars |
| `gpt-5.6-sol` | 4000 | low | **10.7s** | 785 chars |
| `gpt-5.4` | 4000 | low | 14.4s | 1,089 chars |
| `gpt-4.1-mini` | 800 | n/a | 17.2s | 1,296 chars |

On average latency the frontier reasoning model wins outright — and that conclusion is
**wrong at scale**. A single-page benchmark measures the average; the 30s ceiling punishes the
*tail*. In a real run `gpt-5.6-sol` at `reasoning_effort: low` still blew the limit on a
complex figure and destroyed the whole document's ingestion. A non-reasoning model has a much
tighter latency distribution, which is the property that actually matters here.

**Lesson worth carrying:** when a service imposes a hard per-call timeout with a total-failure
mode, select on **latency variance**, not mean latency — and benchmark more than one input.

### Token budget still matters

Reasoning tokens are charged against the completion budget, so a small budget makes a
reasoning model return **empty content with no error** (see the 800-token row above). If you
do point this skill at a reasoning model, give it headroom.

**Pass budget through `extraParameters`, never `commonModelParameters`:**

```jsonc
"extraParameters": { "max_completion_tokens": 1200 }
```

`commonModelParameters.maxTokens` serializes to the legacy `max_tokens`, which current API
versions reject: *"Unsupported parameter: 'max_tokens' is not supported with this model. Use
'max_completion_tokens' instead."* Note this is an **API-version** behaviour, not a
model-family one — `gpt-4.1` rejects it too on `2025-04-01-preview`. Only send
`reasoning_effort` when actually pointing at a reasoning model; non-reasoning deployments
reject it.

### A brand-new deployment isn't immediately usable

It fails in three different-looking ways — `DeploymentIdNotFound`,
`FigureUnderstandingSkipped` ("the model deployment returned an error"), or a vision-skill
`InternalServerError` — all meaning the same thing, and all while the control plane already
reports `Succeeded`. Verify with a direct chat-completions call, then reset and re-run. Don't
start editing the skillset.

### Content Understanding's own figure descriptions: not used, and why

CU's native figure-description feature (`modelName` / `modelDeployment`) is **preview**, and on
a clean rebuild it failed consistently:

```
FigureUnderstandingSkipped: Figure understanding was skipped because
the model deployment returned an error.
```

— reproducibly across a reasoning model (`gpt-5.5`) and a non-reasoning model (`gpt-4.1`), with
both deployments verified serving HTTP 200 directly. In an earlier build the same condition
degraded to a **silent warning**: the tier reported success while producing zero figure
descriptions.

This pattern therefore uses the GA `ChatCompletionSkill` for figures on **both** tiers. That
also means CU's model allowlist — which lags the Foundry catalog and caps at `gpt-5.5` — is no
longer a constraint you have to design around.

Measured effect of the switch: CU-tier figure rows went from **0 → 78** on the same document.

---

## Verify before you quote

**Preview status — state this plainly to a customer.** The two capabilities this pattern leans
on hardest are preview:

| Capability | Status |
|---|---|
| `chunkingProperties.method: "semantic"` (AI Search CU skill) | **Preview** — `2026-05-01-preview` API |
| `modelName` / `modelDeployment` figure descriptions (AI Search CU skill) | **Preview** — same API version |
| `ContentUnderstandingSkill` itself | **GA** in Search REST API `2026-04-01` |
| Content Understanding service | **Unresolved in Microsoft's own docs** — the pricing page FAQ says "public preview… does not have an SLA", while the pricing-explainer describes GA behavior for API `2025-11-01`. Confirm current status before committing a customer to an SLA. |
| Azure AI Search **Knowledge Base** + native MCP endpoint | Real, documented capability; the agentic-retrieval surface has moved quickly (renamed from "Knowledge Agents" in 2026) — re-verify the API version before a customer build |

The Tier DI+ path (`DocumentIntelligenceLayoutSkill` + `ChatCompletionSkill`) is **GA on both
skills**, which is worth noting: if a customer cannot accept preview components, the hybrid
degrades to the DI+ tier for the whole corpus — losing chunk quality and cost efficiency, but
**not** losing figure comprehension, because the vision skill is GA.

Prices are list, US East, captured **2026-08-21**, and move:
- [Content Understanding pricing](https://azure.microsoft.com/pricing/details/content-understanding/) · [pricing explainer](https://learn.microsoft.com/azure/ai-services/content-understanding/pricing-explainer)
- [Document Intelligence pricing](https://azure.microsoft.com/pricing/details/ai-document-intelligence/)
- [Azure OpenAI pricing](https://azure.microsoft.com/pricing/details/cognitive-services/openai-service/)
- [AI Search pricing](https://azure.microsoft.com/pricing/details/search/)

Capability references:

Prices are list, US East, captured **2026-08-20**, and move:
- [Content Understanding pricing](https://azure.microsoft.com/pricing/details/content-understanding/) — Standard $5.00/1,000 pages
- [Document Intelligence pricing](https://azure.microsoft.com/pricing/details/document-intelligence/) — Layout $10.00/1,000 pages

Capability references:
- [Azure Content Understanding skill](https://learn.microsoft.com/azure/search/cognitive-search-skill-content-understanding) — GA in Search REST API `2026-04-01`; semantic chunking + image description are `2026-05-01-preview` parameters calling the CU `2025-11-01` **GA** backend
- [Document Layout skill](https://learn.microsoft.com/azure/search/cognitive-search-skill-document-intelligence-layout) — its own guidance now says *"For new skillsets, use the Azure Content Understanding skill"*
- [Multimodal search in Azure AI Search](https://learn.microsoft.com/azure/search/multimodal-search-overview) — the image-verbalization alternative if you stay on Document Layout
- [Chunk documents for vector search](https://learn.microsoft.com/azure/search/vector-search-how-to-chunk-documents)

There is **no** Microsoft-published reference architecture specific to technical-manual or
datasheet RAG — confirmed absent at time of writing, not merely unfound. Treat this document
as filling that gap for this pattern, and re-verify the underlying service behaviour before
committing a customer to it.

---

*Last updated: 2026-08-24*
