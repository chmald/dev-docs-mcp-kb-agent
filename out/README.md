# out/ — generated artifacts

Scratch output directory. **Everything in here is gitignored except this file.**

`scripts/compare_extraction_tiers.py --report` writes a timestamped pair here on each run:

```
out/extraction-comparison-<YYYY-MM-DD>_<HHMMSS>.json   # raw measurements
out/extraction-comparison-<YYYY-MM-DD>_<HHMMSS>.md     # the readable scorecard
```

## Why these aren't committed

- They embed **deployment-specific values** (search service and index names) and per-run
  measurements that are only meaningful for the environment that produced them.
- Any rendered figure images written here are **derived from the source corpus**, so they carry
  the same redistribution caution as the PDFs themselves — see
  [`../samples/README.md`](../samples/README.md).
- They are cheap to regenerate: point the harness at a deployment and re-run.

## Reference results

The measured results worth keeping — the DI-only vs CU-only vs hybrid comparison, the cost
model, and the model-selection benchmarks — are written up in
[`../docs/08-extraction-tier-comparison.md`](../docs/08-extraction-tier-comparison.md), which
**is** version-controlled. Use that for the customer conversation; use this folder for a
specific run against a specific corpus.

## Regenerating

```powershell
cd scripts
python compare_extraction_tiers.py --ids-file ../demo-ids.local.json --report
```
