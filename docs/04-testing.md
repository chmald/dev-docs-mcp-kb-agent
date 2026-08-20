# 04 — Testing

End-to-end test plan. Run these after Phase 5 validation in [03-deployment.md](./03-deployment.md) passes.

## Test categories

| Category | Goal | Cadence |
|---|---|---|
| A. Functional | End-to-end happy path: PDF in → cited answer out via MCP | Every deployment |
| B. Smoke | Scripts import cleanly, REST calls return expected shape | Every deployment |
| C. Quality (golden set) | Retrieval recall/precision + citation accuracy against known Q&A pairs | Before any customer demo; monthly |
| D. Native-vs-fallback comparison | Confirm both MCP paths return equivalent grounded answers | Once per instance, and after any Search service tier change |
| E. End-to-end demo script | The lived developer experience in VS Code | Day-of-demo dry run |
| F. Regression | Re-run after any schema, skillset, or MCP server change | After every change |

## A — Functional tests

1. Upload a known test PDF (`tests/fixtures/sample.pdf` or your corpus) → run the indexer → confirm it appears in `idx-documents`
2. Call the Knowledge Base's `retrieve` operation directly with a question the test PDF answers → confirm the response text and citation match
3. Call the MCP endpoint (native or fallback) with the same question → confirm the MCP tool result matches the direct `retrieve` call

## B — Smoke tests

Automated in `tests/test_post_deploy_search.py` and `tests/test_mcp_fallback_server.py`:

```powershell
cd scripts
pip install -r requirements.txt
pytest ../tests -q
```

- Script modules import without error
- `post_deploy_search.py` REST payload builders produce well-formed JSON matching the documented schema
- `mcp_fallback_server.py`'s retrieval helper returns a non-empty, correctly-shaped result for a mocked Search response

## C — Quality (golden set)

Build a small golden set (10-20 question/answer pairs) from your actual corpus:

| Field | Description |
|---|---|
| `question` | Natural-language developer question |
| `expected_source_document` | The PDF that should be cited |
| `expected_section` | Acceptable heading path for the citation (e.g. `Peripherals > Timer Registers`) |
| `expected_answer_contains` | A substring/fact that must appear in the answer |

**Acceptance thresholds (v1 default — tune per engagement):**

- >= 90% of golden-set questions return a citation from the correct source document
- >= 80% of citations land within the expected page range
- 100% of answers that should be "not found in the corpus" correctly decline rather than hallucinate

Log failures with the actual retrieved chunk(s) — most quality misses trace back to chunk boundaries splitting a table or register definition across two chunks; re-tune the Split skill's page/overlap settings if this recurs.

## D — Native-vs-fallback comparison

If you deployed both MCP paths (Phase 3 native + Phase 4 fallback), run the same golden-set questions against both and diff the citations. They should agree — if they diverge, one path is likely querying a stale or differently-configured Knowledge Base/index reference; re-check both configs against [docs/06](./06-mcp-endpoint-and-fallback-server.md).

## E — End-to-end demo script

| Step | Action | Demo point |
|---|---|---|
| 1 | Open VS Code in a workspace with `.vscode/mcp.json` configured | Zero-friction setup — one file, no separate portal |
| 2 | Open a firmware/hardware source file relevant to the corpus | Grounding happens in-context, not a separate chat window |
| 3 | Ask Copilot Chat (agent mode): "Does this GPIO config match the datasheet's default pin mux?" | Cross-references code + datasheet in one turn |
| 4 | Point out the citation (document, page, section) in the response | Answers are traceable, not hallucinated |
| 5 | Ask a question the corpus does NOT answer | Confirms honest "not found" behavior, not fabrication |

## F — Regression checklist

Re-run categories A + B (minimum) after any change to:

- The skillset (Document Layout / Split / Embedding skill configuration)
- The index schema
- The Knowledge Base configuration
- The fallback MCP server code
- The Azure AI Search API version referenced anywhere in this pattern (preview surfaces move fast — see [docs/06](./06-mcp-endpoint-and-fallback-server.md))

## Test harness layout

```
tests/
├── fixtures/
│   └── sample.pdf                    # small public-domain test document (bring your own)
├── test_post_deploy_search.py        # smoke tests for the ingestion/setup script
└── test_mcp_fallback_server.py       # smoke tests for the fallback MCP server's retrieval helper
```

## When to re-run what

| Trigger | Tests to run |
|---|---|
| First deployment | A, B, C, E |
| Corpus updated (new/changed PDFs) | A, C |
| Skillset/index schema change | A, B, F |
| Search service tier or region change | A, D, F |
| Before a customer-facing demo | E (dry run), spot-check C |

---

*Last updated: 2026-08-18*
