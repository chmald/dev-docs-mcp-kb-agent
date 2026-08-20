# 00 — Step-by-step reproduction guide

> **Audience.** Someone who wants to clone this repo and stand up the full demo against a fresh Azure subscription + Azure DevOps organisation. Each part below is a discrete checkpoint — finish A before starting B, etc. The deep-dive runbooks (linked inline below) are referenced rather than duplicated.

> **Time budget.** First-time stand-up: roughly 3-4 hours end-to-end, dominated by Azure OpenAI/Search resource provisioning waits and the Phase 3 preview-feature verification. Subsequent reproductions in the same tenant/region: under 1 hour.

---

## What you'll end up with

```
Developer's laptop                         Azure subscription (rg-ddmcp-<env>-<region>)
┌──────────────────────────┐               ┌───────────────────────────────────────────┐
│ VS Code                  │               │  Storage (raw/ container)                  │
│  + GitHub Copilot        │               │       │                                     │
│  + .vscode/mcp.json  ────┼───MCP─────────┼──►  AI Search (index + skillset + indexer)  │
│    (agent mode)          │   (Streamable │       │                                     │
│                          │    HTTP)      │  Knowledge Base ──► native MCP endpoint     │
│                          │               │       │                                     │
│                          │◄──────────────┼── (fallback) Container App: mcp_fallback_    │
│                          │   MCP (alt)   │           server.py, wraps the same          │
│                          │               │           retrieval call                     │
│                          │               │                                             │
│                          │               │  Foundry account: Document Intelligence      │
│                          │               │  Layout model + text-embedding-3-large +     │
│                          │               │  gpt-5-mini (query planning)                │
│                          │               │                                             │
│                          │               │  Key Vault: Search admin key                 │
└──────────────────────────┘               └───────────────────────────────────────────┘
```

---

## Prerequisites checklist (verify before starting Part A)

- [ ] Azure subscription with Contributor + RBAC-admin rights on the target resource group
- [ ] Azure OpenAI model access confirmed for `text-embedding-3-large` and a chat model in your target region (see [02-prerequisites.md § 8](02-prerequisites.md#8--regional--preview-feature-availability))
- [ ] AI Search Standard tier available in that region with semantic ranker
- [ ] **Local tools**: `az` CLI (>= 2.60), PowerShell 7+, Python 3.11+, VS Code with GitHub Copilot (agent mode enabled)
- [ ] A technical document corpus (PDFs) ready to upload — bring your own; see [../samples/README.md](../samples/README.md)

---

## Part A — Provision the platform

### A1. Authenticate to the right tenant/subscription

See [03-deployment.md § Phase 0](03-deployment.md#phase-0--authenticate-to-the-right-tenant) — never trust the ambient `az` login.

### A2. Deploy the Bicep template

```powershell
cd infra
./deploy.ps1 -Environment dev -Region eastus2 -ResourceGroup "rg-ddmcp-dev-eastus2"
```

This provisions Storage, the Foundry multi-service account, AI Search, Key Vault, and RBAC — see [03-deployment.md § Phase 1](03-deployment.md#phase-1--foundation-resources) for the full breakdown.

*Why one Cognitive Services account for both Document Intelligence and embeddings?* See [01-architecture.md § Layer note](01-architecture.md#layer-note--why-a-foundry-multi-service-account-not-standalone-resources) — one resource, one endpoint, one RBAC surface.

---

## Part B — Ingest the corpus

### B1. Upload your PDFs

```powershell
cd ../scripts
pip install -r requirements.txt
python upload_documents.py --ids-file ../demo-ids.local.json --source-dir "<path-to-your-pdfs>"
```

### B2. Create the data source, skillset, index, indexer; run it

```powershell
python post_deploy_search.py --ids-file ../demo-ids.local.json --create-index --create-skillset --create-indexer --run-indexer
python post_deploy_search.py --ids-file ../demo-ids.local.json --indexer-status
```

Full detail: [03-deployment.md § Phase 2](03-deployment.md#phase-2--ingestion-data-source-skillset-index-indexer).

---

## Part C — Stand up retrieval: Knowledge Base + MCP endpoint

### C1. Create the Knowledge Base and check native MCP availability

```powershell
python post_deploy_search.py --ids-file ../demo-ids.local.json --create-knowledge-base
python post_deploy_search.py --ids-file ../demo-ids.local.json --check-mcp-endpoint
```

*See [06-mcp-endpoint-and-fallback-server.md](06-mcp-endpoint-and-fallback-server.md) for the full runbook — endpoint paths, API versions, and the native-vs-fallback decision. Link out, do not duplicate.*

If native MCP is available: skip to Part E. If not: continue to Part D.

---

## Part D — Fallback MCP server (only if Part C's native check failed, or you want both)

### D1. Add the Container App infra and deploy the server

```powershell
cd ../infra
./deploy.ps1 -Environment dev -Region eastus2 -ResourceGroup "rg-ddmcp-dev-eastus2" -DeployFallbackServer
cd ../scripts
./deploy_mcp_server.ps1 -IdsFile ../demo-ids.local.json
```

*See [06-mcp-endpoint-and-fallback-server.md § Deploying the custom wrapper server](06-mcp-endpoint-and-fallback-server.md#5--deploying-the-custom-wrapper-server-to-azure-container-apps) for the full runbook.*

---

## Part E — Wire GitHub Copilot / VS Code

### E1. Add `.vscode/mcp.json` and reload

*See [07-github-copilot-mcp-client-setup.md](07-github-copilot-mcp-client-setup.md) for the full runbook — the exact JSON for both the native and fallback endpoint, and how to verify the connection.*

### E2. Ask a corpus question in Copilot Chat (agent mode)

Confirm the answer cites a source document + heading path.

---

## Single-page checklist

| Part | What | Done |
|---|---|---|
| A | Provision the platform (Bicep: Storage, Foundry, Search, Key Vault, RBAC) | [ ] |
| B | Ingest the corpus (upload, index, skillset, indexer) | [ ] |
| C | Stand up retrieval (Knowledge Base + native MCP check) | [ ] |
| D | Fallback MCP server (only if native unavailable) | [ ] |
| E | Wire GitHub Copilot / VS Code | [ ] |

Once all boxes are checked, run the test plan in [04-testing.md](04-testing.md) before treating this as demo-ready.

---

*Last updated: 2026-08-18*
