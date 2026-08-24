<#
.SYNOPSIS
    Deploys the Developer Docs MCP Knowledge Agent pattern's Azure infrastructure.
.DESCRIPTION
    Runs `az deployment group create` against infra/main.bicep, then writes the
    resulting resource names/endpoints into ../demo-ids.local.json (gitignored).
    Also stores the AI Search admin key into the deployed Key Vault.
.PARAMETER Environment
    Environment token (dev, test, prod). Default: dev.
.PARAMETER Region
    Azure region to deploy into. Default: eastus2.
.PARAMETER ResourceGroup
    Target resource group name. Created if it doesn't exist.
.PARAMETER DeployFallbackServer
    Also provision the Container Apps environment + registry + app for the
    fallback MCP server (Phase 4 / Part D). Omit if the native Knowledge Agent
    MCP endpoint is already confirmed available for your Search service.
.PARAMETER WhatIf
    Run `az deployment group what-if` instead of a real deployment.
.EXAMPLE
    ./deploy.ps1 -Environment dev -Region eastus2 -ResourceGroup rg-ddmcp-dev-eastus2
.EXAMPLE
    ./deploy.ps1 -Environment dev -Region eastus2 -ResourceGroup rg-ddmcp-dev-eastus2 -DeployFallbackServer
#>
[CmdletBinding()]
param(
    [string]$Environment = "dev",
    [string]$Region = "eastus2",
    [Parameter(Mandatory = $true)][string]$ResourceGroup,
    [switch]$DeployFallbackServer,
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$idsLocalPath = Join-Path $repoRoot "demo-ids.local.json"
$idsTemplatePath = Join-Path $repoRoot "demo-ids.template.json"

Write-Host "== Developer Docs MCP Knowledge Agent -- infra deploy ==" -ForegroundColor Cyan

# --- Verify auth context (never trust the ambient az login -- see azure-cli-auth instruction) ---
$account = az account show --query "{tenant:tenantId, subscription:id, name:name}" -o json | ConvertFrom-Json
Write-Host "Active account: tenant=$($account.tenant) subscription=$($account.subscription) ($($account.name))" -ForegroundColor Yellow
$confirm = Read-Host "Is this the INTENDED tenant/subscription for this deploy? (y/N)"
if ($confirm -ne "y") {
    Write-Host "Aborting. Run 'az login --tenant <id>' and 'az account set --subscription <id>' for the correct target, then re-run." -ForegroundColor Red
    exit 1
}

# --- Ensure resource group exists ---
$rgExists = az group exists --name $ResourceGroup
if ($rgExists -eq "false") {
    Write-Host "Creating resource group $ResourceGroup in $Region..."
    az group create --name $ResourceGroup --location $Region | Out-Null
}

# --- Soft-delete guard -------------------------------------------------------
# Deleting the resource group SOFT-deletes Cognitive Services accounts. Redeploying
# with the same name then fails preflight with FlagMustBeSetForRestore, which reads
# like a template bug rather than leftover state. Detect it and offer to purge.
$foundryName = "aif-ddmcp-$Environment-$Region"
$deleted = az cognitiveservices account list-deleted --query "[?name=='$foundryName'] | [0].name" -o tsv 2>$null
if ($deleted) {
    Write-Host "`nA soft-deleted Cognitive Services account named '$foundryName' still exists." -ForegroundColor Yellow
    Write-Host "Redeploying with the same name will fail preflight until it is purged or restored." -ForegroundColor Yellow
    $purge = Read-Host "Purge it now and continue? (y/N)"
    if ($purge -eq "y") {
        Write-Host "Purging $foundryName..."
        az cognitiveservices account purge --location $Region --resource-group $ResourceGroup --name $foundryName | Out-Null
        Write-Host "Purged. Note the purge can take a minute to propagate; re-run this script if the deploy still reports FlagMustBeSetForRestore." -ForegroundColor Green
    } else {
        Write-Host "Aborting. Purge manually with:" -ForegroundColor Red
        Write-Host "  az cognitiveservices account purge --location $Region --resource-group $ResourceGroup --name $foundryName" -ForegroundColor Red
        exit 1
    }
}

# --- Resolve the deployer's principal ID for Key Vault + Storage RBAC ---
$deployerPrincipalId = az ad signed-in-user show --query id -o tsv 2>$null
$deployerPrincipalType = "User"
if (-not $deployerPrincipalId) {
    Write-Host "Could not resolve a signed-in user (likely running as a service principal). Falling back to SP lookup." -ForegroundColor Yellow
    $spInfo = az account show --query "user" -o json | ConvertFrom-Json
    $deployerPrincipalId = az ad sp show --id $spInfo.name --query id -o tsv 2>$null
    $deployerPrincipalType = "ServicePrincipal"
}

# --- Deploy (or what-if) ---
$mode = if ($WhatIf) { "what-if" } else { "create" }
$deployArgs = @(
    "deployment", "group", $mode,
    "--resource-group", $ResourceGroup,
    "--template-file", (Join-Path $scriptDir "main.bicep"),
    "--parameters", (Join-Path $scriptDir "main.parameters.json"),
    "--parameters", "environment=$Environment", "location=$Region",
    "--parameters", "deployFallbackServer=$($DeployFallbackServer.IsPresent)",
    "--parameters", "deployerPrincipalId=$deployerPrincipalId", "deployerPrincipalType=$deployerPrincipalType"
)

if ($WhatIf) {
    az @deployArgs
    exit 0
}

Write-Host "Deploying main.bicep to $ResourceGroup..." -ForegroundColor Cyan

# Tearing down a resource group leaves background deletes running. Redeploying
# with the same names can then transiently fail on:
#   ServiceDeleting        - AI Search name still held by an in-flight delete
#   FlagMustBeSetForRestore - Cognitive Services purge not yet propagated
# Neither is a template problem and both clear on their own, so retry with
# backoff rather than making the operator re-run manually.
$maxAttempts = 5
$attempt = 0
while ($true) {
    $attempt++
    $deploymentJson = az @deployArgs -o json
    if ($LASTEXITCODE -eq 0) { break }

    $transient = $deploymentJson -match 'ServiceDeleting|FlagMustBeSetForRestore|another operation is in progress'
    if ($transient -and $attempt -lt $maxAttempts) {
        $wait = 60 * $attempt
        Write-Host "Transient teardown conflict (attempt $attempt/$maxAttempts) - a previous delete is still finishing." -ForegroundColor Yellow
        Write-Host "Retrying in $wait seconds..." -ForegroundColor Yellow
        Start-Sleep -Seconds $wait
        continue
    }

    Write-Host "`nDeployment FAILED (az exit code $LASTEXITCODE). No IDs were written." -ForegroundColor Red
    if ($transient) {
        Write-Host "Still blocked by an in-flight delete after $maxAttempts attempts." -ForegroundColor Red
        Write-Host "Wait a few more minutes and re-run, or deploy with a different -Environment/-Region token." -ForegroundColor Red
    }
    Write-Host "Inspect the failed operations with:" -ForegroundColor Red
    Write-Host "  az deployment operation group list --resource-group $ResourceGroup --name main -o table" -ForegroundColor Red
    exit 1
}

$deployment = $deploymentJson | ConvertFrom-Json
$summary = $deployment.properties.outputs.deploymentSummary.value
if (-not $summary -or -not $summary.searchService -or -not $summary.keyVault) {
    Write-Host "`nDeployment returned no usable deploymentSummary output. Aborting before writing IDs." -ForegroundColor Red
    exit 1
}

# --- Merge into demo-ids.local.json (preserve any manually-populated fields) ---
$ids = if (Test-Path $idsLocalPath) {
    Get-Content $idsLocalPath -Raw | ConvertFrom-Json -AsHashtable
} else {
    Get-Content $idsTemplatePath -Raw | ConvertFrom-Json -AsHashtable
}

$ids["resourceGroup"]                = $summary.resourceGroup
$ids["region"]                       = $summary.region
$ids["environment"]                  = $summary.environment
$ids["storageAccount"]               = $summary.storageAccount
$ids["blobEndpoint"]                 = $summary.blobEndpoint
$ids["rawContainer"]                 = $summary.rawContainer
$ids["foundryResource"]              = $summary.foundryResource
$ids["foundryOpenAIEndpoint"]        = $summary.foundryOpenAIEndpoint
$ids["documentIntelligenceEndpoint"] = $summary.documentIntelligenceEndpoint
$ids["aiServicesSubdomainUrl"]       = $summary.aiServicesSubdomainUrl
$ids["foundryProject"]               = $summary.foundryProject
$ids["embeddingDeployment"]          = $summary.embeddingDeployment
$ids["chatDeployment"]               = $summary.chatDeployment
$ids["searchService"]                = $summary.searchService
$ids["searchEndpoint"]               = $summary.searchEndpoint
$ids["searchPrincipalId"]            = $summary.searchPrincipalId
$ids["keyVault"]                     = $summary.keyVault
$ids["keyVaultUri"]                  = $summary.keyVaultUri
$ids["tenantId"]                     = $account.tenant
$ids["subscriptionId"]               = $account.subscription

if ($summary.fallbackServerDeployed) {
    if (-not $ids.ContainsKey("_fallback_mcp_server_fields_populated_only_if_deployed")) {
        $ids["_fallback_mcp_server_fields_populated_only_if_deployed"] = @{}
    }
    $ids["_fallback_mcp_server_fields_populated_only_if_deployed"]["containerRegistry"] = $summary.containerRegistry
    $ids["_fallback_mcp_server_fields_populated_only_if_deployed"]["containerAppFqdn"]  = $summary.containerAppFqdn
}

$ids | ConvertTo-Json -Depth 10 | Set-Content -Path $idsLocalPath -Encoding utf8
Write-Host "Wrote resource IDs to $idsLocalPath" -ForegroundColor Green

# --- Store the Search admin key in Key Vault ---
$adminKey = az search admin-key show --resource-group $ResourceGroup --service-name $summary.searchService --query primaryKey -o tsv
if ($LASTEXITCODE -ne 0 -or -not $adminKey) {
    Write-Host "Could not read the Search admin key for '$($summary.searchService)'. Resource IDs were written, but the Key Vault secret was NOT set." -ForegroundColor Red
    exit 1
}
az keyvault secret set --vault-name $summary.keyVault --name "search-admin-key" --value $adminKey | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "WARNING: could not write 'search-admin-key' to Key Vault '$($summary.keyVault)'." -ForegroundColor Yellow
    Write-Host "  This is expected in subscriptions where policy forces Key Vault publicNetworkAccess=Disabled." -ForegroundColor Yellow
    Write-Host "  The setup scripts fall back to 'az search admin-key show', so the build can continue." -ForegroundColor Yellow
} else {
    Write-Host "Stored Search admin key in Key Vault '$($summary.keyVault)' as secret 'search-admin-key'" -ForegroundColor Green
}

Write-Host "`nDeployment complete. Next:" -ForegroundColor Cyan
Write-Host "  1. Deploy the two model deployments the hybrid ingestion path needs:" -ForegroundColor Cyan
Write-Host "     # Figure verbalization (both tiers) -- NON-reasoning model on purpose:" -ForegroundColor Cyan
Write-Host "     # the vision skill has a fixed 30s timeout whose failure mode is total." -ForegroundColor Cyan
Write-Host "     az cognitiveservices account deployment create -n $($summary.foundryResource) -g $ResourceGroup ``" -ForegroundColor Cyan
Write-Host "       --deployment-name vision --model-name gpt-4.1 --model-version 2025-04-14 ``" -ForegroundColor Cyan
Write-Host "       --model-format OpenAI --sku-name GlobalStandard --sku-capacity 400" -ForegroundColor Cyan
Write-Host "     # Knowledge-base query planning -- frontier, no timeout pressure:" -ForegroundColor Cyan
Write-Host "     az cognitiveservices account deployment create -n $($summary.foundryResource) -g $ResourceGroup ``" -ForegroundColor Cyan
Write-Host "       --deployment-name sol --model-name gpt-5.6-sol --model-version 2026-07-09 ``" -ForegroundColor Cyan
Write-Host "       --model-format OpenAI --sku-name GlobalStandard --sku-capacity 200" -ForegroundColor Cyan
Write-Host "  2. cd ../scripts && pip install -r requirements.txt" -ForegroundColor Cyan
Write-Host "  3. python hybrid_ingest.py --ids-file $idsLocalPath --plan   --source-dir <your-pdfs>" -ForegroundColor Cyan
Write-Host "  4. python hybrid_ingest.py --ids-file $idsLocalPath --upload --source-dir <your-pdfs>" -ForegroundColor Cyan
Write-Host "  5. python hybrid_ingest.py --ids-file $idsLocalPath --build" -ForegroundColor Cyan
Write-Host "`n  See docs/00-reproduce-this-demo.md for the full checkpointed walkthrough." -ForegroundColor Cyan
