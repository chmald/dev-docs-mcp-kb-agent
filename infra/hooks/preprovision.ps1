#!/usr/bin/env pwsh
# azd preprovision hook -- runs before `azd provision` / `azd up` deploys infra/azd.bicep.
#
#  1. Environment-name guard: the azd env name becomes part of every resource name,
#     and some resource types (Storage, Key Vault, Foundry subdomain) reject
#     uppercase, symbols or long names. Fail fast with a clear message instead of
#     a cryptic ARM validation error ten minutes in.
#  2. Tenant guard: azd and the Azure CLI keep SEPARATE logins. azd provisions
#     with its own; the postprovision hook and every Python script use `az`.
#     Both must point at the same tenant + subscription, or the scripts operate
#     on the wrong account. Never trust the ambient `az` login.
#  3. Soft-delete guard: a soft-deleted Foundry account with the same name makes
#     provisioning fail with FlagMustBeSetForRestore. Purge it when
#     DEMO_PURGE_SOFT_DELETED=true, otherwise stop and print the command.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')

function Stop-Hook([string]$Message) {
    Write-Host "`n[preprovision] $Message" -ForegroundColor Red
    exit 1
}

$envName = $env:AZURE_ENV_NAME
$location = $env:AZURE_LOCATION
$subscription = $env:AZURE_SUBSCRIPTION_ID
$tenant = $env:AZURE_TENANT_ID
$workload = if ($env:WORKLOAD_NAME) { $env:WORKLOAD_NAME } else { 'ddmcp' }

# --- 1. environment name ---------------------------------------------------
if ($envName -notmatch '^[a-z0-9]{1,12}$') {
    Stop-Hook ("azd environment name '$envName' must be 1-12 lowercase letters/digits (it is embedded in " +
        "Storage, Key Vault and Foundry names). Create a new one: azd env new dev")
}
if (-not $location -or -not $subscription) {
    Stop-Hook "Set the target first: azd env set AZURE_SUBSCRIPTION_ID <id>; azd env set AZURE_LOCATION <region> (see docs/12)."
}

# --- 2. tenant / subscription --------------------------------------------
$account = az account show --query "{tenant:tenantId, subscription:id, name:name}" -o json 2>$null | ConvertFrom-Json
if (-not $account) {
    Stop-Hook "The Azure CLI is not logged in. Run: az login --tenant <tenant-id>; az account set --subscription $subscription"
}
Write-Host "[preprovision] azd target : subscription=$subscription tenant=$(if ($tenant) { $tenant } else { '<not set>' })" -ForegroundColor Yellow
Write-Host "[preprovision] az account : subscription=$($account.subscription) tenant=$($account.tenant) ($($account.name))" -ForegroundColor Yellow
if ($account.subscription -ne $subscription) {
    Stop-Hook ("The Azure CLI is on a different subscription than the azd environment. The hooks and scripts use az, so align it:`n" +
        "  az login --tenant $(if ($tenant) { $tenant } else { '<tenant-id>' })`n  az account set --subscription $subscription")
}
if ($tenant -and $account.tenant -ne $tenant) {
    Stop-Hook "The Azure CLI is in tenant $($account.tenant) but AZURE_TENANT_ID is $tenant. Run: az login --tenant $tenant"
}
if (-not $tenant) {
    Write-Host "[preprovision] Tip: pin the tenant with 'azd env set AZURE_TENANT_ID $($account.tenant)' so a later login drift is caught." -ForegroundColor Yellow
}

# --- 3. soft-deleted Foundry account --------------------------------------
$rg = if ($env:AZURE_RESOURCE_GROUP) { $env:AZURE_RESOURCE_GROUP } else { "rg-$workload-$envName-$location" }
$foundry = Get-FoundryAccountName -Workload $workload -Environment $envName -Region $location
$mode = if ("$env:DEMO_PURGE_SOFT_DELETED" -eq 'true') { 'Purge' } else { 'Fail' }
if (-not (Resolve-SoftDeletedFoundry -Name $foundry -Region $location -ResourceGroup $rg -Mode $mode)) {
    Stop-Hook "Soft-deleted Foundry account '$foundry' blocks provisioning (see above)."
}

Write-Host "[preprovision] Checks passed -- provisioning rg '$rg' in $location." -ForegroundColor Green
