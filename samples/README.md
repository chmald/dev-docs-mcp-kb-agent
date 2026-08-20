# Sample corpus guidance

This pattern needs a corpus of source documents to demonstrate against. No sample corpus is bundled in this repo.

**The pipeline is corpus-neutral** — it works for hardware/firmware datasheets, API references, HR policies, contracts, research papers, or any other document set. Retargeting it is a config change in `demo-ids.local.json` (the `corpus` block), not a code change — see [Adapting this pattern to another document corpus](../docs/01-architecture.md#adapting-this-pattern-to-another-document-corpus).

## Bring your own corpus

- Use your own documentation, provided you have the right to store and index it in Azure resources you control
- **Do not** commit copyrighted vendor manuals (e.g., chip vendor datasheets) into this repository or a shared demo environment without confirming redistribution rights — most vendor datasheets are freely distributable for reference use, but check the vendor's terms before including one in a customer-facing or shared repo. `.gitignore` excludes `samples/*.pdf` and `tests/fixtures/*.pdf` so this is default-safe.
- A single 50-200 page document is enough to demonstrate the pattern end-to-end; a handful of documents is enough to demonstrate cross-document retrieval

## Supported file types

PDF is the default (`corpus.sourceFileExtensions`). The Document Intelligence Layout skill also reads Office formats (`.docx`, `.pptx`, `.xlsx`) and images (`.png`, `.jpeg`, `.tiff`) — add those extensions to the `corpus` block when your corpus needs them and `upload_documents.py` picks them up with no code change.

## Tuning chunking for your corpus

Chunk sizing lives in the same `corpus` block (`chunkSizeTokens` / `chunkOverlapTokens`, defaults 1500/200):

- **Dense reference material** (register tables, spec sheets, pricing matrices) → smaller chunks with more overlap, so a table isn't split mid-definition
- **Long-form prose** (policies, contracts, reports) → the defaults or larger, since context spans paragraphs

## If you need a placeholder for a quick smoke test

Use a public-domain or Creative-Commons-licensed document (many open-hardware projects, RFCs, and government technical standards qualify) — place it at `tests/fixtures/sample.pdf` for the automated smoke tests and the functional test in [`../docs/04-testing.md`](../docs/04-testing.md).

## Uploading your corpus

```powershell
cd ../scripts
python upload_documents.py --ids-file ../demo-ids.local.json --source-dir "<path-to-your-documents>"
```

See [`../docs/03-deployment.md` § Phase 2](../docs/03-deployment.md#phase-2--ingestion-data-source-skillset-index-indexer) for the full ingestion flow.

## Building a golden test set

Once your corpus is indexed, build a small golden set of question/answer pairs from it for the quality tests in [`../docs/04-testing.md` § C](../docs/04-testing.md#c--quality-golden-set) — this is what tells you the pattern is actually answering correctly, not just returning *something*.

---

*Last updated: 2026-08-18*
