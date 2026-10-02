[README](../README.md) › [docs index](./00-reproduce-this-demo.md) › 00 Reproduce this demo

# 00 — Stand this demo up from scratch

<p>
<img src="./assets/icons/subscription.svg" width="40" alt="Azure subscription"/>&nbsp;
<img src="./assets/icons/azure-devops.svg" width="40" alt="Azure Developer CLI"/>&nbsp;
<img src="./assets/icons/ai-search.svg" width="40" alt="Azure AI Search"/>&nbsp;
<img src="./assets/icons/document-intelligence.svg" width="40" alt="Document Intelligence"/>&nbsp;
<img src="./assets/icons/azure-openai.svg" width="40" alt="Azure OpenAI"/>&nbsp;
<img src="./assets/icons/blob-block.svg" width="40" alt="Blob Storage"/>&nbsp;
<img src="./assets/icons/code.svg" width="40" alt="VS Code and GitHub Copilot"/>
</p>

![Version](./assets/badges/version.svg) ![Live-tested](./assets/badges/live-tested.svg) ![Static-only](./assets/badges/static-only.svg) ![Public preview](./assets/badges/public-preview.svg)

The single-page orchestrator for building this demo against a fresh Azure subscription, from empty tenant to a Copilot Chat question answered from a figure. Each of the six parts (A–F) ends in a checkpoint — finish one before starting the next. Deep-dive runbooks are linked rather than duplicated.

## At a glance

| | Topic | One-line answer |
|---|---|---|
| <img src="./assets/icons/subscription.svg" width="24" alt=""/> | **Who it is for** | Someone cloning this repo to build the full demo against a fresh Azure subscription |
| <img src="./assets/icons/azure-devops.svg" width="24" alt=""/> | **Shortcut** | `azd up` replaces A1–A3 ([03 § Fast path](./03-deployment.md#fast-path--azd-up)) ![Static-only](./assets/badges/static-only.svg) |
| <img src="./assets/icons/ai-search.svg" width="24" alt=""/> | **Slow parts** | Model + AI Search provisioning waits, and ingestion, which scales with corpus size |
| <img src="./assets/icons/code.svg" width="24" alt=""/> | **Done when** | A question answerable only from a *figure* returns a grounded, cited answer in Copilot Chat |

> [!NOTE]
> **Audience.** Each part is a checkpoint — finish A before starting B. Deep-dive runbooks are linked rather than duplicated.

## Time budget

First build: **1.5–3 hours**, dominated by (a) model + AI Search provisioning waits and (b) ingestion, which scales with corpus size — a 906-page document with figure verbalization takes ~45 minutes on its own. Subsequent rebuilds in the same tenant: well under an hour.

| Part | | What | Where the time goes |
|---|---|---|---|
| **A** | <img src="./assets/icons/resource-group.svg" width="24" alt=""/> | Provision the platform | Model + AI Search provisioning waits |
| **B** | <img src="./assets/icons/blob-block.svg" width="24" alt=""/> | Route and upload the corpus | Quick; `--plan` is free |
| **C** | <img src="./assets/icons/ai-search.svg" width="24" alt=""/> | Build both tiers and ingest | **Dominant** — Tier DI+ ~45 min for a 906-page spec; Tier CU much faster |
| **D** | <img src="./assets/icons/monitor.svg" width="24" alt=""/> | Verify retrieval and the MCP endpoint | Minutes |
| **E** | <img src="./assets/icons/code.svg" width="24" alt=""/> | Wire GitHub Copilot | Minutes |
| **F** | <img src="./assets/icons/powershell.svg" width="24" alt=""/> | Rehearse; optional repository export | Minutes |

> [!TIP]
> Faster path: `azd up` replaces Parts A1–A3 with one command — see the callout under Part A.

---

## What you end up with

[![Deployed topology](./assets/deployed-topology.png)](./assets/deployed-topology.png)

<sub>Editable source: [`assets/deployed-topology.drawio`](./assets/deployed-topology.drawio).</sub>

Read [01-architecture.md](./01-architecture.md) for *why* ingestion is split into two tiers,
and [08-extraction-tier-comparison.md](./08-extraction-tier-comparison.md) for the measured
evidence and cost model behind that decision.

---

## Before you start

Full detail in [02-prerequisites.md](./02-prerequisites.md). Do not skip the first three —
each has cost a real build time.

| | Prerequisite | Gate |
|---|---|---|
| <img src="./assets/icons/subscription.svg" width="24" alt=""/> | Azure subscription with Contributor + RBAC-admin on the target resource group | ☐ |
| <img src="./assets/icons/ai-search.svg" width="24" alt=""/> | **AI Search regional _capacity_ confirmed** | ☐ |
| <img src="./assets/icons/policy.svg" width="24" alt=""/> | **Storage / Key Vault public network access reachable** | ☐ |
| <img src="./assets/icons/azure-openai.svg" width="24" alt=""/> | Model quota in-region for `text-embedding-3-large` (**Standard** SKU) and the chat/vision models | ☐ |
| <img src="./assets/icons/powershell.svg" width="24" alt=""/> | `az` CLI ≥ 2.60, **PowerShell 7 (`pwsh`)**, Python 3.11+ | ☐ |
| <img src="./assets/icons/code.svg" width="24" alt=""/> | **VS Code with the GitHub Copilot Chat extension** (`github.copilot-chat`) | ☐ |
| <img src="./assets/icons/file.svg" width="24" alt=""/> | A PDF corpus you have the right to index (see [../samples/README.md](../samples/README.md)) | ☐ |

> [!IMPORTANT]
> A region can list Basic as available and still reject AI Search creation with `InsufficientResourcesAvailable` — that is capacity, not quota. Governed subscriptions may force `publicNetworkAccess: Disabled` on Storage / Key Vault and silently revert an override.

- [ ] Azure subscription with Contributor + RBAC-admin on the target resource group
- [ ] **AI Search regional _capacity_ confirmed** — a region can list Basic as available and
      still reject creation with `InsufficientResourcesAvailable`
- [ ] **Storage / Key Vault public network access reachable** — governed subscriptions may
      force `publicNetworkAccess: Disabled` and silently revert an override
- [ ] Model quota in-region for `text-embedding-3-large` (**Standard** SKU) and the chat/vision
      models
- [ ] `az` CLI ≥ 2.60, **PowerShell 7 (`pwsh`)**, Python 3.11+
- [ ] **VS Code with the GitHub Copilot Chat extension** (`github.copilot-chat`) — note
      `ms-azuretools.vscode-azure-github-copilot` is a *different* extension and is not enough
- [ ] A PDF corpus you have the right to index (see [../samples/README.md](../samples/README.md))

### Platform note

Everything here runs on **Windows, Linux, and macOS**. `deploy.ps1` runs under PowerShell 7,
which is cross-platform. Activate the virtual environment first — that is the only step whose
syntax differs per shell — and every command below is then identical everywhere:

```powershell
# PowerShell (any OS)
python -m venv .venv
.venv/Scripts/Activate.ps1        # Windows
# .venv/bin/Activate.ps1          # Linux / macOS
pip install -r scripts/requirements.txt
```

```bash
# bash / zsh
python3 -m venv .venv
source .venv/bin/activate
pip install -r scripts/requirements.txt
```

---

## Part A — Provision the platform

| Step | | Action | Gate |
|---|---|---|---|
| **A1** | <img src="./assets/icons/entra-id.svg" width="24" alt=""/> | Authenticate to the intended tenant/subscription | ☐ `az account show` matches the target |
| **A2** | <img src="./assets/icons/resource-group.svg" width="24" alt=""/> | Deploy the Bicep (`deploy.ps1`) | ☐ `demo-ids.local.json` written |
| **A3** | <img src="./assets/icons/azure-openai.svg" width="24" alt=""/> | Frontier model deployments (usually created by the Bicep) | ☐ `embedding`, `chat`, `sol`, `vision` listed |

> [!TIP]
> **One command instead of A1–A3:** `azd up` provisions everything below, writes
> `demo-ids.local.json` and can ingest your corpus in the same run ![Static-only](./assets/badges/static-only.svg). Steps and settings:
> [03 § Fast path — azd up](./03-deployment.md#fast-path--azd-up) and
> [12-configuration-reference.md](./12-configuration-reference.md). Then continue at Part B
> (or Part C if you set `DEMO_CORPUS_DIR`).

### A1. Authenticate to the intended tenant/subscription

Never trust the ambient `az` login — see
[03-deployment.md § Phase 0](./03-deployment.md#phase-0--authenticate-to-the-right-tenant).

### A2. Deploy the Bicep

```bash
cd infra
./deploy.ps1 -Environment dev -Region eastus -ResourceGroup "rg-ddmcp-dev-eastus"
```

Provisions Storage, the Foundry multi-service account **plus a Foundry project**, AI Search,
Key Vault, and RBAC, then writes `demo-ids.local.json`. Detail:
[03-deployment.md § Phase 1](./03-deployment.md#phase-1--foundation-resources).

### A3. Frontier models (created by the Bicep — usually nothing to do)

The Bicep now creates all four deployments (`embedding`, `chat`, `vision`, `sol`). Run the two
commands below only if you deployed with `deployHybridModels=false` / `DEPLOY_HYBRID_MODELS=false`
(names must match `demo-ids.local.json`):

<details>
<summary><b>The two <code>az cognitiveservices</code> commands (only if <code>deployHybridModels=false</code>)</b></summary>

```bash
# Figure verbalization, BOTH tiers. Deliberately a NON-reasoning model: the
# vision skill has a fixed 30s timeout whose failure mode is total (one slow
# figure fails the whole document), so latency variance matters more than
# capability. See docs/08 § Model selection.
#
# CAPACITY IS A RELIABILITY SETTING HERE, not a cost setting. A deployment's
# requests-per-minute limit scales with capacity (capacity 400 -> 400 req/min),
# AI Search fires figure calls concurrently, and `degreeOfParallelism` was
# removed from the skill in API 2026-04-01 -- so there is no way to throttle
# from the AI Search side. Under-provision this and ingestion fails with a
# misleading 30s *timeout* even though each call takes ~7s.
az cognitiveservices account deployment create -n <foundry> -g <rg> --deployment-name vision --model-name gpt-4.1 --model-version 2025-04-14 --model-format OpenAI --sku-name GlobalStandard --sku-capacity 1000

# Knowledge-base query planning. Frontier -- no timeout pressure here.
az cognitiveservices account deployment create -n <foundry> -g <rg> --deployment-name sol --model-name gpt-5.6-sol --model-version 2026-07-09 --model-format OpenAI --sku-name GlobalStandard --sku-capacity 200
```

</details>

> [!WARNING]
> A brand-new deployment is not immediately usable, and it fails in three different-looking
> ways: `DeploymentIdNotFound`, `FigureUnderstandingSkipped` ("the model deployment returned
> an error"), or a vision-skill `InternalServerError`. All three mean the deployment is not
> serving yet, even though the control plane already reports `Succeeded`. Verify with a direct
> chat-completions call, then reset and re-run the indexers — don't start editing the skillset.

**Checkpoint:** `az cognitiveservices account deployment list` shows `embedding`, `chat`, `sol`, `vision`.

---

## Part B — Route and upload the corpus

| Step | | Action | Gate |
|---|---|---|---|
| **B1** | <img src="./assets/icons/file.svg" width="24" alt=""/> | Split oversized documents (`--split`) ![Default](./assets/badges/default.svg) | ☐ no part over 300 pages |
| **B2** | <img src="./assets/icons/folder.svg" width="24" alt=""/> | See the routing plan (`--plan`, free) | ☐ tier split understood |
| **B3** | <img src="./assets/icons/blob-block.svg" width="24" alt=""/> | Upload into the tier prefixes (`--upload`) | ☐ blobs under `raw/cu/` and/or `raw/di/` |

### B1. Split oversized documents (strongly recommended)

```bash
cd ../scripts
pip install -r requirements.txt
python hybrid_ingest.py --ids-file ../demo-ids.local.json --split --plan --source-dir "<path-to-pdfs>"
```

`--split` breaks any PDF over 300 pages into page-ranged parts
(`manual__p0001-0300.pdf`), which fixes **two independent problems at once**:

- Every part becomes eligible for the **higher-quality Content Understanding tier**, instead of
  the whole manual falling to Document Layout because of the 300-page limit.
- Figure verbalization fires one vision call per figure. A 900-page document produces a burst
  large enough to exhaust the vision deployment's requests-per-minute ceiling, and long runs
  hit transient upstream 500s — and because AI Search treats a document as one unit, a late
  failure **discards the whole document's enrichment**. Smaller parts make failures cheap and
  localised.

Part filenames carry the source page range so citations stay traceable to the original
document. Skip `--split` only if every document is already under 300 pages.

### B2. See the routing plan (free — no service calls)

```bash
python hybrid_ingest.py --ids-file ../demo-ids.local.json --plan --source-dir "<path-to-pdfs>"
```

This is also the number you need for a cost estimate — see
[08 § Cost model](./08-extraction-tier-comparison.md#cost-model).

### B3. Upload into the tier prefixes

```bash
python hybrid_ingest.py --ids-file ../demo-ids.local.json --upload --source-dir "<split-or-original-dir>"
```

If the directory still contains a document over 300 pages, `--upload` **stops before uploading
anything** and asks you to choose: re-run with `--split` (recommended) or `--no-split` (keep it
whole on Tier DI+). Routing an oversized manual is a quality and reliability decision, so the
script no longer makes it silently.

**Checkpoint:** blobs appear under `raw/cu/` and/or `raw/di/`.

---

## Part C — Build both tiers and ingest

| Step | | Action | Gate |
|---|---|---|---|
| **C1** | <img src="./assets/icons/ai-search.svg" width="24" alt=""/> | `--build` — index, skillsets, data sources, indexers, knowledge source and knowledge base | ☐ command completes |
| **C2** | <img src="./assets/icons/monitor.svg" width="24" alt=""/> | Poll `--status` until ingestion finishes | ☐ both indexers `success` |

> [!NOTE]
> **Expect this to take a while** — this is the dominant cost of a first build (see the time budget above).

```bash
python hybrid_ingest.py --ids-file ../demo-ids.local.json --build
python hybrid_ingest.py --ids-file ../demo-ids.local.json --status
```

`--build` creates the unified index, both skillsets, both data sources, both indexers, the
knowledge source and the knowledge base, then starts ingestion. Poll `--status` until both
tiers report `success`.

**Expect this to take a while.** Tier DI+ makes one vision call per extracted figure; a
906-page specification took ~45 minutes. Tier CU is much faster.

**Checkpoint:** both indexers `success`, and the index contains rows from every tier you
expected:

```bash
python hybrid_ingest.py --ids-file ../demo-ids.local.json --status
```

Full detail: [03-deployment.md § Phase 2](./03-deployment.md#phase-2--hybrid-ingestion).

---

## Part D — Verify retrieval and the MCP endpoint

| Step | | Action | Gate |
|---|---|---|---|
| **D1** | <img src="./assets/icons/ai-search.svg" width="24" alt=""/> | `post_deploy_search.py --check-mcp-endpoint` ![Live-tested](./assets/badges/live-tested.svg) | ☐ native MCP endpoint answers |
| **D2** | <img src="./assets/icons/dev-console.svg" width="24" alt=""/> | Run the [04-testing.md](./04-testing.md) test plan | ☐ both tiers contribute (tier-provenance check) |

```bash
python post_deploy_search.py --ids-file ../demo-ids.local.json --check-mcp-endpoint
```

Then run the test plan in [04-testing.md](./04-testing.md) — functional, chunk-quality, and
golden-set checks, including the tier-provenance check that proves both tiers are contributing.

---

## Part E — Wire GitHub Copilot

| Step | | Action | Gate |
|---|---|---|---|
| **E1** | <img src="./assets/icons/code.svg" width="24" alt=""/> | Add `.vscode/mcp.json` pointing at the hybrid knowledge base; reload VS Code | ☐ server listed in Copilot Chat |
| **E2** | <img src="./assets/icons/file.svg" width="24" alt=""/> | Ask a figure-only corpus question in agent mode | ☐ grounded, cited answer |

> [!NOTE]
> Requires `github.copilot-chat` — `ms-azuretools.vscode-azure-github-copilot` is a *different* extension and is not enough.

Add `.vscode/mcp.json` pointing at the **hybrid** knowledge base and reload VS Code, then ask
a corpus question in Copilot Chat (agent mode). Exact JSON and verification steps:
[07-github-copilot-mcp-client-setup.md](./07-github-copilot-mcp-client-setup.md).

**Checkpoint:** a question answerable only from a *figure* returns a grounded answer citing
the source document — that is the whole pattern working end to end.

---

## Part F — Rehearse, and optionally export to a repository

| Step | | Action | Gate |
|---|---|---|---|
| **F1** | <img src="./assets/icons/powershell.svg" width="24" alt=""/> | Run `demo_walkthrough.py` ![Static-only](./assets/badges/static-only.svg) | ☐ `5/5 steps passed` |
| **F2** | <img src="./assets/icons/folder.svg" width="24" alt=""/> | *(Optional)* export the corpus to a repository ![Opt-in](./assets/badges/opt-in.svg) | ☐ `out/repo-export/README.md` lists every document |

```bash
python demo_walkthrough.py --ids-file ../demo-ids.local.json --script ../samples/walkthrough.example.json --report
```

**Checkpoint:** `5/5 steps passed`. Full detail: [11-customer-walkthrough.md](./11-customer-walkthrough.md).

Optional — if developers want the documents themselves in their repo
([10-repo-corpus-export.md](./10-repo-corpus-export.md)):

```bash
python export_repo_corpus.py --ids-file ../demo-ids.local.json --analyze --source-dir "<split-or-original-dir>"
python export_repo_corpus.py --ids-file ../demo-ids.local.json --export --out ../out/repo-export
```

**Checkpoint:** `out/repo-export/README.md` lists every document with section, figure and table counts.

---

## Single-page checklist

| Part | | What | Done |
|---|---|---|---|
| A | <img src="./assets/icons/resource-group.svg" width="24" alt=""/> | Provision platform (Bicep) + frontier model deployments | [ ] |
| B | <img src="./assets/icons/blob-block.svg" width="24" alt=""/> | Route by page count and upload to tier prefixes | [ ] |
| C | <img src="./assets/icons/ai-search.svg" width="24" alt=""/> | Build both tiers, ingest, confirm both indexers succeed | [ ] |
| D | <img src="./assets/icons/monitor.svg" width="24" alt=""/> | Verify retrieval + native MCP endpoint | [ ] |
| E | <img src="./assets/icons/code.svg" width="24" alt=""/> | Wire GitHub Copilot and ask a figure-only question | [ ] |
| F | <img src="./assets/icons/powershell.svg" width="24" alt=""/> | Walkthrough passes 5/5; *(optional)* repository export generated | [ ] |

Then run [04-testing.md](./04-testing.md) before calling it demo-ready. If anything fails,
[05-troubleshooting.md](./05-troubleshooting.md) is organised by symptom.

---

## Tearing down

```bash
python hybrid_ingest.py --ids-file ../demo-ids.local.json --teardown   # Search objects only
az group delete --name rg-ddmcp-dev-eastus --yes --no-wait             # everything
```

> [!CAUTION]
> AI Search Basic and the model deployments bill continuously — tear down when the demo is not
> in use. `az group delete` removes everything in the resource group.

---

Next: [01 - Architecture](./01-architecture.md) →

*Last updated: 2026-10-02*
