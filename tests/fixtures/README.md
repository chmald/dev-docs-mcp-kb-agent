# Test fixtures

No sample corpus is bundled in this repo (see [`../../samples/README.md`](../../samples/README.md)).

To exercise the functional test path in [`04-testing.md § A`](../../docs/04-testing.md#a--functional-tests) against a real document, place a small public-domain or Creative-Commons-licensed PDF here as `sample.pdf`:

```
tests/fixtures/sample.pdf
```

This file intentionally has no bundled binary — the automated smoke tests in `test_post_deploy_search.py` and `test_mcp_fallback_server.py` mock the AI Search/MCP responses and do not require a real PDF to pass. `sample.pdf` is only needed for the manual functional/quality tests (categories A and C).

*Last updated: 2026-08-18*
