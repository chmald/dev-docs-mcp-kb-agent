"""Smoke tests for scripts/post_deploy_search.py -- confirm the module
imports cleanly and its REST payload builders produce well-formed requests
matching the documented index/skillset schema, and that the pipeline stays
corpus-neutral (see the "Reusability guards" section at the bottom). These are
NOT integration tests against a live Search service."""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import post_deploy_search as pds  # noqa: E402

SAMPLE_IDS = {
    "searchEndpoint": "https://srch-ddmcp-dev-eastus2.search.windows.net",
    "searchIndexName": "idx-documents",
    "searchSkillsetName": "skillset-documents",
    "searchDataSourceName": "ds-documents-blob",
    "searchIndexerName": "ixr-documents",
    "knowledgeBaseName": "kb-documents",
    "knowledgeSourceName": "ks-documents",
    "rawContainer": "raw",
    "foundryOpenAIEndpoint": "https://aif-ddmcp-dev-eastus2.openai.azure.com",
    "embeddingDeployment": "embedding",
    "embeddingModel": "text-embedding-3-large",
    "chatDeployment": "chat",
    "chatModel": "gpt-5-mini",
    "subscriptionId": "00000000-0000-0000-0000-000000000000",
    "resourceGroup": "rg-ddmcp-dev-eastus2",
    "storageAccount": "stddmcpdeveastus2",
    "documentIntelligenceEndpoint": "https://aif-ddmcp-dev-eastus2.cognitiveservices.azure.com/",
}
FAKE_ADMIN_KEY = "fake-admin-key"


def test_module_imports():
    assert pds is not None


def test_search_api_version_is_set():
    assert pds.SEARCH_API_VERSION


def test_storage_resource_id_shape():
    resource_id = pds._storage_resource_id(SAMPLE_IDS)
    assert resource_id.startswith("/subscriptions/")
    assert "storageAccounts/stddmcpdeveastus2" in resource_id


@patch("post_deploy_search.search_request")
def test_create_index_sends_expected_schema(mock_request):
    mock_request.return_value = MagicMock(status_code=201, raise_for_status=lambda: None)

    pds.create_index(SAMPLE_IDS, FAKE_ADMIN_KEY)

    assert mock_request.called
    method, path, _ids, _admin_key, body = mock_request.call_args[0]
    assert method == "PUT"
    assert path == "/indexes('idx-documents')"
    field_names = {f["name"] for f in body["fields"]}
    assert {
        "chunkId", "parent_id", "content", "contentVector", "sourceDocument",
        "sourceUri", "sectionH1",
    } <= field_names
    assert body["vectorSearch"]["vectorizers"][0]["azureOpenAIParameters"]["deploymentId"] == "embedding"


@patch("post_deploy_search.search_request")
def test_create_skillset_wires_three_skills(mock_request):
    mock_request.return_value = MagicMock(status_code=201, raise_for_status=lambda: None)

    pds.create_skillset(SAMPLE_IDS, FAKE_ADMIN_KEY)

    _method, _path, _ids, _admin_key, body = mock_request.call_args[0]
    skill_types = [s["@odata.type"] for s in body["skills"]]
    assert skill_types == [
        "#Microsoft.Skills.Util.DocumentIntelligenceLayoutSkill",
        "#Microsoft.Skills.Text.SplitSkill",
        "#Microsoft.Skills.Text.AzureOpenAIEmbeddingSkill",
    ]
    assert body["indexProjections"]["selectors"][0]["targetIndexName"] == "idx-documents"


@patch("post_deploy_search.search_request")
def test_create_knowledge_base_references_source(mock_request):
    mock_request.return_value = MagicMock(status_code=201, raise_for_status=lambda: None)

    pds.create_knowledge_base(SAMPLE_IDS, FAKE_ADMIN_KEY)

    _method, path, _ids, _admin_key, body = mock_request.call_args[0]
    assert path == "/knowledgebases/kb-documents"
    assert body["knowledgeSources"] == [{"name": "ks-documents"}]
    assert body["models"][0]["azureOpenAIParameters"]["deploymentId"] == "chat"


@patch("post_deploy_search.search_request")
def test_create_data_source_points_at_raw_container(mock_request):
    mock_request.return_value = MagicMock(status_code=201, raise_for_status=lambda: None)

    pds.create_data_source(SAMPLE_IDS, FAKE_ADMIN_KEY)

    _method, path, _ids, _admin_key, body = mock_request.call_args[0]
    assert path == "/datasources('ds-documents-blob')"
    assert body["container"]["name"] == "raw"


# --- Reusability guards --------------------------------------------------
# These tests exist to keep the pipeline corpus-neutral. If someone hardcodes
# a domain term or a fixed dimension/chunk size back into the index or
# skillset builders, these fail.

@patch("post_deploy_search.search_request")
def test_index_schema_has_no_domain_specific_fields(mock_request):
    """The index describes WHERE a passage came from, not what it's about --
    so the same schema serves any document corpus."""
    mock_request.return_value = MagicMock(status_code=201, raise_for_status=lambda: None)

    pds.create_index(SAMPLE_IDS, FAKE_ADMIN_KEY)

    _method, _path, _ids, _admin_key, body = mock_request.call_args[0]
    field_names = {f["name"] for f in body["fields"]}
    # Exact set -- adding a domain field (partNumber, policyId, ...) to the
    # shared baseline is what this guard is here to catch.
    assert field_names == {
        "chunkId", "parent_id", "content", "contentVector", "sourceDocument",
        "sourceUri", "sectionH1", "sectionH2", "sectionH3",
    }
    serialized = json.dumps(body).lower()
    for domain_term in ("datasheet", "firmware", "hardware", "dev-docs", "technical"):
        assert domain_term not in serialized, f"domain term '{domain_term}' leaked into the index schema"


# --- Azure AI Search API-contract guards ---------------------------------
# Each of these encodes a documented Azure AI Search requirement that was
# violated in an earlier revision of this script. They are regression tests,
# not style checks -- every one of them corresponds to a real failure mode.

@patch("post_deploy_search.search_request")
def test_index_declares_parent_id_for_index_projections(mock_request):
    """indexProjections.parentKeyFieldName points at 'parent_id'; that field
    must exist in the target index, be Edm.String, and be filterable."""
    mock_request.return_value = MagicMock(status_code=201, raise_for_status=lambda: None)

    pds.create_index(SAMPLE_IDS, FAKE_ADMIN_KEY)
    _m, _p, _i, _k, index_body = mock_request.call_args[0]
    parent = next((f for f in index_body["fields"] if f["name"] == "parent_id"), None)
    assert parent is not None, "index projections require a parent_id field"
    assert parent["type"] == "Edm.String"
    assert parent["filterable"] is True
    assert not parent.get("key", False), "parent_id must not be the key field"

    pds.create_skillset(SAMPLE_IDS, FAKE_ADMIN_KEY)
    _m, _p, _i, _k, skillset_body = mock_request.call_args[0]
    selector = skillset_body["indexProjections"]["selectors"][0]
    assert selector["parentKeyFieldName"] == parent["name"]


@patch("post_deploy_search.search_request")
def test_key_field_uses_keyword_analyzer(mock_request):
    """Without the keyword analyzer the default tokenizer splits chunk IDs,
    breaking key lookups and projection dedupe."""
    mock_request.return_value = MagicMock(status_code=201, raise_for_status=lambda: None)

    pds.create_index(SAMPLE_IDS, FAKE_ADMIN_KEY)
    _m, _p, _i, _k, body = mock_request.call_args[0]
    key_field = next(f for f in body["fields"] if f.get("key"))
    assert key_field["analyzer"] == "keyword"
    assert key_field["searchable"] is True


@patch("post_deploy_search.search_request")
def test_semantic_config_fields_are_searchable_and_correctly_named(mock_request):
    """Fields named in a semantic configuration must be searchable, and the
    property is `prioritizedContentFields` -- not `contentFields`. Getting
    either wrong is an HTTP 400 on index creation."""
    mock_request.return_value = MagicMock(status_code=201, raise_for_status=lambda: None)

    pds.create_index(SAMPLE_IDS, FAKE_ADMIN_KEY)
    _m, _p, _i, _k, body = mock_request.call_args[0]
    fields = {f["name"]: f for f in body["fields"]}
    prioritized = body["semantic"]["configurations"][0]["prioritizedFields"]

    assert "contentFields" not in prioritized, "must be prioritizedContentFields"
    assert "prioritizedContentFields" in prioritized

    referenced = [prioritized["titleField"]["fieldName"]]
    for group in ("prioritizedContentFields", "prioritizedKeywordsFields"):
        referenced += [f["fieldName"] for f in prioritized.get(group, [])]
    for field_name in referenced:
        assert fields[field_name].get("searchable") is True, \
            f"semantic field '{field_name}' must be searchable"


@patch("post_deploy_search.search_request")
def test_indexer_allows_skillset_to_read_file_data(mock_request):
    """The Layout skill reads /document/file_data, which only exists when the
    indexer sets allowSkillsetToReadFileData."""
    mock_request.return_value = MagicMock(status_code=201, raise_for_status=lambda: None)

    pds.create_indexer(SAMPLE_IDS, FAKE_ADMIN_KEY)
    _m, _p, _i, _k, body = mock_request.call_args[0]
    config = body["parameters"]["configuration"]
    assert config["allowSkillsetToReadFileData"] is True
    assert config["parsingMode"] == "default"


@patch("post_deploy_search.search_request")
def test_skillset_attaches_billable_ai_services(mock_request):
    """The Document Layout skill is billable; without a cognitiveServices
    attachment the skillset stops after 20 enrichments per run per day."""
    mock_request.return_value = MagicMock(status_code=201, raise_for_status=lambda: None)

    pds.create_skillset(SAMPLE_IDS, FAKE_ADMIN_KEY)
    _m, _p, _i, _k, body = mock_request.call_args[0]
    attachment = body["cognitiveServices"]
    assert attachment["@odata.type"].endswith("AIServicesByIdentity")
    assert attachment["subdomainUrl"]


@patch("post_deploy_search.search_request")
def test_skillset_projects_only_real_layout_outputs(mock_request):
    """In markdown mode the Layout skill emits content/sections/ordinal_position
    per item -- there is no pageNumber or sectionHeading. Projecting invented
    paths writes silent nulls instead of erroring."""
    mock_request.return_value = MagicMock(status_code=201, raise_for_status=lambda: None)

    pds.create_skillset(SAMPLE_IDS, FAKE_ADMIN_KEY)
    _m, _p, _i, _k, body = mock_request.call_args[0]
    sources = [m["source"] for m in body["indexProjections"]["selectors"][0]["mappings"]]

    for invented in ("/pageNumber", "/sectionHeading"):
        assert not any(s.endswith(invented) for s in sources), \
            f"'{invented}' does not exist in Layout markdown output"
    assert any(s.endswith("/sections/h1") for s in sources)


@patch("post_deploy_search.search_request")
def test_embedding_dimensions_match_between_skill_and_index(mock_request):
    """A mismatch between the embedding skill's dimensions and the index's
    vector field width fails at indexing time."""
    mock_request.return_value = MagicMock(status_code=201, raise_for_status=lambda: None)
    ids = {**SAMPLE_IDS, "embeddingDimensions": 1536}

    pds.create_index(ids, FAKE_ADMIN_KEY)
    _m, _p, _i, _k, index_body = mock_request.call_args[0]
    vector_field = next(f for f in index_body["fields"] if f["name"] == "contentVector")

    pds.create_skillset(ids, FAKE_ADMIN_KEY)
    _m, _p, _i, _k, skillset_body = mock_request.call_args[0]
    embed_skill = next(s for s in skillset_body["skills"] if s["@odata.type"].endswith("AzureOpenAIEmbeddingSkill"))

    assert vector_field["dimensions"] == embed_skill["dimensions"] == 1536


@patch("post_deploy_search.search_request")
def test_knowledge_base_references_a_knowledge_source(mock_request):
    """Agentic retrieval uses a two-object model. `targetIndexes` is not a real
    knowledge-base property -- the index is reached via a knowledge source."""
    mock_request.return_value = MagicMock(status_code=201, raise_for_status=lambda: None)

    pds.create_knowledge_source(SAMPLE_IDS, FAKE_ADMIN_KEY)
    _m, source_path, _i, _k, source_body = mock_request.call_args[0]
    assert source_path == "/knowledgesources/ks-documents"
    assert source_body["kind"] == "searchIndex"
    assert source_body["searchIndexParameters"]["searchIndexName"] == "idx-documents"
    # sourceDataFields is what makes references[].sourceData non-empty.
    assert source_body["searchIndexParameters"]["sourceDataFields"]

    pds.create_knowledge_base(SAMPLE_IDS, FAKE_ADMIN_KEY)
    _m, kb_path, _i, _k, kb_body = mock_request.call_args[0]
    assert kb_path == "/knowledgebases/kb-documents"
    assert kb_body["knowledgeSources"] == [{"name": "ks-documents"}]
    assert "targetIndexes" not in kb_body
    assert "defaultRerankerThreshold" not in json.dumps(kb_body)


def test_retrieve_requests_reference_source_data():
    """Without includeReferenceSourceData the service returns
    references[].sourceData = null and all citations come back empty."""
    body = pds.retrieve_body(SAMPLE_IDS, "a question")
    params = body["knowledgeSourceParams"][0]
    assert params["knowledgeSourceName"] == "ks-documents"
    assert params["includeReferenceSourceData"] is True
    assert params["includeReferences"] is True


@patch("post_deploy_search.search_request")
def test_index_respects_configured_embedding_dimensions(mock_request):
    """Swapping the embedding model changes vector width; a hardcoded 3072
    would fail index creation for anyone using a different model."""
    mock_request.return_value = MagicMock(status_code=201, raise_for_status=lambda: None)
    ids = {**SAMPLE_IDS, "embeddingDimensions": 1536, "embeddingModel": "text-embedding-3-small"}

    pds.create_index(ids, FAKE_ADMIN_KEY)

    _method, _path, _ids, _admin_key, body = mock_request.call_args[0]
    vector_field = next(f for f in body["fields"] if f["name"] == "contentVector")
    assert vector_field["dimensions"] == 1536


@patch("post_deploy_search.search_request")
def test_skillset_chunking_is_configurable_per_corpus(mock_request):
    """Dense reference material and long-form prose want different chunking."""
    mock_request.return_value = MagicMock(status_code=201, raise_for_status=lambda: None)
    ids = {**SAMPLE_IDS, "corpus": {"chunkSizeTokens": 800, "chunkOverlapTokens": 400}}

    pds.create_skillset(ids, FAKE_ADMIN_KEY)

    _method, _path, _ids, _admin_key, body = mock_request.call_args[0]
    split_skill = next(s for s in body["skills"] if s["@odata.type"].endswith("SplitSkill"))
    assert split_skill["maximumPageLength"] == 800
    assert split_skill["pageOverlapLength"] == 400


@patch("post_deploy_search.search_request")
def test_pipeline_retargets_to_a_different_corpus(mock_request):
    """End-to-end reuse check: point every name at a non-technical corpus and
    confirm nothing dev-docs-flavored survives in the emitted payloads."""
    mock_request.return_value = MagicMock(status_code=201, raise_for_status=lambda: None)
    hr_ids = {
        **SAMPLE_IDS,
        "searchIndexName": "idx-hr-policies",
        "searchSkillsetName": "skillset-hr-policies",
        "searchDataSourceName": "ds-hr-policies-blob",
        "knowledgeBaseName": "kb-hr-policies",
        "knowledgeSourceName": "ks-hr-policies",
        "corpus": {"displayName": "HR policy"},
    }

    pds.create_index(hr_ids, FAKE_ADMIN_KEY)
    _m, index_path, _i, _k, index_body = mock_request.call_args[0]
    assert index_path == "/indexes('idx-hr-policies')"

    pds.create_skillset(hr_ids, FAKE_ADMIN_KEY)
    _m, skillset_path, _i, _k, skillset_body = mock_request.call_args[0]
    assert skillset_path == "/skillsets('skillset-hr-policies')"
    assert "HR policy" in skillset_body["description"]
    assert skillset_body["indexProjections"]["selectors"][0]["targetIndexName"] == "idx-hr-policies"

    pds.create_knowledge_source(hr_ids, FAKE_ADMIN_KEY)
    _m, source_path, _i, _k, source_body = mock_request.call_args[0]
    assert source_path == "/knowledgesources/ks-hr-policies"
    assert source_body["searchIndexParameters"]["searchIndexName"] == "idx-hr-policies"

    pds.create_knowledge_base(hr_ids, FAKE_ADMIN_KEY)
    _m, kb_path, _i, _k, kb_body = mock_request.call_args[0]
    assert kb_path == "/knowledgebases/kb-hr-policies"
    assert kb_body["knowledgeSources"] == [{"name": "ks-hr-policies"}]

    combined = json.dumps([index_body, skillset_body, source_body, kb_body]).lower()
    assert "dev-docs" not in combined
    assert "datasheet" not in combined
