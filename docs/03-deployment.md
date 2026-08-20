# 03 — Deployment

Step-by-step build of the Developer Docs MCP Knowledge Base pattern via Bicep/IaC. Assumes all of [02-prerequisites.md](./02-prerequisites.md) is complete.

> **Don't want to use Bicep?** See [03b-manual-deployment.md](./03b-manual-deployment.md) for a complete Azure Portal + imperative CLI alternative that produces the same resources — useful when a customer's environment doesn't allow IaC deployments.

> **Build order matters.** Phases are sequential — each depends on artifacts from the prior phase. For the full "clone and stand up from scratch" experience with time budgets, see [docs/00-reproduce-this-demo.md](./00-reproduce-this-demo.md).

---

## Phase overview

| Phase | What you build | ~Time | Validation at end |
|---|---|---|---|
| 0 | Authenticate to the intended tenant + subscription | 2 min | `az account show` matches target |
| 1 | Foundation resources (RG, Storage, Key Vault, Foundry, Search) via Bicep | 30-45 min | All 5 resources show `Succeeded`; RBAC assigned |
| 2 | Upload sample corpus + create data source, skillset, index, indexer | 30 min | Indexer run shows 0 failed docs; index has documents |
| 3 | Create the Knowledge Base; verify/enable the MCP endpoint (native or confirm fallback) | 30-60 min | Retrieve call returns grounded, cited results |
| 4 | Deploy the fallback MCP server (if native MCP isn't available or you want both) | 45 min | Container App responds to an MCP `tools/list` call |
| 5 | Wire GitHub Copilot / VS Code to the MCP endpoint | 15 min | Copilot Chat (agent mode) answers a corpus question with a citation |

**Total demo build: ~3-4 hours of hands-on time** (dominated by Phase 1 resource provisioning waits and Phase 3's preview-feature verification).

---

## Phase 0 — Authenticate to the right tenant

> **Multi-tenant safety.** The active `az`/`azd` account drifts between tenants. Set it explicitly before building — never trust the ambient login.

```powershell
$TenantId       = "<entra-tenant-guid>"        # from demo-ids.local.json once you have one
$SubscriptionId = "<azure-subscription-guid>"

az account show --query "{tenant:tenantId, subscription:id, name:name}" -o table   # verify
# If it does not match the target:
az login --tenant $TenantId                    # add --use-device-code when headless
az account set --subscription $SubscriptionId
```

### Phase 0 validation

- [ ] `az account show` returns the intended tenant **and** subscription

---

## Phase 1 — Foundation resources

### 1.1 Create the resource group

```powershell
$Env = "dev"; $Region = "eastus2"
az group create --name "rg-ddmcp-$Env-$Region" --location $Region
```

### 1.2 Deploy the Bicep template

```powershell
cd infra
./deploy.ps1 -Environment $Env -Region $Region -ResourceGroup "rg-ddmcp-$Env-$Region"
```

`deploy.ps1` runs `az deployment group create` against `main.bicep`, which provisions (see `infra/modules/`):

- `storage.bicep` — Storage account + `raw` blob container
- `foundry.bicep` — Cognitive Services multi-service account (kind `AIServices`) + `text-embedding-3-large` + chat model deployments
- `search.bicep` — AI Search service (Standard tier, semantic ranker enabled)
- `keyvault.bicep` — Key Vault for the Search admin key + any secrets the fallback server needs
- `rbac.bicep` — role assignments (deployer + Search system-assigned MI → Storage Blob Data Reader; deployer → Key Vault Secrets Officer)
- `containerapp.bicep` — Container Apps environment + registry, only if `-DeployFallbackServer` is passed (Phase 4)

`deploy.ps1` writes the resulting resource names/endpoints into `demo-ids.local.json` (gitignored) automatically.

### 1.3 RBAC wiring

Confirm the automatic role assignments from `rbac.bicep`:

```powershell
az role assignment list --resource-group "rg-ddmcp-$Env-$Region" -o table
```

### Phase 1 validation

- [ ] All 5 (or 6, if `-DeployFallbackServer`) resources show `Succeeded` in `az deployment group show`
- [ ] `demo-ids.local.json` populated with real resource names/endpoints
- [ ] Role assignments present for the Search service MI and your deployer identity

---

## Phase 2 — Ingestion: data source, skillset, index, indexer

### 2.1 Upload your corpus

```powershell
cd ../scripts
pip install -r requirements.txt
python upload_documents.py --ids-file ../demo-ids.local.json --source-dir "<path-to-your-pdfs>"
```

See [samples/README.md](../samples/README.md) if you don't have a corpus ready — do **not** use copyrighted vendor manuals in a shared/customer-facing demo without checking redistribution rights; bring your own or use a public-domain technical document.

### 2.2 Create the data source, skillset, index, and indexer; run the indexer

```powershell
python post_deploy_search.py --ids-file ../demo-ids.local.json --create-index --create-skillset --create-indexer --run-indexer
```

This script (see [scripts/post_deploy_search.py](../scripts/post_deploy_search.py)) calls the AI Search REST API to:

1. Create the `ds-documents-blob` data source pointing at the `raw` container
2. Create the `skillset-documents` skillset: Document Layout skill → Split skill (heading/page-aware) → AOAI Embedding skill vectorizer
3. Create the `idx-documents` index (schema in [01-architecture.md](./01-architecture.md#reference-schemas))
4. Create and run the `ixr-documents` indexer

### 2.3 Confirm ingestion succeeded

```powershell
python post_deploy_search.py --ids-file ../demo-ids.local.json --indexer-status
```

### Phase 2 validation

- [ ] Indexer status shows `success` with 0 failed items (warnings on a handful of pages are common and usually fine — see [05-troubleshooting.md](./05-troubleshooting.md))
- [ ] `idx-documents` document count > 0 (`az search` or the portal Search Explorer)
- [ ] A manual query in Search Explorer returns chunks with populated `sectionH1` / `sectionH2`

---

## Phase 3 — Knowledge Base + MCP endpoint

*Full reference — endpoint paths, API versions, the native-vs-fallback decision, and the exact REST payloads — lives in [docs/06-mcp-endpoint-and-fallback-server.md](./06-mcp-endpoint-and-fallback-server.md). This phase is the short version.*

### 3.1 Create the Knowledge Base

```powershell
python post_deploy_search.py --ids-file ../demo-ids.local.json --create-knowledge-base
```

### 3.2 Verify the native MCP endpoint

```powershell
python post_deploy_search.py --ids-file ../demo-ids.local.json --check-mcp-endpoint
```

If this succeeds, you have a working native MCP endpoint — skip to Phase 5. If it returns a 404 / `FeatureNotEnabled`-style error, the preview surface isn't available on your Search service yet — proceed to Phase 4 for the fallback.

### Phase 3 validation

- [ ] Knowledge Base created (`knowledgebases/kb-documents` returns 200)
- [ ] A direct `retrieve` call against the Knowledge Base returns a grounded answer with citations
- [ ] MCP endpoint check result recorded (native available, or fallback required) — noted in `demo-ids.local.json`

---

## Phase 4 — Optional custom wrapper server (skip if Phase 3's native check succeeded and you don't want it)

*Full reference in [docs/06-mcp-endpoint-and-fallback-server.md](./06-mcp-endpoint-and-fallback-server.md). This wrapper is not required for a working demo — see § 4 there for why you might still want it (token lifecycle, custom processing, network controls).*

```powershell
cd ../infra
./deploy.ps1 -Environment $Env -Region $Region -ResourceGroup "rg-ddmcp-$Env-$Region" -DeployFallbackServer
cd ../scripts
./deploy_mcp_server.ps1 -IdsFile ../demo-ids.local.json
```

`deploy_mcp_server.ps1` builds `mcp_fallback_server.py` into a container image via `az acr build` and deploys/updates the Container App.

### Phase 4 validation

- [ ] Container App shows `Running` / `Succeeded` provisioning state
- [ ] A request against the Container App's `/mcp` endpoint returns a valid `tools/list` response

---

## Phase 5 — Wire GitHub Copilot / VS Code

*Full reference in [docs/07-github-copilot-mcp-client-setup.md](./07-github-copilot-mcp-client-setup.md).*

1. Add a `.vscode/mcp.json` in the target workspace pointing at whichever endpoint you validated (native from Phase 3, or fallback from Phase 4)
2. Reload the VS Code window; confirm the MCP server shows as connected in the Copilot Chat tools list
3. Ask a corpus question in Copilot Chat (agent mode) and confirm the response cites a source document + heading path

### Phase 5 validation

- [ ] MCP server shows connected in VS Code's MCP server list
- [ ] A test question in Copilot Chat returns an answer with a document/section citation traceable back to the source PDF

---

## Post-deployment checklist

- [ ] All Phase 0-5 validation boxes checked
- [ ] Indexer schedule configured if the corpus will be updated regularly (or documented as manual re-run)
- [ ] Cost alert configured on the resource group (see [02-prerequisites.md § 11](./02-prerequisites.md#11--rough-cost-estimate-poc-scale-monthly))
- [ ] `demo-ids.local.json` backed up somewhere safe (not committed) if you'll tear down and rebuild
- [ ] Owner identified for keeping the corpus current as source documents change

---

*Last updated: 2026-08-18*
