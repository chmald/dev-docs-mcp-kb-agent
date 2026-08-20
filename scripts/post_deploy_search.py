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


def get_admin_key(ids: dict) -> str:
    """Fetch the Search admin key from Key Vault via the az CLI (avoids adding
    an azure-keyvault-secrets dependency for a single lookup)."""
    result = subprocess.run(
        [
            "az", "keyvault", "secret", "show",
            "--vault-name", ids["keyVault"],
            "--name", ids.get("searchAdminKeySecretName", "search-admin-key"),
            "--query", "value", "-o", "tsv",
        ],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def _storage_resource_id(ids: dict) -> str:
    return (
        f"/subscriptions/{ids['subscriptionId']}/resourceGroups/{ids['resourceGroup']}"
        f"/providers/Microsoft.Storage/storageAccounts/{ids['storageAccount']}"
    )


def _ai_services_subdomain_url(ids: dict) -> str:
    """Subdomain URL for the billable AI Services (Foundry) attachment on the
    skillset. Prefer an explicit `aiServicesSubdomainUrl` in demo-ids.local.json;
    otherwise fall back to the Document Intelligence endpoint, which is the same
    multi-service account. If your Foundry resource exposes the newer
    `https://<name>.services.ai.azure.com` form, set it explicitly."""
    return ids.get("aiServicesSubdomainUrl") or ids["documentIntelligenceEndpoint"]


def search_request(method: str, path: str, ids: dict, admin_key: str, body: dict | None = None) -> requests.Response:
    url = f"{ids['searchEndpoint']}{path}?api-version={SEARCH_API_VERSION}"
    headers = {"api-key": admin_key, "Content-Type": "application/json"}
    return requests.request(method, url, headers=headers, json=body, timeout=60)


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
    resp.raise_for_status()
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
    resp.raise_for_status()
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
    resp.raise_for_status()
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
    resp.raise_for_status()
    print(f"Indexer '{name}': {resp.status_code}")


def run_indexer(ids: dict, admin_key: str) -> None:
    name = ids.get("searchIndexerName", DEFAULT_INDEXER_NAME)
    resp = search_request("POST", f"/indexers('{name}')/search.run", ids, admin_key)
    resp.raise_for_status()
    print(f"Indexer '{name}' run triggered.")


def get_indexer_status(ids: dict, admin_key: str) -> None:
    name = ids.get("searchIndexerName", DEFAULT_INDEXER_NAME)
    resp = search_request("GET", f"/indexers('{name}')/search.status", ids, admin_key)
    resp.raise_for_status()
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
    resp.raise_for_status()
    print(f"Knowledge Source '{name}': {resp.status_code}")


def create_knowledge_base(ids: dict, admin_key: str) -> None:
    """Requires the knowledge source from create_knowledge_source() to exist.

    NOTE: there is no `targetIndexes` property and no `defaultRerankerThreshold`
    on a knowledge base -- the index is reached through the knowledge source,
    and reranker threshold is a per-query option on the retrieve call.
    `outputMode`/`retrievalReasoningEffort` require 2026-05-01-preview."""
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
        "outputMode": "answerSynthesis",
    }
    resp = search_request("PUT", f"/knowledgebases/{name}", ids, admin_key, body)
    resp.raise_for_status()
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
    resp.raise_for_status()
    print(json.dumps(resp.json(), indent=2))


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
    headers = {"api-key": admin_key, "Content-Type": "application/json"}
    try:
        resp = requests.post(url, headers=headers, json=body, timeout=30)
        if resp.status_code == 200 and "result" in resp.json():
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
