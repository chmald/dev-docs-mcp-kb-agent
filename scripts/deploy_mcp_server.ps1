<#
.SYNOPSIS
    Builds and deploys the fallback MCP server (mcp_fallback_server.py) to the
    Azure Container App provisioned by infra/modules/containerapp.bicep.
.DESCRIPTION
    Runs `az acr build` to build the container image from this scripts/
    folder, then updates the Container App to point at the freshly-built
    image tag. Requires infra/deploy.ps1 -DeployFallbackServer to have been
    run first (so the Container Apps environment + registry + app exist).
.PARAMETER IdsFile
    Path to demo-ids.local.json.
.EXAMPLE
    ./deploy_mcp_server.ps1 -IdsFile ../demo-ids.local.json
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$IdsFile
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

$ids = Get-Content $IdsFile -Raw | ConvertFrom-Json
$fallback = $ids._fallback_mcp_server_fields_populated_only_if_deployed
if (-not $fallback -or -not $fallback.containerRegistry) {
    Write-Error "demo-ids.local.json has no fallback server fields. Run: infra/deploy.ps1 -DeployFallbackServer first."
    exit 1
}

# A minimal Dockerfile is required alongside this script for az acr build to
# work -- see scripts/Dockerfile.
$registry = $fallback.containerRegistry
$registryName = ($registry -split '\.')[0]
$imageTag = "mcp-kb-wrapper:$(Get-Date -Format 'yyyyMMddHHmmss')"

Write-Host "Building image $imageTag in $registryName via az acr build..." -ForegroundColor Cyan
az acr build --registry $registryName --image $imageTag $scriptDir

# Corpus identity drives the MCP tool's advertised name/description -- GitHub
# Copilot uses those to decide whether the tool is relevant to a question, so
# they must reflect THIS corpus, not a generic default.
$corpus = $ids.corpus
$toolName = if ($corpus -and $corpus.mcpToolName) { $corpus.mcpToolName } else { "retrieve_documents" }
$toolDescription = if ($corpus -and $corpus.mcpToolDescription) {
    $corpus.mcpToolDescription
} else {
    "Search the indexed document corpus and return grounded, cited passages for the given question."
}

$containerAppName = ($fallback.containerAppFqdn -split '\.')[0]  # Container App FQDNs are prefixed with the app name
Write-Host "Updating Container App '$containerAppName' to image $registry/$imageTag..." -ForegroundColor Cyan
az containerapp update `
    --name $containerAppName `
    --resource-group $ids.resourceGroup `
    --image "$registry/$imageTag" `
    --set-env-vars `
        "KNOWLEDGE_BASE_NAME=$($ids.knowledgeBaseName)" `
        "SEARCH_INDEX_NAME=$($ids.searchIndexName)" `
        "MCP_TOOL_NAME=$toolName" `
        "MCP_TOOL_DESCRIPTION=$toolDescription"

$fqdn = az containerapp show --name $containerAppName --resource-group $ids.resourceGroup --query "properties.configuration.ingress.fqdn" -o tsv
Write-Host "`nFallback MCP server deployed: https://$fqdn/mcp" -ForegroundColor Green
Write-Host "Add this to .vscode/mcp.json -- see docs/07-github-copilot-mcp-client-setup.md" -ForegroundColor Cyan
