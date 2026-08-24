"""
post_deploy_search.py

Creates and manages the AI Search data source, skillset, index, indexer, and
Knowledge Base for the Developer Docs MCP Knowledge Base pattern, and checks
whether the native Knowledge Base -> MCP endpoint is available on this Search
service.

The Knowledge Base MCP endpoint (`/knowledgebases/{name}/mcp`) is a real,
documented Azure AI Search capability (agentic retrieval was renamed from
"Knowledge Agents" to "Knowledge Bases" in 2026) -- it is not a fabricated or
purely speculative feature. That said, the exact API version
(SEARCH_API_VERSION below), tool schema, and regional rollout still move
quickly, and the Document Intelligence Layout skill's exact @odata.type /
output field names are a comparatively recent addition. Re-verify the exact
REST paths, payload shapes, and skill names against the current Azure AI
Search REST API reference before a customer-facing build -- see
docs/06-mcp-endpoint-and-fallback-server.md.

Usage:
    python post_deploy_search.py --ids-file ../demo-ids.local.json --create-index --create-skillset --create-indexer --run-indexer
    python post_deploy_search.py --ids-file ../demo-ids.local.json --indexer-status
    python post_deploy_search.py --ids-file ../demo-ids.local.json --create-knowledge-base
    python post_deploy_search.py --ids-file ../demo-ids.local.json --check-mcp-endpoint
    python post_deploy_search.py --ids-file ../demo-ids.local.json --test-retrieve --query "your question"
"""
import argparse
import json
import shutil
import subprocess
import sys

import requests

SEARCH_API_VERSION = "2026-05-01-preview"  # verify at deployment time -- see docs/06

# --- Generic, corpus-neutral defaults -----------------------------------
# Every name below is overridable via demo-ids.local.json so the same pipeline
# serves any document corpus (HR policies, contracts, research papers, API
# specs, hardware datasheets, ...). Nothing here is domain-specific.
DEFAULT_INDEX_NAME = "idx-documents"
DEFAULT_SKILLSET_NAME = "skillset-documents"
DEFAULT_DATASOURCE_NAME = "ds-documents-blob"
DEFAULT_INDEXER_NAME = "ixr-documents"
DEFAULT_KNOWLEDGE_BASE_NAME = "kb-documents"
DEFAULT_KNOWLEDGE_SOURCE_NAME = "ks-documents"

# These four are scoped WITHIN an index definition, so they never collide
# across indexes and never need renaming per corpus. Keeping them as fixed
# generic constants removes a whole class of "renamed the index but forgot the
# semantic config" bugs. mcp_fallback_server.py uses the same SEMANTIC_CONFIG_NAME.
VECTOR_PROFILE_NAME = "vector-profile"
VECTOR_ALGORITHM_NAME = "hnsw-algorithm"
VECTORIZER_NAME = "vectorizer-embedding"
SEMANTIC_CONFIG_NAME = "semantic-config"

# Chunking defaults -- tune per corpus in demo-ids.local.json (`corpus` block).
# Dense reference material with tables benefits from smaller chunks + more
# overlap; long-form prose tolerates larger chunks.
DEFAULT_CHUNK_SIZE_TOKENS = 1500
DEFAULT_CHUNK_OVERLAP_TOKENS = 200

# text-embedding-3-large = 3072. Override in demo-ids.local.json if you deploy
# a different embedding model -- a dimension mismatch fails at index creation.
DEFAULT_EMBEDDING_DIMENSIONS = 3072


def _corpus(ids: dict) -> dict:
    """The optional `corpus` block in demo-ids.local.json carries the only
    genuinely corpus-specific settings (display name, MCP tool identity,
    chunking, source file types). Everything else in this pipeline is generic."""
    return ids.get("corpus") or {}


def load_ids(ids_file: str) -> dict:
    with open(ids_file, "r", encoding="utf-8") as f:
        return json.load(f)


def save_ids(ids_file: str, ids: dict) -> None:
    with open(ids_file, "w", encoding="utf-8") as f:
        json.dump(ids, f, indent=2)


def _az_executable() -> str:
    """Resolve the Azure CLI entry point.

    On Windows the CLI ships as `az.cmd`, which `subprocess.run` cannot locate
    from a bare "az" argument without a shell. `shutil.which` honours PATHEXT
    and returns the real path on every platform."""
    az = shutil.which("az")
    if not az:
        raise RuntimeError(
            "Azure CLI ('az') was not found on PATH. Install it from "
            "https://aka.ms/installazurecli and re-run."
        )
    return az


def get_admin_key(ids: dict) -> str:
    """Fetch the Search admin key via the az CLI (avoids adding an
    azure-keyvault-secrets dependency for a single lookup).

    Primary source is the Key Vault secret written by infra/deploy.ps1. Some
    governed subscriptions apply a policy that forces Key Vault
    `publicNetworkAccess: Disabled`, which makes that lookup fail from a
    developer workstation even with correct RBAC. In that case fall back to
    reading the key straight off the Search service via the control plane,
    which needs no data-plane access to the vault."""
    kv_reason = "no 'keyVault' configured in the ids file"
    if ids.get("keyVault"):
        kv_result = subprocess.run(
            [
                _az_executable(), "keyvault", "secret", "show",
                "--vault-name", ids["keyVault"],
                "--name", ids.get("searchAdminKeySecretName", "search-admin-key"),
                "--query", "value", "-o", "tsv",
            ],
            capture_output=True, text=True,
        )
        if kv_result.returncode == 0 and kv_result.stdout.strip():
            return kv_result.stdout.strip()
        stderr = kv_result.stderr.strip()
        kv_reason = stderr.splitlines()[0] if stderr else "unknown"

    print(
        "  ! Key Vault lookup unavailable -- falling back to 'az search admin-key show'.\n"
        f"    (Key Vault reason: {kv_reason})"
    )
    fallback = subprocess.run(
        [
            _az_executable(), "search", "admin-key", "show",
            "--resource-group", ids["resourceGroup"],
            "--service-name", ids["searchService"],
            "--query", "primaryKey", "-o", "tsv",
        ],
        capture_output=True, text=True, check=True,
    )
    return fallback.stdout.strip()


def _storage_resource_id(ids: dict) -> str:
    return (
        f"/subscriptions/{ids['subscriptionId']}/resourceGroups/{ids['resourceGroup']}"
        f"/providers/Microsoft.Storage/storageAccounts/{ids['storageAccount']}"
    )


def _ai_services_subdomain_url(ids: dict) -> str:
    """Subdomain URL for the billable AI Services (Foundry) attachment on the
    skillset.

    IMPORTANT: for a `kind: AIServices` (Foundry) account, the Search API only
    accepts the **AI Foundry** subdomain form
    `https://<name>.services.ai.azure.com`. Passing the Document Intelligence /
    FormRecognizer endpoint (`https://<name>.cognitiveservices.azure.com`) --
    even though it is the same multi-service account, and is what the Bicep
    `documentIntelligenceEndpoint` output and the Azure portal both show -- is
    rejected with "'SubdomainUrl' parameter is not well-formed".

    A trailing slash is also rejected, and both the Bicep output and the portal
    render the endpoint *with* one, so normalise it here.

    Resolution order: explicit `aiServicesSubdomainUrl` -> derived from
    `foundryResource` -> `documentIntelligenceEndpoint` (last-resort legacy)."""
    url = ids.get("aiServicesSubdomainUrl")
    if not url and ids.get("foundryResource"):
        url = f"https://{ids['foundryResource']}.services.ai.azure.com"
    if not url:
        url = ids["documentIntelligenceEndpoint"]
    return url.rstrip("/")


def search_request(method: str, path: str, ids: dict, admin_key: str, body: dict | None = None) -> requests.Response:
    url = f"{ids['searchEndpoint']}{path}?api-version={SEARCH_API_VERSION}"
    headers = {"api-key": admin_key, "Content-Type": "application/json"}
    return requests.request(method, url, headers=headers, json=body, timeout=60)


def raise_with_detail(resp: requests.Response) -> None:
    """`raise_for_status()` hides the Search API's response body, which is where
    the actual reason for a 400 lives (bad field definition, unknown skill
    property, wrong API version). Surface it before raising."""
    if resp.status_code >= 400:
        detail = resp.text.strip()
        print(f"  ! {resp.status_code} {resp.reason} from {resp.request.method} {resp.url}")
        if detail:
            print(f"    {detail[:2000]}")
    resp.raise_for_status()


def create_data_source(ids: dict, admin_key: str) -> None:
    name = ids.get("searchDataSourceName", DEFAULT_DATASOURCE_NAME)
    body = {
        "name": name,
        "type": "azureblob",
        # Managed-identity connection. Trailing "/;" matches the documented
        # canonical form. No "identity" property needed for system-assigned MI.
        "credentials": {"connectionString": f"ResourceId={_storage_resource_id(ids)}/;"},
        "container": {"name": ids.get("rawContainer", "raw")},
    }
    resp = search_request("PUT", f"/datasources('{name}')", ids, admin_key, body)
    raise_with_detail(resp)
    print(f"Data source '{name}': {resp.status_code}")


def create_index(ids: dict, admin_key: str) -> None:
    """Corpus-neutral chunk index. The field set describes *where a passage came
    from* (source document, URI, heading path), not what it is about, so the
    same schema serves any document corpus. Add domain fields (partNumber,
    policyId, contractDate, ...) only when you actually need to filter or
    facet on them.

    Field-attribute requirements are not cosmetic:
      - the key needs `analyzer: keyword` + searchable, or the default Lucene
        analyzer tokenizes chunk IDs and breaks key lookups
      - `parent_id` must exist, be Edm.String and filterable -- index
        projections write the parent document key into it
      - every field named in the semantic configuration must be searchable and
        retrievable, or index creation is rejected with HTTP 400
    """
    name = ids.get("searchIndexName", DEFAULT_INDEX_NAME)
    dimensions = ids.get("embeddingDimensions", DEFAULT_EMBEDDING_DIMENSIONS)
    body = {
        "name": name,
        "fields": [
            {
                "name": "chunkId", "type": "Edm.String", "key": True,
                "analyzer": "keyword", "searchable": True, "filterable": True,
                "sortable": True, "retrievable": True,
            },
            # Required by indexProjections.parentKeyFieldName -- links each
            # chunk back to its source document.
            {"name": "parent_id", "type": "Edm.String", "filterable": True, "retrievable": True},
            {"name": "content", "type": "Edm.String", "searchable": True, "retrievable": True},
            {
                "name": "contentVector", "type": "Collection(Edm.Single)",
                "searchable": True, "retrievable": False, "dimensions": dimensions,
                "vectorSearchProfile": VECTOR_PROFILE_NAME,
            },
            # searchable because the semantic configuration names it as titleField.
            {
                "name": "sourceDocument", "type": "Edm.String", "searchable": True,
                "filterable": True, "facetable": True, "retrievable": True,
            },
            {"name": "sourceUri", "type": "Edm.String", "retrievable": True},
            # Heading path from the Document Layout skill's markdown `sections`
            # object (h1/h2/h3). This is what the skill actually emits in
            # markdown mode -- see create_skillset for the page-number caveat.
            {"name": "sectionH1", "type": "Edm.String", "searchable": True, "filterable": True, "retrievable": True},
            {"name": "sectionH2", "type": "Edm.String", "searchable": True, "filterable": True, "retrievable": True},
            {"name": "sectionH3", "type": "Edm.String", "searchable": True, "filterable": True, "retrievable": True},
        ],
        "vectorSearch": {
            "profiles": [
                {"name": VECTOR_PROFILE_NAME, "algorithm": VECTOR_ALGORITHM_NAME, "vectorizer": VECTORIZER_NAME}
            ],
            "algorithms": [{"name": VECTOR_ALGORITHM_NAME, "kind": "hnsw"}],
            "vectorizers": [
                {
                    "name": VECTORIZER_NAME,
                    "kind": "azureOpenAI",
                    # NOTE: on the wire this wrapper is `azureOpenAIParameters`.
                    # The Azure SDKs expose it as `parameters` -- don't cross-wire
                    # them, and don't copy the embedding SKILL's flat shape here.
                    "azureOpenAIParameters": {
                        "resourceUri": ids["foundryOpenAIEndpoint"],
                        "deploymentId": ids.get("embeddingDeployment", "embedding"),
                        "modelName": ids.get("embeddingModel", "text-embedding-3-large"),
                    },
                }
            ],
        },
        "semantic": {
            "configurations": [
                {
                    "name": SEMANTIC_CONFIG_NAME,
                    "prioritizedFields": {
                        "titleField": {"fieldName": "sourceDocument"},
                        # Property is `prioritizedContentFields`, NOT `contentFields`.
                        "prioritizedContentFields": [{"fieldName": "content"}],
                        "prioritizedKeywordsFields": [{"fieldName": "sectionH1"}],
                    },
                }
            ]
        },
    }
    resp = search_request("PUT", f"/indexes('{name}')", ids, admin_key, body)
    raise_with_detail(resp)
    print(f"Index '{name}': {resp.status_code}")


def create_skillset(ids: dict, admin_key: str) -> None:
    """Corpus-neutral ingestion pipeline: Document Intelligence Layout ->
    Split -> Azure OpenAI embedding -> index projections. The same three-skill
    chain works for any document type the Layout skill reads (PDF, images,
    Office formats) -- only the chunk sizing in the `corpus` block is worth
    tuning per corpus.

    PAGE NUMBERS: the Layout skill in `markdown` mode emits, per section,
    `content` + `sections` (h1..h6) + `ordinal_position` -- there is NO page
    number. To cite page numbers instead of heading paths, switch
    `outputFormat` to "text" with `extractionOptions: ["locationMetadata"]`,
    consume the `text_sections` output, and project
    `/document/<target>/*/locationMetadata/...`. This pattern uses markdown
    mode because heading paths survive re-pagination and are more stable
    citations for most corpora.
    """
    name = ids.get("searchSkillsetName", DEFAULT_SKILLSET_NAME)
    index_name = ids.get("searchIndexName", DEFAULT_INDEX_NAME)
    corpus = _corpus(ids)
    corpus_label = corpus.get("displayName", "document")
    chunk_size = corpus.get("chunkSizeTokens", DEFAULT_CHUNK_SIZE_TOKENS)
    chunk_overlap = corpus.get("chunkOverlapTokens", DEFAULT_CHUNK_OVERLAP_TOKENS)
    dimensions = ids.get("embeddingDimensions", DEFAULT_EMBEDDING_DIMENSIONS)
    body = {
        "name": name,
        "description": (
            f"Extracts structured layout from the {corpus_label} corpus, chunks it "
            "heading-aware, and embeds each chunk."
        ),
        "skills": [
            {
                "@odata.type": "#Microsoft.Skills.Util.DocumentIntelligenceLayoutSkill",
                "context": "/document",
                "outputMode": "oneToMany",
                "outputFormat": "markdown",
                "markdownHeaderDepth": "h3",
                "inputs": [{"name": "file_data", "source": "/document/file_data"}],
                "outputs": [{"name": "markdown_document", "targetName": "markdownDocument"}],
            },
            {
                "@odata.type": "#Microsoft.Skills.Text.SplitSkill",
                "context": "/document/markdownDocument/*",
                "textSplitMode": "pages",
                "maximumPageLength": chunk_size,
                "pageOverlapLength": chunk_overlap,
                "unit": "azureOpenAITokens",
                # cl100k_base is the tokenizer for the text-embedding-3-* family.
                # (o200k_base is NOT supported by this skill.)
                "azureOpenAITokenizerParameters": {"encoderModelName": "cl100k_base"},
                "inputs": [{"name": "text", "source": "/document/markdownDocument/*/content"}],
                "outputs": [{"name": "textItems", "targetName": "chunks"}],
            },
            {
                "@odata.type": "#Microsoft.Skills.Text.AzureOpenAIEmbeddingSkill",
                "context": "/document/markdownDocument/*/chunks/*",
                # NOTE: flat property placement is correct for the SKILL. The
                # index VECTORIZER nests the same values under
                # `azureOpenAIParameters` -- the two shapes differ by design.
                "resourceUri": ids["foundryOpenAIEndpoint"],
                "deploymentId": ids.get("embeddingDeployment", "embedding"),
                "modelName": ids.get("embeddingModel", "text-embedding-3-large"),
                # Must match the index's contentVector `dimensions`.
                "dimensions": dimensions,
                "inputs": [{"name": "text", "source": "/document/markdownDocument/*/chunks/*"}],
                "outputs": [{"name": "embedding", "targetName": "contentVector"}],
            },
        ],
        # The Layout skill is billable. Without this attachment the skillset
        # silently stops after 20 enrichments per indexer run per day and the
        # execution history reports a "Time Out" message.
        "cognitiveServices": {
            "@odata.type": "#Microsoft.Azure.Search.AIServicesByIdentity",
            "subdomainUrl": _ai_services_subdomain_url(ids),
            # null == the search service's system-assigned managed identity,
            # which infra/modules/rbac.bicep grants Cognitive Services User.
            "identity": None,
        },
        "indexProjections": {
            "selectors": [
                {
                    "targetIndexName": index_name,
                    "parentKeyFieldName": "parent_id",
                    "sourceContext": "/document/markdownDocument/*/chunks/*",
                    "mappings": [
                        {"name": "content", "source": "/document/markdownDocument/*/chunks/*"},
                        {"name": "contentVector", "source": "/document/markdownDocument/*/chunks/*/contentVector"},
                        {"name": "sourceDocument", "source": "/document/metadata_storage_name"},
                        {"name": "sourceUri", "source": "/document/metadata_storage_path"},
                        # Heading path -- these are the paths the Layout skill
                        # actually emits in markdown mode.
                        {"name": "sectionH1", "source": "/document/markdownDocument/*/sections/h1"},
                        {"name": "sectionH2", "source": "/document/markdownDocument/*/sections/h2"},
                        {"name": "sectionH3", "source": "/document/markdownDocument/*/sections/h3"},
                    ],
                }
            ],
            "parameters": {"projectionMode": "skipIndexingParentDocuments"},
        },
    }
    resp = search_request("PUT", f"/skillsets('{name}')", ids, admin_key, body)
    raise_with_detail(resp)
    print(f"Skillset '{name}': {resp.status_code}")


def create_indexer(ids: dict, admin_key: str) -> None:
    """`allowSkillsetToReadFileData: true` is REQUIRED -- it is what creates the
    `/document/file_data` path the Document Layout skill reads. Without it the
    indexer fails on the first document."""
    name = ids.get("searchIndexerName", DEFAULT_INDEXER_NAME)
    body = {
        "name": name,
        "dataSourceName": ids.get("searchDataSourceName", DEFAULT_DATASOURCE_NAME),
        "targetIndexName": ids.get("searchIndexName", DEFAULT_INDEX_NAME),
        "skillsetName": ids.get("searchSkillsetName", DEFAULT_SKILLSET_NAME),
        "parameters": {
            "batchSize": 1,
            "configuration": {
                "dataToExtract": "contentAndMetadata",
                "parsingMode": "default",
                "allowSkillsetToReadFileData": True,
            },
        },
        # Chunks reach the index via indexProjections, not output field mappings.
        "outputFieldMappings": [],
    }
    resp = search_request("PUT", f"/indexers('{name}')", ids, admin_key, body)
    raise_with_detail(resp)
    print(f"Indexer '{name}': {resp.status_code}")


def run_indexer(ids: dict, admin_key: str) -> None:
    name = ids.get("searchIndexerName", DEFAULT_INDEXER_NAME)
    resp = search_request("POST", f"/indexers('{name}')/search.run", ids, admin_key)
    raise_with_detail(resp)
    print(f"Indexer '{name}' run triggered.")


def get_indexer_status(ids: dict, admin_key: str) -> None:
    name = ids.get("searchIndexerName", DEFAULT_INDEXER_NAME)
    resp = search_request("GET", f"/indexers('{name}')/search.status", ids, admin_key)
    raise_with_detail(resp)
    status = resp.json()
    last = status.get("lastResult", {}) or {}
    print(
        f"Indexer status: {last.get('status')}, "
        f"itemsProcessed={last.get('itemsProcessed')}, itemsFailed={last.get('itemsFailed')}"
    )
    for error in last.get("errors", []):
        print(f"  ERROR: {error}")
    for warning in last.get("warnings", []):
        print(f"  WARNING: {warning}")


def create_knowledge_source(ids: dict, admin_key: str) -> None:
    """Agentic retrieval uses a TWO-OBJECT model: a knowledge source wraps the
    index, and the knowledge base references the source by name. This must be
    created BEFORE create_knowledge_base().

    `sourceDataFields` decides which fields come back in
    `references[].sourceData` at retrieve time -- fields not listed here are
    simply absent, so the MCP layer can't cite them."""
    name = ids.get("knowledgeSourceName", DEFAULT_KNOWLEDGE_SOURCE_NAME)
    body = {
        "name": name,
        "description": "Search-index-backed knowledge source for agentic retrieval.",
        "kind": "searchIndex",
        "searchIndexParameters": {
            "searchIndexName": ids.get("searchIndexName", DEFAULT_INDEX_NAME),
            "semanticConfigurationName": SEMANTIC_CONFIG_NAME,
            "sourceDataFields": [
                {"name": "chunkId"},
                {"name": "content"},
                {"name": "sourceDocument"},
                {"name": "sourceUri"},
                {"name": "sectionH1"},
                {"name": "sectionH2"},
                {"name": "sectionH3"},
            ],
        },
    }
    resp = search_request("PUT", f"/knowledgesources/{name}", ids, admin_key, body)
    raise_with_detail(resp)
    print(f"Knowledge Source '{name}': {resp.status_code}")


def create_knowledge_base(ids: dict, admin_key: str) -> None:
    """Requires the knowledge source from create_knowledge_source() to exist.

    NOTE: there is no `targetIndexes` property and no `defaultRerankerThreshold`
    on a knowledge base -- the index is reached through the knowledge source,
    and reranker threshold is a per-query option on the retrieve call.
    `outputMode`/`retrievalReasoningEffort` require 2026-05-01-preview.

    OUTPUT MODE -- this matters more than it looks. The native MCP tool
    (`knowledge_base_retrieve`) accepts ONLY a `queries` array; it cannot pass
    `includeReferenceSourceData`, so retrieval options must be set here on the
    knowledge base. With `answerSynthesis`, the MCP path returns a single
    gpt-5-mini-synthesised paragraph and, in practice, one that claims it
    "cannot access external documents" -- the grounded passages never reach the
    client. `extractiveData` returns the actual ranked passages with
    `ref_id` / source document / heading path, which is what an MCP client
    (GitHub Copilot) needs: Copilot does its own synthesis and citation, so
    synthesising first is both lossy and redundant.

    Override with `knowledgeBaseOutputMode` in demo-ids.local.json if you are
    consuming the knowledge base from a non-LLM client that genuinely wants a
    prose answer."""
    name = ids.get("knowledgeBaseName", DEFAULT_KNOWLEDGE_BASE_NAME)
    source_name = ids.get("knowledgeSourceName", DEFAULT_KNOWLEDGE_SOURCE_NAME)
    body = {
        "name": name,
        "description": "Agentic retrieval over the indexed document corpus.",
        "knowledgeSources": [{"name": source_name}],
        "models": [
            {
                "kind": "azureOpenAI",
                "azureOpenAIParameters": {
                    "resourceUri": ids["foundryOpenAIEndpoint"],
                    "deploymentId": ids.get("chatDeployment", "chat"),
                    "modelName": ids.get("chatModel", "gpt-5-mini"),
                },
            }
        ],
        "outputMode": ids.get("knowledgeBaseOutputMode", "extractiveData"),
    }
    resp = search_request("PUT", f"/knowledgebases/{name}", ids, admin_key, body)
    raise_with_detail(resp)
    print(f"Knowledge Base '{name}': {resp.status_code}")


def retrieve_body(ids: dict, query: str, reranker_threshold: float = 2.5) -> dict:
    """Retrieve request payload. `knowledgeSourceParams` is not optional in
    practice: without `includeReferenceSourceData: true` the service returns
    `references[].sourceData: null`, and every citation downstream is empty."""
    return {
        "messages": [{"role": "user", "content": [{"type": "text", "text": query}]}],
        "knowledgeSourceParams": [
            {
                "kind": "searchIndex",
                "knowledgeSourceName": ids.get("knowledgeSourceName", DEFAULT_KNOWLEDGE_SOURCE_NAME),
                "includeReferences": True,
                "includeReferenceSourceData": True,
                "rerankerThreshold": reranker_threshold,
            }
        ],
    }


def test_retrieve(ids: dict, admin_key: str, query: str) -> None:
    name = ids.get("knowledgeBaseName", DEFAULT_KNOWLEDGE_BASE_NAME)
    resp = search_request("POST", f"/knowledgebases/{name}/retrieve", ids, admin_key, retrieve_body(ids, query))
    raise_with_detail(resp)
    print(json.dumps(resp.json(), indent=2))


def _parse_mcp_response(resp: requests.Response) -> dict | None:
    """The MCP Streamable HTTP transport replies with Server-Sent Events
    (`Content-Type: text/event-stream`), not a bare JSON body -- each JSON-RPC
    message arrives on a `data:` line. Calling `resp.json()` on that raises
    "Expecting value: line 1 column 1", which previously made this script report
    a working native endpoint as unavailable and push people to deploy the
    fallback Container App for no reason.

    Handles both transports: plain JSON and SSE."""
    ctype = resp.headers.get("Content-Type", "")
    if "text/event-stream" not in ctype:
        try:
            return resp.json()
        except ValueError:
            return None
    for line in resp.text.splitlines():
        if line.startswith("data:"):
            try:
                return json.loads(line[len("data:"):].strip())
            except ValueError:
                continue
    return None


def check_mcp_endpoint(ids: dict, admin_key: str, ids_file: str) -> None:
    """Sends an MCP `initialize` request to the Knowledge Base's native MCP
    endpoint. Records availability into demo-ids.local.json so downstream
    scripts/docs know whether to rely on native MCP or the optional custom
    wrapper server."""
    name = ids.get("knowledgeBaseName", DEFAULT_KNOWLEDGE_BASE_NAME)
    url = f"{ids['searchEndpoint']}/knowledgebases/{name}/mcp?api-version={SEARCH_API_VERSION}"
    body = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "mcp-knowledge-base-setup-script", "version": "1.0"},
        },
    }
    # The Streamable HTTP transport requires the client to advertise that it can
    # accept an SSE stream; omitting text/event-stream gets a 406 from some
    # server implementations.
    headers = {
        "api-key": admin_key,
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    try:
        resp = requests.post(url, headers=headers, json=body, timeout=30)
        payload = _parse_mcp_response(resp)
        if resp.status_code == 200 and payload and "result" in payload:
            print("Native MCP endpoint AVAILABLE.")
            ids["mcpEndpointAvailability"] = "native"
        else:
            print(f"Native MCP endpoint check returned {resp.status_code}: {resp.text[:300]}")
            print("Native endpoint not available -- deploy the optional custom MCP server wrapper (Phase 4 / Part D).")
            ids["mcpEndpointAvailability"] = "wrapper-required"
    except requests.RequestException as exc:
        print(f"Native MCP endpoint check failed: {exc}")
        print("Native endpoint not available -- deploy the optional custom MCP server wrapper (Phase 4 / Part D).")
        ids["mcpEndpointAvailability"] = "wrapper-required"
    save_ids(ids_file, ids)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ids-file", required=True)
    parser.add_argument("--create-index", action="store_true")
    parser.add_argument("--create-skillset", action="store_true")
    parser.add_argument("--create-indexer", action="store_true", help="Also creates the data source")
    parser.add_argument("--run-indexer", action="store_true")
    parser.add_argument("--indexer-status", action="store_true")
    parser.add_argument("--create-knowledge-base", action="store_true",
                        help="Creates the knowledge source, then the knowledge base that references it")
    parser.add_argument("--check-mcp-endpoint", action="store_true")
    parser.add_argument("--test-retrieve", action="store_true")
    parser.add_argument("--query", default=None)
    args = parser.parse_args()

    ids = load_ids(args.ids_file)
    admin_key = get_admin_key(ids)

    ran_something = False

    if args.create_index:
        create_index(ids, admin_key)
        ran_something = True
    if args.create_skillset:
        create_skillset(ids, admin_key)
        ran_something = True
    if args.create_indexer:
        create_data_source(ids, admin_key)
        create_indexer(ids, admin_key)
        ran_something = True
    if args.run_indexer:
        run_indexer(ids, admin_key)
        ran_something = True
    if args.indexer_status:
        get_indexer_status(ids, admin_key)
        ran_something = True
    if args.create_knowledge_base:
        # Two-object model: the source must exist before the base references it.
        create_knowledge_source(ids, admin_key)
        create_knowledge_base(ids, admin_key)
        ran_something = True
    if args.check_mcp_endpoint:
        check_mcp_endpoint(ids, admin_key, args.ids_file)
        ran_something = True
    if args.test_retrieve:
        if not args.query:
            print("--test-retrieve requires --query", file=sys.stderr)
            sys.exit(1)
        test_retrieve(ids, admin_key, args.query)
        ran_something = True

    if not ran_something:
        parser.print_help()


if __name__ == "__main__":
    main()
