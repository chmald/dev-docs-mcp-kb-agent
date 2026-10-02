# Shared helpers for infra/deploy.ps1 and the azd hooks (infra/hooks/*.ps1).
# Both deployment paths provision main.bicep; keeping these steps in one place
# guarantees they produce the same demo-ids.local.json and Key Vault secret.
# Requires PowerShell 7+ (pwsh) and the Azure CLI.

Set-StrictMode -Version Latest

function Get-FoundryAccountName {
    param([string]$Workload = 'ddmcp', [Parameter(Mandatory)][string]$Environment, [Parameter(Mandatory)][string]$Region)
    # Must match main.bicep: var foundryAccountName = 'aif-${workload}-${environment}-${location}'
    return "aif-$Workload-$Environment-$Region"
}

function Resolve-SoftDeletedFoundry {
    <#
    Deleting a resource group SOFT-deletes Cognitive Services accounts. Redeploying the
    same name then fails preflight with FlagMustBeSetForRestore, which reads like a
    template bug. Returns $true when it is safe to continue.
      -Mode Prompt : ask the operator (deploy.ps1, interactive)
      -Mode Purge  : purge without asking (azd: DEMO_PURGE_SOFT_DELETED=true)
      -Mode Fail   : print the purge command and return $false
    #>
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Region,
        [Parameter(Mandatory)][string]$ResourceGroup,
        [ValidateSet('Prompt', 'Purge', 'Fail')][string]$Mode = 'Prompt'
    )
    $deleted = az cognitiveservices account list-deleted --query "[?name=='$Name'] | [0].name" -o tsv 2>$null
    if (-not $deleted) { return $true }

    $purgeCmd = "az cognitiveservices account purge --location $Region --resource-group $ResourceGroup --name $Name"
    Write-Host "`nA soft-deleted Cognitive Services account named '$Name' still exists." -ForegroundColor Yellow
    Write-Host "Redeploying with the same name will fail preflight until it is purged or restored." -ForegroundColor Yellow
    $doPurge = switch ($Mode) {
        'Purge'  { $true }
        'Prompt' { (Read-Host "Purge it now and continue? (y/N)") -eq 'y' }
        default  { $false }
    }
    if (-not $doPurge) {
        Write-Host "Purge it manually (or set DEMO_PURGE_SOFT_DELETED=true for azd), then re-run:" -ForegroundColor Red
        Write-Host "  $purgeCmd" -ForegroundColor Red
        return $false
    }
    Write-Host "Purging $Name..."
    az cognitiveservices account purge --location $Region --resource-group $ResourceGroup --name $Name | Out-Null
    Write-Host "Purged. Propagation can take a minute; if provisioning still reports FlagMustBeSetForRestore, wait and re-run." -ForegroundColor Green
    return $true
}

function Write-DemoIds {
    <#
    Merge deployment outputs into demo-ids.local.json (gitignored), preserving any
    manually-populated fields. $Summary carries the keys of main.bicep's
    deploymentSummary output (any object with those properties works).
    #>
    param(
        [Parameter(Mandatory)]$Summary,
        [Parameter(Mandatory)][string]$TenantId,
        [Parameter(Mandatory)][string]$SubscriptionId,
        [Parameter(Mandatory)][string]$RepoRoot
    )
    $idsLocalPath = Join-Path $RepoRoot 'demo-ids.local.json'
    $fromTemplate = -not (Test-Path $idsLocalPath)
    $source = if ($fromTemplate) { Join-Path $RepoRoot 'demo-ids.template.json' } else { $idsLocalPath }
    $ids = Get-Content $source -Raw | ConvertFrom-Json -AsHashtable
    if ($fromTemplate) { $ids.Remove('_template') | Out-Null }

    $keys = @(
        'resourceGroup', 'region', 'environment', 'storageAccount', 'blobEndpoint', 'rawContainer',
        'foundryResource', 'foundryOpenAIEndpoint', 'documentIntelligenceEndpoint', 'aiServicesSubdomainUrl',
        'foundryProject', 'embeddingDeployment', 'chatDeployment', 'visionDeployment', 'frontierDeployment',
        'frontierModel', 'searchService', 'searchEndpoint', 'searchPrincipalId', 'keyVault', 'keyVaultUri'
    )
    foreach ($key in $keys) {
        $prop = $Summary.PSObject.Properties[$key]
        $value = if ($Summary -is [System.Collections.IDictionary]) { $Summary[$key] } elseif ($prop) { $prop.Value } else { $null }
        if ($null -ne $value -and "$value" -ne '') { $ids[$key] = $value }
    }
    $ids['tenantId'] = $TenantId
    $ids['subscriptionId'] = $SubscriptionId

    $fallback = if ($Summary -is [System.Collections.IDictionary]) { $Summary['fallbackServerDeployed'] } else { $Summary.fallbackServerDeployed }
    if ("$fallback" -eq 'True' -or "$fallback" -eq 'true') {
        $bucket = '_fallback_mcp_server_fields_populated_only_if_deployed'
        if (-not $ids.ContainsKey($bucket) -or $ids[$bucket] -isnot [System.Collections.IDictionary]) { $ids[$bucket] = @{} }
        $ids[$bucket]['containerRegistry'] = if ($Summary -is [System.Collections.IDictionary]) { $Summary['containerRegistry'] } else { $Summary.containerRegistry }
        $ids[$bucket]['containerAppFqdn'] = if ($Summary -is [System.Collections.IDictionary]) { $Summary['containerAppFqdn'] } else { $Summary.containerAppFqdn }
    }

    $ids | ConvertTo-Json -Depth 10 | Set-Content -Path $idsLocalPath -Encoding utf8
    Write-Host "Wrote resource IDs to $idsLocalPath" -ForegroundColor Green
    return $idsLocalPath
}

function Set-SearchAdminKeySecret {
    <#
    Store the Search admin key in Key Vault. Returns 'stored', 'kv-unreachable' or
    'no-key'. 'kv-unreachable' is a warning, not an error: governed subscriptions
    can force Key Vault publicNetworkAccess=Disabled, and the scripts fall back to
    'az search admin-key show'. 'no-key' means the Search service itself could not
    be read and is a real failure.
    #>
    param(
        [Parameter(Mandatory)][string]$ResourceGroup,
        [Parameter(Mandatory)][string]$SearchService,
        [Parameter(Mandatory)][string]$KeyVault
    )
    $adminKey = az search admin-key show --resource-group $ResourceGroup --service-name $SearchService --query primaryKey -o tsv
    if ($LASTEXITCODE -ne 0 -or -not $adminKey) {
        Write-Host "Could not read the Search admin key for '$SearchService'. The Key Vault secret was NOT set." -ForegroundColor Red
        return 'no-key'
    }
    az keyvault secret set --vault-name $KeyVault --name 'search-admin-key' --value $adminKey --output none 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "WARNING: could not write 'search-admin-key' to Key Vault '$KeyVault'." -ForegroundColor Yellow
        Write-Host "  Expected where policy forces Key Vault publicNetworkAccess=Disabled; the scripts fall back to 'az search admin-key show'." -ForegroundColor Yellow
        return 'kv-unreachable'
    }
    Write-Host "Stored Search admin key in Key Vault '$KeyVault' as secret 'search-admin-key'" -ForegroundColor Green
    return 'stored'
}
