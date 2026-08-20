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
$deployment = az @deployArgs -o json | ConvertFrom-Json
$summary = $deployment.properties.outputs.deploymentSummary.value

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
az keyvault secret set --vault-name $summary.keyVault --name "search-admin-key" --value $adminKey | Out-Null
Write-Host "Stored Search admin key in Key Vault '$($summary.keyVault)' as secret 'search-admin-key'" -ForegroundColor Green

Write-Host "`nDeployment complete. Next:" -ForegroundColor Cyan
Write-Host "  cd ../scripts && pip install -r requirements.txt" -ForegroundColor Cyan
Write-Host "  python upload_documents.py --ids-file $idsLocalPath --source-dir <your-pdfs>" -ForegroundColor Cyan
