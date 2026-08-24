# 07 — GitHub Copilot MCP client setup

> Deep dive on wiring VS Code / GitHub Copilot to the MCP endpoint from [docs/06](06-mcp-endpoint-and-fallback-server.md). Referenced from [03-deployment.md Phase 5](03-deployment.md#phase-5--wire-github-copilot--vs-code) and [docs/00-reproduce-this-demo.md Part E](00-reproduce-this-demo.md).

---

## 1 — Prerequisites checklist

### 1. GitHub Copilot with agent mode

VS Code with the GitHub Copilot + GitHub Copilot Chat extensions installed, signed in, and **agent mode** enabled (Copilot Chat settings → agent mode / MCP support — this ships as a standard capability of recent Copilot Chat versions; confirm your extension version supports MCP server configuration if the option isn't visible).

### 2. A working MCP endpoint

Either the native Knowledge Base MCP endpoint (validated in [docs/06 § 3](06-mcp-endpoint-and-fallback-server.md#3--checking-availability)) or the optional custom wrapper Container App (deployed in [docs/06 § 5](06-mcp-endpoint-and-fallback-server.md#5--deploying-the-custom-wrapper-server-to-azure-container-apps)).

---

## 2 — Workspace `.vscode/mcp.json`

VS Code discovers MCP servers from a workspace-level `.vscode/mcp.json` (shared with the team when committed to the project repo) or a user-level MCP config (personal, not shared). For a customer-deployable demo, **commit the workspace file** so every developer on the project gets the connection automatically when they open the repo.

> **Portability note.** When VS Code's Agent Host mode is enabled, `.vscode/mcp.json` entries that use `${input:...}` prompted secrets aren't forwarded to the Agent Host the same way as static config. If your team relies on Agent Host and hits connection issues with the `inputs`-based options below, use a portable `.mcp.json` at the repo root (not under `.vscode/`) or a user-level `~/.copilot/mcp-config.json` instead — the server entry shape is the same either way.

### Option A — native AI Search MCP endpoint, API key auth (simplest)

```jsonc
// .vscode/mcp.json
{
  "servers": {
    "dev-docs-knowledge-agent": {
      "type": "http",
      "url": "https://${input:searchServiceName}.search.windows.net/knowledgebases/kb-hybrid/mcp?api-version=2026-05-01-preview",
      "headers": {
        "api-key": "${input:searchApiKey}"
      }
    }
  },
  "inputs": [
    {
      "id": "searchServiceName",
      "type": "promptString",
      "description": "AI Search service name (e.g. srch-ddmcp-dev-eastus2)"
    },
    {
      "id": "searchApiKey",
      "type": "promptString",
      "description": "AI Search query key",
      "password": true
    }
  ]
}
```

`inputs` prompts each developer for their own key on first use (stored securely by VS Code, never written to this file) — this is why the API key is **never hardcoded** in the committed `mcp.json`.

### Option A2 — native AI Search MCP endpoint, Entra ID bearer token (Microsoft's recommended production auth)

```jsonc
// .vscode/mcp.json
{
  "servers": {
    "dev-docs-knowledge-agent": {
      "type": "http",
      "url": "https://${input:searchServiceName}.search.windows.net/knowledgebases/kb-hybrid/mcp?api-version=2026-05-01-preview",
      "headers": {
        "Authorization": "Bearer ${input:searchBearerToken}"
      }
    }
  },
  "inputs": [
    {
      "id": "searchServiceName",
      "type": "promptString",
      "description": "AI Search service name (e.g. srch-ddmcp-dev-eastus2)"
    },
    {
      "id": "searchBearerToken",
      "type": "promptString",
      "description": "Entra ID token scoped to https://search.azure.com/.default (e.g. `az account get-access-token --resource https://search.azure.com --query accessToken -o tsv`) — the caller needs Search Index Data Reader on the service",
      "password": true
    }
  ]
}
```

Entra ID bearer tokens expire (roughly an hour), so re-prompting is more frequent than Option A's API key — acceptable for a demo, but flag to the customer that a service-principal / `DefaultAzureCredential`-based client (or the custom wrapper server, which can refresh tokens server-side) is the more durable choice for daily developer use.

### Option B — optional custom wrapper Container App

```jsonc
// .vscode/mcp.json
{
  "servers": {
    "dev-docs-knowledge-agent": {
      "type": "http",
      "url": "https://${input:mcpServerFqdn}/mcp"
    }
  },
  "inputs": [
    {
      "id": "mcpServerFqdn",
      "type": "promptString",
      "description": "Custom wrapper MCP server Container App FQDN (from demo-ids.local.json: containerAppFqdn)"
    }
  ]
}
```

The wrapper server's own auth to AI Search is handled server-side (Key Vault reference — see [docs/06 § 5](06-mcp-endpoint-and-fallback-server.md#5--deploying-the-custom-wrapper-server-to-azure-container-apps)), so no key is needed in the VS Code client config for this option.

### Local stdio option (single-developer testing only — not for team distribution)

```jsonc
// .vscode/mcp.json
{
  "servers": {
    "dev-docs-knowledge-agent": {
      "type": "stdio",
      "command": "python",
      "args": ["${workspaceFolder}/scripts/mcp_fallback_server.py", "--transport", "stdio"],
      "env": {
        "SEARCH_ENDPOINT": "${input:searchEndpoint}",
        "SEARCH_API_KEY": "${input:searchApiKey}"
      }
    }
  }
}
```

Use this only for a developer running the wrapper server locally against their own dev Search instance — not suitable for a shared team demo (each developer would need Python + dependencies installed locally).

---

## 3 — Verifying the connection

1. Reload the VS Code window (`Developer: Reload Window`) after adding/editing `.vscode/mcp.json`
2. Open the Copilot Chat panel, switch to **agent mode**
3. Open the MCP servers list (Command Palette → `MCP: List Servers`, or the tools icon in the Copilot Chat input) — confirm `dev-docs-knowledge-agent` shows as **connected**
4. If it fails to connect, open `MCP: Show Output` for the connection log (auth errors, malformed URL, and transport mismatches show up here first)

## 4 — Using it

Ask a natural-language question about the corpus directly in Copilot Chat (agent mode) — no special syntax required:

> "According to the datasheet, what's the maximum SPI clock frequency this part supports, and does our current firmware config exceed it?"

Copilot recognizes the connected MCP tool as relevant to the question, calls it, and incorporates the grounded, cited result into its response — while still having full access to your open workspace files for the code-side half of the comparison.

## 5 — Team distribution

- Commit `.vscode/mcp.json` to the customer's repository so every developer gets the connection when they clone/open the project — this is the "customer-ready, easily deployable" part of the ask: one file, no separate portal step per developer
- Use the `inputs` prompt pattern (Option A or A2) so no team member needs to know or share a hardcoded secret; VS Code's secret storage keeps each developer's entered credential local to their machine
- If the corpus or endpoint changes (e.g., switching from the custom wrapper to the native endpoint once it's confirmed available), update the committed `.vscode/mcp.json` once — every developer picks up the change on next pull

## Troubleshooting

| Symptom | Root cause | Fix |
|---|---|---|
| Server never shows as connected | Malformed JSON (trailing comma, wrong `type` value) | Validate `.vscode/mcp.json`; reload window after any edit |
| Connected, but Copilot never calls the tool | Tool description too generic for Copilot to judge relevance | Set `corpus.mcpToolDescription` in `demo-ids.local.json` to name the actual document types and question types (no code edit needed), then redeploy the wrapper — or update the Knowledge Base's configured description for the native endpoint |
| 401/403 on tool call | API key or bearer token input wasn't entered, or a rotated key/expired token | Re-trigger the `inputs` prompt (Command Palette → `MCP: Reset Server Auth` or remove and re-add the server); for Option A2, tokens expire roughly hourly — re-prompt is expected |
| Works for one developer, not another | Each developer needs their own credential entered once — if this fails, they're either on the wrong endpoint or lack Search access (`Search Index Data Reader` for bearer-token auth) | Confirm they're using the same `searchServiceName`/`mcpServerFqdn` and have valid credentials |

---

*Last updated: 2026-08-18*
