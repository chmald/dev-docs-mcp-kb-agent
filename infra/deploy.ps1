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
. (Join-Path $scriptDir "hooks/common.ps1")

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

# --- Soft-delete guard (shared with the azd preprovision hook) --------------
$foundryName = Get-FoundryAccountName -Environment $Environment -Region $Region
if (-not (Resolve-SoftDeletedFoundry -Name $foundryName -Region $Region -ResourceGroup $ResourceGroup -Mode Prompt)) {
    exit 1
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

# --- Write demo-ids.local.json + store the Search admin key (shared with the azd postprovision hook) ---
Write-DemoIds -Summary $summary -TenantId $account.tenant -SubscriptionId $account.subscription -RepoRoot $repoRoot | Out-Null
$keyStatus = Set-SearchAdminKeySecret -ResourceGroup $ResourceGroup -SearchService $summary.searchService -KeyVault $summary.keyVault
if ($keyStatus -eq 'no-key') {
    Write-Host "Resource IDs were written, but the Search admin key could not be read." -ForegroundColor Red
    exit 1
}

Write-Host "`nDeployment complete. Next:" -ForegroundColor Cyan
if (-not $summary.hybridModelsDeployed) {
    Write-Host "  1. deployHybridModels=false: create the 'vision' and 'sol' deployments by hand (docs/00 § A3)." -ForegroundColor Cyan
} else {
    Write-Host "  1. Model deployments 'embedding', 'chat', 'vision' and 'sol' were created by the Bicep." -ForegroundColor Cyan
}
Write-Host "  2. cd ../scripts && pip install -r requirements.txt" -ForegroundColor Cyan
Write-Host "  3. python hybrid_ingest.py --ids-file $idsLocalPath --split --plan   --source-dir <your-pdfs>" -ForegroundColor Cyan
Write-Host "  4. python hybrid_ingest.py --ids-file $idsLocalPath --split --upload --source-dir <your-pdfs>" -ForegroundColor Cyan
Write-Host "  5. python hybrid_ingest.py --ids-file $idsLocalPath --build" -ForegroundColor Cyan
Write-Host "`n  See docs/00-reproduce-this-demo.md for the full checkpointed walkthrough." -ForegroundColor Cyan
