#!/usr/bin/env pwsh
# azd postprovision hook -- runs after infra/azd.bicep is deployed.
#
#  1. Writes demo-ids.local.json from the Bicep outputs (azd exports them as
#     environment variables), so every script in scripts/ works unchanged.
#  2. Stores the AI Search admin key in Key Vault (a warning, not a failure, when
#     policy blocks Key Vault public access -- the scripts fall back to az).
#  3. Optional: when DEMO_CORPUS_DIR is set, routes, uploads and builds the hybrid
#     index in the same run, so `azd up` ends with a working knowledge base.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot '..' '..')

function Get-Out([string]$Name) { [Environment]::GetEnvironmentVariable($Name) }

$summary = [ordered]@{
    resourceGroup                = Get-Out 'AZURE_RESOURCE_GROUP'
    region                       = Get-Out 'AZURE_LOCATION'
    environment                  = Get-Out 'DEMO_ENVIRONMENT'
    storageAccount               = Get-Out 'STORAGE_ACCOUNT'
    blobEndpoint                 = Get-Out 'BLOB_ENDPOINT'
    rawContainer                 = Get-Out 'RAW_CONTAINER'
    foundryResource              = Get-Out 'FOUNDRY_RESOURCE'
    foundryOpenAIEndpoint        = Get-Out 'FOUNDRY_OPENAI_ENDPOINT'
    documentIntelligenceEndpoint = Get-Out 'DOCUMENT_INTELLIGENCE_ENDPOINT'
    aiServicesSubdomainUrl       = Get-Out 'AI_SERVICES_SUBDOMAIN_URL'
    foundryProject               = Get-Out 'FOUNDRY_PROJECT'
    embeddingDeployment          = Get-Out 'EMBEDDING_DEPLOYMENT'
    chatDeployment               = Get-Out 'CHAT_DEPLOYMENT'
    visionDeployment             = Get-Out 'VISION_DEPLOYMENT'
    frontierDeployment           = Get-Out 'FRONTIER_DEPLOYMENT'
    frontierModel                = Get-Out 'FRONTIER_MODEL'
    searchService                = Get-Out 'SEARCH_SERVICE'
    searchEndpoint               = Get-Out 'SEARCH_ENDPOINT'
    searchPrincipalId            = Get-Out 'SEARCH_PRINCIPAL_ID'
    keyVault                     = Get-Out 'KEY_VAULT'
    keyVaultUri                  = Get-Out 'KEY_VAULT_URI'
    fallbackServerDeployed       = Get-Out 'FALLBACK_SERVER_DEPLOYED'
    containerAppFqdn             = Get-Out 'CONTAINER_APP_FQDN'
    containerRegistry            = Get-Out 'CONTAINER_REGISTRY'
}
if (-not $summary.searchService -or -not $summary.keyVault) {
    Write-Host "[postprovision] Bicep outputs are missing from the azd environment (SEARCH_SERVICE / KEY_VAULT). Nothing written." -ForegroundColor Red
    exit 1
}

$tenant = if (Get-Out 'AZURE_TENANT_ID') { Get-Out 'AZURE_TENANT_ID' } else { az account show --query tenantId -o tsv }
$idsPath = Write-DemoIds -Summary $summary -TenantId $tenant -SubscriptionId (Get-Out 'AZURE_SUBSCRIPTION_ID') -RepoRoot $repoRoot
if ((Set-SearchAdminKeySecret -ResourceGroup $summary.resourceGroup -SearchService $summary.searchService -KeyVault $summary.keyVault) -eq 'no-key') {
    Write-Host "[postprovision] Could not read the Search admin key -- check the az login matches the azd environment." -ForegroundColor Red
    exit 1
}

# --- optional: ingest a corpus in the same run -----------------------------
$corpus = Get-Out 'DEMO_CORPUS_DIR'
if ($corpus) {
    if (-not (Test-Path $corpus)) {
        Write-Host "[postprovision] DEMO_CORPUS_DIR '$corpus' does not exist -- skipping ingestion." -ForegroundColor Yellow
    } else {
        $python = if (Get-Out 'DEMO_PYTHON') { Get-Out 'DEMO_PYTHON' } else { 'python' }
        $splitFlag = if ("$(Get-Out 'DEMO_SPLIT')" -eq 'false') { '--no-split' } else { '--split' }
        $ingest = Join-Path $repoRoot 'scripts' 'hybrid_ingest.py'
        Write-Host "[postprovision] Ingesting $corpus ($splitFlag) ..." -ForegroundColor Cyan
        & $python $ingest --ids-file $idsPath $splitFlag --upload --source-dir $corpus
        if ($LASTEXITCODE -ne 0) { Write-Host "[postprovision] Upload failed -- fix and re-run: azd hooks run postprovision" -ForegroundColor Red; exit 1 }
        & $python $ingest --ids-file $idsPath --build
        if ($LASTEXITCODE -ne 0) { Write-Host "[postprovision] Build failed -- see the error above." -ForegroundColor Red; exit 1 }
        Write-Host "[postprovision] Ingestion started. Poll: python scripts/hybrid_ingest.py --ids-file demo-ids.local.json --status" -ForegroundColor Green
    }
}

Write-Host "`n[postprovision] Done. Next:" -ForegroundColor Cyan
if (-not $corpus) {
    Write-Host "  python scripts/hybrid_ingest.py --ids-file demo-ids.local.json --split --upload --source-dir <your-pdfs>" -ForegroundColor Cyan
    Write-Host "  python scripts/hybrid_ingest.py --ids-file demo-ids.local.json --build" -ForegroundColor Cyan
}
Write-Host "  Wire Copilot: docs/07 · Rehearse: python scripts/demo_walkthrough.py --ids-file demo-ids.local.json --script samples/walkthrough.example.json" -ForegroundColor Cyan
Write-Host "  Tear down:    azd down --purge" -ForegroundColor Cyan
