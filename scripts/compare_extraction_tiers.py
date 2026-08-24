#!/usr/bin/env python3
"""Extraction-tier A/B: Document Intelligence Layout vs. Content Understanding.

Answers "which extraction tier should this pattern use for MY corpus?" with
measurements instead of an opinion, by standing a second ingestion pipeline up
beside the existing one and scoring both on the same documents.

    Tier A (baseline, already deployed by post_deploy_search.py)
        DocumentIntelligenceLayoutSkill (markdown) -> SplitSkill -> embedding
        Citations: heading path (sectionH1/H2/H3)

    Tier B (this script)
        ContentUnderstandingSkill (semantic chunking + figure verbalization)
        Citations: page range (pageNumberFrom/pageNumberTo)

The two tiers cite differently on purpose -- that is one of the findings, not an
oversight. Document Layout gives you a heading path; Content Understanding gives
you a page range plus inlined figure descriptions.

Design note: this deliberately mirrors the reporting philosophy of the sibling
`document-intelligence-vs-content-understanding` demo (normalize both services
into one comparable shape, then emit a scorecard you can show a customer). That
demo compares *field extraction* on forms; this one compares *chunk quality* for
RAG, which is a different axis and needs different metrics.

Usage:
    python compare_extraction_tiers.py --ids-file ../demo-ids.local.json --build-cu
    python compare_extraction_tiers.py --ids-file ../demo-ids.local.json --status
    python compare_extraction_tiers.py --ids-file ../demo-ids.local.json --audit
    python compare_extraction_tiers.py --ids-file ../demo-ids.local.json --golden
    python compare_extraction_tiers.py --ids-file ../demo-ids.local.json --report
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import post_deploy_search as pds  # noqa: E402  (reuse auth, ids loading, HTTP helpers)

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "out"

# Content Understanding skill + semantic chunking are Search-skill features in
# this API version. They call the Content Understanding 2025-11-01 GA backend --
# so this is a GA-backed capability reached through a preview skill surface, not
# a preview service gamble.
CU_API_VERSION = "2026-05-01-preview"

# Tier B resource names. Suffixed so both tiers coexist on one Search service and
# can be compared without tearing the baseline down.
CU_SUFFIX = "-cu"

# Published list prices per 1,000 pages (US East, captured 2026-08-20). These
# move -- re-verify before putting them in front of a customer.
#   https://azure.microsoft.com/pricing/details/document-intelligence/
#   https://azure.microsoft.com/pricing/details/content-understanding/
PRICE_PER_1K_PAGES = {
    "document-intelligence-layout": 10.00,
    "content-understanding-standard": 5.00,
}


# --------------------------------------------------------------------------
# naming
# --------------------------------------------------------------------------

def cu_names(ids: dict) -> dict:
    return {
        "index": ids.get("searchIndexName", "idx-documents") + CU_SUFFIX,
        "skillset": ids.get("searchSkillsetName", "skillset-documents") + CU_SUFFIX,
        "datasource": ids.get("searchDataSourceName", "ds-documents-blob") + CU_SUFFIX,
        "indexer": ids.get("searchIndexerName", "ixr-documents") + CU_SUFFIX,
        "knowledgeSource": ids.get("knowledgeSourceName", "ks-documents") + CU_SUFFIX,
        "knowledgeBase": ids.get("knowledgeBaseName", "kb-documents") + CU_SUFFIX,
    }


def _request(method: str, path: str, ids: dict, admin_key: str, body: dict | None = None,
             api_version: str = CU_API_VERSION) -> requests.Response:
    url = f"{ids['searchEndpoint']}{path}?api-version={api_version}"
    headers = {"api-key": admin_key, "Content-Type": "application/json"}
    return requests.request(method, url, headers=headers, json=body, timeout=120)


# --------------------------------------------------------------------------
# Tier B build
# --------------------------------------------------------------------------

def create_cu_index(ids: dict, admin_key: str) -> None:
    """Tier B index.

    Note the citation fields differ from the baseline: Content Understanding's
    semantic chunking emits `locationMetadata` (page ranges), not the markdown
    `sections` h1/h2/h3 dictionary the Document Layout skill produces. Page
    ranges are a *more* reliable citation for a spec than a heading path that
    can misattribute a parent -- see docs/08-extraction-tier-comparison.md.
    """
    n = cu_names(ids)
    dims = ids.get("embeddingDimensions", pds.DEFAULT_EMBEDDING_DIMENSIONS)
    body = {
        "name": n["index"],
        "fields": [
            {"name": "chunkId", "type": "Edm.String", "key": True, "searchable": True,
             "analyzer": "keyword", "filterable": True},
            {"name": "parent_id", "type": "Edm.String", "searchable": False, "filterable": True,
             "retrievable": True},
            {"name": "content", "type": "Edm.String", "searchable": True, "retrievable": True},
            {"name": "contentVector", "type": "Collection(Edm.Single)", "searchable": True,
             "retrievable": False, "dimensions": dims,
             "vectorSearchProfile": pds.VECTOR_PROFILE_NAME},
            {"name": "sourceDocument", "type": "Edm.String", "searchable": True, "filterable": True,
             "retrievable": True, "facetable": True},
            {"name": "sourceUri", "type": "Edm.String", "searchable": False, "retrievable": True},
            {"name": "pageNumberFrom", "type": "Edm.Int32", "filterable": True, "retrievable": True,
             "sortable": True},
            {"name": "pageNumberTo", "type": "Edm.Int32", "filterable": True, "retrievable": True,
             "sortable": True},
        ],
        "vectorSearch": {
            "profiles": [{"name": pds.VECTOR_PROFILE_NAME,
                          "algorithm": pds.VECTOR_ALGORITHM_NAME,
                          "vectorizer": pds.VECTORIZER_NAME}],
            "algorithms": [{"name": pds.VECTOR_ALGORITHM_NAME, "kind": "hnsw"}],
            "vectorizers": [{
                "name": pds.VECTORIZER_NAME,
                "kind": "azureOpenAI",
                "azureOpenAIParameters": {
                    "resourceUri": ids["foundryOpenAIEndpoint"],
                    "deploymentId": ids.get("embeddingDeployment", "embedding"),
                    "modelName": ids.get("embeddingModel", "text-embedding-3-large"),
                },
            }],
        },
        "semantic": {
            "configurations": [{
                "name": pds.SEMANTIC_CONFIG_NAME,
                "prioritizedFields": {
                    "titleField": {"fieldName": "sourceDocument"},
                    "prioritizedContentFields": [{"fieldName": "content"}],
                },
            }]
        },
    }
    resp = _request("PUT", f"/indexes('{n['index']}')", ids, admin_key, body)
    pds.raise_with_detail(resp)
    print(f"  [CU] index '{n['index']}': {resp.status_code}")


def create_cu_skillset(ids: dict, admin_key: str) -> None:
    """Single skill replaces BOTH the Layout skill and the Split skill.

    Per the Content Understanding skill reference: "You can use the Azure Content
    Understanding skill for both content extraction and chunking. There's no need
    to use the Text Split skill in your skillset."

    `modelName` + `modelDeployment` are what turn empty `<figure></figure>` tags
    into inlined AI-generated descriptions -- the single highest-value setting
    here for a corpus whose figures carry real engineering meaning.
    """
    n = cu_names(ids)
    corpus = pds._corpus(ids)
    label = corpus.get("displayName", "document")
    body = {
        "name": n["skillset"],
        "description": f"Content Understanding extraction + semantic chunking for the {label} corpus.",
        "skills": [
            {
                "@odata.type": "#Microsoft.Skills.Util.ContentUnderstandingSkill",
                "name": "content-understanding",
                "context": "/document",
                # modelName must describe the model actually behind modelDeployment.
                # Hardcoding the doc sample's "gpt-4.1" against a gpt-5-mini
                # deployment produced NO figure descriptions and no error -- the
                # skillset was accepted and the indexer reported success. Derive
                # it from the deployment we actually made.
                "modelName": ids.get("cuFigureModelName") or ids.get("chatModel", "gpt-5-mini"),
                "modelDeployment": ids.get("chatDeployment", "chat"),
                "chunkingProperties": {
                    "method": "semantic",
                    "unit": "tokens",
                    "maximumLength": ids.get("cuChunkTokens", 500),
                },
                "extractionOptions": ["images", "locationMetadata"],
                "inputs": [{"name": "file_data", "source": "/document/file_data"}],
                "outputs": [{"name": "text_sections", "targetName": "text_sections"}],
            },
            {
                "@odata.type": "#Microsoft.Skills.Text.AzureOpenAIEmbeddingSkill",
                "name": "embedding",
                "context": "/document/text_sections/*",
                "resourceUri": ids["foundryOpenAIEndpoint"],
                "deploymentId": ids.get("embeddingDeployment", "embedding"),
                "modelName": ids.get("embeddingModel", "text-embedding-3-large"),
                "dimensions": ids.get("embeddingDimensions", pds.DEFAULT_EMBEDDING_DIMENSIONS),
                "inputs": [{"name": "text", "source": "/document/text_sections/*/content"}],
                "outputs": [{"name": "embedding", "targetName": "contentVector"}],
            },
        ],
        "cognitiveServices": {
            "@odata.type": "#Microsoft.Azure.Search.AIServicesByIdentity",
            "subdomainUrl": pds._ai_services_subdomain_url(ids),
            "identity": None,
        },
        "indexProjections": {
            "selectors": [{
                "targetIndexName": n["index"],
                "parentKeyFieldName": "parent_id",
                "sourceContext": "/document/text_sections/*",
                "mappings": [
                    {"name": "content", "source": "/document/text_sections/*/content"},
                    {"name": "contentVector", "source": "/document/text_sections/*/contentVector"},
                    {"name": "sourceDocument", "source": "/document/metadata_storage_name"},
                    {"name": "sourceUri", "source": "/document/metadata_storage_path"},
                    {"name": "pageNumberFrom",
                     "source": "/document/text_sections/*/locationMetadata/pageNumberFrom"},
                    {"name": "pageNumberTo",
                     "source": "/document/text_sections/*/locationMetadata/pageNumberTo"},
                ],
            }],
            "parameters": {"projectionMode": "skipIndexingParentDocuments"},
        },
    }
    resp = _request("PUT", f"/skillsets('{n['skillset']}')", ids, admin_key, body)
    pds.raise_with_detail(resp)
    print(f"  [CU] skillset '{n['skillset']}': {resp.status_code}")


def create_cu_datasource_and_indexer(ids: dict, admin_key: str) -> None:
    n = cu_names(ids)
    ds = {
        "name": n["datasource"],
        "type": "azureblob",
        "credentials": {"connectionString": (
            f"ResourceId={pds._storage_resource_id(ids)};"
        )},
        "container": {"name": ids.get("rawContainer", "raw")},
    }
    resp = _request("PUT", f"/datasources('{n['datasource']}')", ids, admin_key, ds)
    pds.raise_with_detail(resp)
    print(f"  [CU] data source '{n['datasource']}': {resp.status_code}")

    ixr = {
        "name": n["indexer"],
        "dataSourceName": n["datasource"],
        "targetIndexName": n["index"],
        "skillsetName": n["skillset"],
        "parameters": {
            "batchSize": 1,
            "configuration": {
                "dataToExtract": "contentAndMetadata",
                "parsingMode": "default",
                "allowSkillsetToReadFileData": True,
            },
        },
        "fieldMappings": [
            {"sourceFieldName": "metadata_storage_path", "targetFieldName": "sourceUri"},
        ],
    }
    resp = _request("PUT", f"/indexers('{n['indexer']}')", ids, admin_key, ixr)
    pds.raise_with_detail(resp)
    print(f"  [CU] indexer '{n['indexer']}': {resp.status_code}")


def create_cu_knowledge_base(ids: dict, admin_key: str) -> None:
    n = cu_names(ids)
    src = {
        "name": n["knowledgeSource"],
        "kind": "searchIndex",
        "description": "Content Understanding tier knowledge source.",
        "searchIndexParameters": {
            "searchIndexName": n["index"],
            "semanticConfigurationName": pds.SEMANTIC_CONFIG_NAME,
            "sourceDataFields": [
                {"name": "chunkId"}, {"name": "content"}, {"name": "sourceDocument"},
                {"name": "sourceUri"}, {"name": "pageNumberFrom"}, {"name": "pageNumberTo"},
            ],
        },
    }
    resp = _request("PUT", f"/knowledgesources/{n['knowledgeSource']}", ids, admin_key, src)
    pds.raise_with_detail(resp)
    print(f"  [CU] knowledge source '{n['knowledgeSource']}': {resp.status_code}")

    kb = {
        "name": n["knowledgeBase"],
        "description": "Agentic retrieval over the Content Understanding tier index.",
        "knowledgeSources": [{"name": n["knowledgeSource"]}],
        "models": [{
            "kind": "azureOpenAI",
            "azureOpenAIParameters": {
                "resourceUri": ids["foundryOpenAIEndpoint"],
                "deploymentId": ids.get("chatDeployment", "chat"),
                "modelName": ids.get("chatModel", "gpt-5-mini"),
            },
        }],
        # extractiveData, for the same reason the baseline uses it: the native MCP
        # tool cannot request reference source data, so answerSynthesis silently
        # starves the client of grounded passages.
        "outputMode": ids.get("knowledgeBaseOutputMode", "extractiveData"),
    }
    resp = _request("PUT", f"/knowledgebases/{n['knowledgeBase']}", ids, admin_key, kb)
    pds.raise_with_detail(resp)
    print(f"  [CU] knowledge base '{n['knowledgeBase']}': {resp.status_code}")


def run_cu_indexer(ids: dict, admin_key: str) -> None:
    n = cu_names(ids)
    resp = _request("POST", f"/indexers('{n['indexer']}')/search.run", ids, admin_key)
    pds.raise_with_detail(resp)
    print(f"  [CU] indexer run triggered.")


def indexer_status(ids: dict, admin_key: str, name: str) -> dict:
    resp = _request("GET", f"/indexers('{name}')/search.status", ids, admin_key)
    pds.raise_with_detail(resp)
    return resp.json()


# --------------------------------------------------------------------------
# chunk-quality audit -- the actual comparison
# --------------------------------------------------------------------------

SECTION_NUM = re.compile(r"^\s*(\d+(?:\.\d+)*)\.?\s")

# Language that indicates a vision model actually described an image, rather
# than the text merely mentioning a figure by number. Derived from observed
# Content Understanding output on register bit-field diagrams.
IMAGE_DESCRIPTION_MARKERS = re.compile(
    r"horizontal (?:axis|bar|strip)|boxes containing|segments? (?:with|labeled)|"
    r"The image shows|rectangular (?:bar|box)|bit-?field|divided into",
    re.IGNORECASE,
)


def _fetch_all(ids: dict, admin_key: str, index: str, select: str, page: int = 1000,
               only_source: str | None = None) -> list[dict]:
    """Pull chunks for auditing. Uses skip paging; fine at demo corpus scale.

    `only_source` restricts the audit to a single source document. This matters:
    if one tier rejected a document the other ingested (Content Understanding
    enforces a 300-page-per-file limit that Document Layout does not), comparing
    whole indexes compares different corpora and every metric is confounded.
    """
    out: list[dict] = []
    skip = 0
    flt = f"sourceDocument eq '{only_source}'" if only_source else None
    while True:
        body = {"search": "*", "top": page, "skip": skip, "select": select}
        if flt:
            body["filter"] = flt
        resp = _request("POST", f"/indexes/{index}/docs/search", ids, admin_key, body)
        if resp.status_code == 400 and skip >= 100000:
            break
        pds.raise_with_detail(resp)
        batch = resp.json().get("value", [])
        out.extend(batch)
        if len(batch) < page:
            break
        skip += page
        if skip >= 100000:  # AI Search skip ceiling
            break
    return out


def common_sources(ids: dict, admin_key: str, index_a: str, index_b: str) -> list[str]:
    """Source documents present in BOTH indexes — the only fair comparison set."""
    def sources(idx: str) -> set[str]:
        body = {"search": "*", "top": 0, "facets": ["sourceDocument,count:200"]}
        resp = _request("POST", f"/indexes/{idx}/docs/search", ids, admin_key, body)
        pds.raise_with_detail(resp)
        facets = resp.json().get("@search.facets", {}).get("sourceDocument", [])
        return {f["value"] for f in facets}
    return sorted(sources(index_a) & sources(index_b))


def audit_index(ids: dict, admin_key: str, index: str, tier: str, has_headings: bool,
                only_source: str | None = None) -> dict:
    select = "content,sourceDocument"
    select += ",sectionH1,sectionH2,sectionH3" if has_headings else ",pageNumberFrom,pageNumberTo"
    docs = _fetch_all(ids, admin_key, index, select, only_source=only_source)

    if not docs:
        return {"tier": tier, "index": index, "chunks": 0, "error": "no chunks indexed"}

    lengths = [len(d.get("content") or "") for d in docs]
    sources = sorted({d.get("sourceDocument") for d in docs if d.get("sourceDocument")})

    # --- table integrity -------------------------------------------------
    # A split table shows up in three shapes, and counting only the first
    # undercounts the problem:
    #   1. head/middle: has <table> but the open/close counts don't balance
    #   2. orphaned tail: has </table> (or leading row markup) but NO <table>
    #   3. orphaned body: starts mid-row with <td>/<tr> and no table context
    # Shape 2 is invisible to a "chunks containing <table>" filter, which is
    # exactly the chunk that lost its header row.
    def _table_involved(c: str) -> bool:
        return ("<table>" in c) or ("</table>" in c) or bool(re.match(r"^\s*(<td>|<tr>|</tr>)", c))

    def _table_broken(c: str) -> bool:
        if c.count("<table>") != c.count("</table>"):
            return True
        if "<table>" not in c and ("</table>" in c or re.match(r"^\s*(<td>|<tr>|</tr>)", c)):
            return True
        return False

    table_chunks = [d for d in docs if _table_involved(d.get("content") or "")]
    broken_tables = [d for d in table_chunks if _table_broken(d["content"])]

    # --- figure verbalization -------------------------------------------
    # The two tiers represent figures completely differently, so counting
    # `<figure>` tags alone would score Content Understanding as 0 when it is
    # actually doing MORE. Document Layout emits a literal `<figure>` tag and
    # leaves it empty unless something fills it. Content Understanding emits no
    # tag at all -- when figure description is active it inlines prose
    # describing the image directly into the chunk text.
    fig_chunks = [d for d in docs if "<figure" in (d.get("content") or "")]
    empty_figs = [d for d in fig_chunks if re.search(r"<figure[^>]*>\s*</figure>", d["content"])]
    verbalized = [d for d in docs if IMAGE_DESCRIPTION_MARKERS.search(d.get("content") or "")]

    if fig_chunks:
        figure_metrics = {
            "representation": "<figure> tags",
            "chunks_with_figure_tag": len(fig_chunks),
            "empty_figures": len(empty_figs),
            "verbalized_pct": round(100 * (len(fig_chunks) - len(empty_figs)) / len(fig_chunks), 1),
        }
    else:
        figure_metrics = {
            "representation": "inlined prose description (no <figure> tag emitted)",
            "chunks_with_figure_tag": 0,
            "empty_figures": 0,
            "verbalized_pct": None,
        }
    figure_metrics["chunks_with_image_description"] = len(verbalized)
    figure_metrics["image_description_pct_of_corpus"] = round(100 * len(verbalized) / len(docs), 1)

    # --- citation quality ------------------------------------------------
    citation: dict = {}
    if has_headings:
        pairs = [d for d in docs if (d.get("sectionH2") or "").strip() and (d.get("sectionH3") or "").strip()]
        child = sibling = undeterminable = 0
        examples: list[str] = []
        for d in pairs:
            m2 = SECTION_NUM.match(d["sectionH2"])
            m3 = SECTION_NUM.match(d["sectionH3"])
            if not m2 or not m3:
                undeterminable += 1
                continue
            if m3.group(1).startswith(m2.group(1) + "."):
                child += 1
            else:
                sibling += 1
                if len(examples) < 3:
                    examples.append(f"H2='{d['sectionH2']}' -> H3='{d['sectionH3']}'")
        determinable = child + sibling
        citation = {
            "style": "heading path (sectionH1/H2/H3)",
            "pairs_with_h2_and_h3": len(pairs),
            "determinable": determinable,
            "valid_ancestor": child,
            "wrong_parent": sibling,
            "undeterminable": undeterminable,
            "valid_pct": round(100 * child / determinable, 1) if determinable else None,
            "wrong_parent_examples": examples,
        }
    else:
        with_pages = [d for d in docs if d.get("pageNumberFrom") is not None]
        spanning = [d for d in with_pages
                    if d.get("pageNumberTo") is not None and d["pageNumberTo"] != d["pageNumberFrom"]]
        citation = {
            "style": "page range (pageNumberFrom/pageNumberTo)",
            "chunks_with_page_metadata": len(with_pages),
            "page_metadata_pct": round(100 * len(with_pages) / len(docs), 1),
            "chunks_spanning_pages": len(spanning),
            # A page range is either present and correct or absent -- it cannot
            # assert a wrong hierarchical parent the way a heading path can.
            "structurally_misattributable": False,
        }

    return {
        "tier": tier,
        "index": index,
        "chunks": len(docs),
        "source_documents": sources,
        "chunk_chars": {
            "avg": round(statistics.mean(lengths)),
            "median": round(statistics.median(lengths)),
            "min": min(lengths),
            "max": max(lengths),
            "under_300": sum(1 for x in lengths if x < 300),
            "over_6000": sum(1 for x in lengths if x > 6000),
        },
        "tables": {
            "chunks_with_table": len(table_chunks),
            "split_across_chunks": len(broken_tables),
            "intact_pct": round(100 * (len(table_chunks) - len(broken_tables)) / len(table_chunks), 1)
            if table_chunks else None,
        },
        "figures": figure_metrics,
        "citation": citation,
    }


# --------------------------------------------------------------------------
# golden set
# --------------------------------------------------------------------------

DEFAULT_GOLDEN = [
    {"q": "What does the dmcontrol register control in the Debug Module?",
     "expect": "riscv-debug-specification.pdf"},
    {"q": "How does the JTAG Debug Transport Module encode DMI operations?",
     "expect": "riscv-debug-specification.pdf"},
    {"q": "What is the encoding format of the RISC-V compressed instruction set?",
     "expect": "riscv-spec.pdf"},
    {"q": "Describe the RVWMO memory consistency model and its preserved program order rules.",
     "expect": "riscv-spec.pdf"},
]


def _sources_from_payload(payload: dict) -> list[str]:
    """Source document titles, ranked, from either knowledge-base response shape.

    The two surfaces differ and both are legitimate:
      * `POST /knowledgebases/{kb}/retrieve` returns synthesised prose in
        `response[0].content[0].text` with `[ref_id:N]` markers, and the actual
        citations in a top-level `references` array.
      * The native MCP `tools/call` path (extractiveData) returns the ranked
        passages themselves as a JSON array inside that same `text` field.

    Parsing only the second shape scores the first as zero matches, which looks
    like a retrieval regression when nothing is wrong.
    """
    refs = payload.get("references") or []
    if refs:
        # Already ranked by the service; preserve order, drop duplicates.
        seen, titles = set(), []
        for r in refs:
            t = r.get("title")
            if t and t not in seen:
                seen.add(t)
                titles.append(t)
        if titles:
            return titles
    try:
        text = payload["response"][0]["content"][0]["text"]
        parsed = json.loads(text)
        seen, titles = set(), []
        for r in parsed:
            t = r.get("title")
            if t and t not in seen:
                seen.add(t)
                titles.append(t)
        return titles
    except Exception:
        return []


def golden_run(ids: dict, admin_key: str, kb_name: str, golden: list[dict],
               knowledge_source: str | None = None) -> dict:
    results = []
    for g in golden:
        body = pds.retrieve_body(ids, g["q"])
        # retrieve_body defaults to the BASELINE knowledge source. Pointing a
        # knowledge base at a source it doesn't own returns an empty result with
        # HTTP 200 -- which reads as "retrieval failed" rather than "you asked
        # the wrong question of the wrong object".
        if knowledge_source:
            body["knowledgeSourceParams"][0]["knowledgeSourceName"] = knowledge_source
        started = time.time()
        resp = _request("POST", f"/knowledgebases/{kb_name}/retrieve", ids, admin_key, body)
        elapsed = time.time() - started
        if resp.status_code >= 400:
            results.append({"query": g["q"], "expected_source": g["expect"],
                            "error": f"{resp.status_code}: {resp.text[:200]}", "correct": False})
            continue
        payload = resp.json()
        titles = _sources_from_payload(payload)
        top = titles[0] if titles else None
        results.append({
            "query": g["q"],
            "expected_source": g["expect"],
            "top_source": top,
            "correct": top == g["expect"] if top else False,
            "distinct_sources": titles,
            "refs_returned": len(payload.get("references") or []),
            "elapsed_seconds": round(elapsed, 2),
        })
    passed = sum(1 for r in results if r.get("correct"))
    return {"knowledge_base": kb_name, "passed": passed, "total": len(golden), "results": results}


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------

def _pct(v) -> str:
    return "n/a" if v is None else f"{v}%"


def _esc(s) -> str:
    return str(s).replace("|", "\\|")


def render_report(payload: dict) -> str:
    a, b = payload.get("tier_a"), payload.get("tier_b")
    ga, gb = payload.get("golden_a"), payload.get("golden_b")
    L: list[str] = []
    L.append("# Extraction tier comparison — Document Intelligence Layout vs. Content Understanding")
    L.append("")
    L.append(f"*Generated {payload['generated']} · corpus: {payload.get('corpus_label','(unspecified)')}*")
    L.append("")
    L.append(f"**Audit scope:** {payload.get('audit_scope','all shared documents')} — restricted to what")
    L.append("**both** tiers successfully ingested, so a document one tier rejected cannot confound the")
    L.append("metrics. Any document a tier could not ingest is reported under *Ingestion* below, which")
    L.append("is a finding in its own right rather than a footnote.")
    L.append("")
    L.append("Both tiers ingest the **same blobs** into the **same Search service** with the same")
    L.append("embedding model, so differences below are attributable to the extraction tier.")
    L.append("")

    if not a or not b or a.get("error") or b.get("error"):
        L.append("> One or both tiers produced no chunks — see the raw JSON for detail.")
        if a and a.get("error"):
            L.append(f"> Tier A error: {a['error']}")
        if b and b.get("error"):
            L.append(f"> Tier B error: {b['error']}")
        L.append("")

    if a and b and not a.get("error") and not b.get("error"):
        L.append("## Scorecard")
        L.append("")
        L.append("| Metric | Tier A · DI Layout + Split | Tier B · Content Understanding | Winner |")
        L.append("|---|---|---|---|")

        def row(label, va, vb, winner):
            L.append(f"| {label} | {_esc(va)} | {_esc(vb)} | {winner} |")

        row("Chunks produced", a["chunks"], b["chunks"], "—")
        row("Source documents indexed", len(a["source_documents"]), len(b["source_documents"]), "—")
        ta, tb = a["tables"], b["tables"]
        win_t = "B" if (tb["intact_pct"] or 0) > (ta["intact_pct"] or 0) else ("A" if (ta["intact_pct"] or 0) > (tb["intact_pct"] or 0) else "tie")
        row("Tables intact (not split)", f"{_pct(ta['intact_pct'])} ({ta['chunks_with_table'] - ta['split_across_chunks']}/{ta['chunks_with_table']})",
            f"{_pct(tb['intact_pct'])} ({tb['chunks_with_table'] - tb['split_across_chunks']}/{tb['chunks_with_table']})", win_t)
        fa, fb = a["figures"], b["figures"]
        row("Figure representation", fa["representation"], fb["representation"], "—")
        row("Chunks carrying an image description",
            f"{fa['chunks_with_image_description']} ({fa['image_description_pct_of_corpus']}%)",
            f"{fb['chunks_with_image_description']} ({fb['image_description_pct_of_corpus']}%)",
            "B" if fb["chunks_with_image_description"] > fa["chunks_with_image_description"]
            else ("A" if fa["chunks_with_image_description"] > fb["chunks_with_image_description"] else "tie"))
        if fa["chunks_with_figure_tag"]:
            row("Empty `<figure></figure>` tags",
                f"{fa['empty_figures']} of {fa['chunks_with_figure_tag']}",
                f"{fb['empty_figures']} of {fb['chunks_with_figure_tag']}" if fb["chunks_with_figure_tag"] else "n/a (no tags emitted)",
                "B")
        row("Citation style", a["citation"]["style"], b["citation"]["style"], "—")
        row("Citation can misattribute?",
            f"YES — {a['citation'].get('wrong_parent','?')} wrong-parent of {a['citation'].get('determinable','?')} determinable ({round(100 - (a['citation'].get('valid_pct') or 0), 1)}%)",
            "NO — a page range is present or absent, never structurally wrong", "B")
        ca, cb = a["chunk_chars"], b["chunk_chars"]
        row("Chunk chars (avg / min / max)", f"{ca['avg']} / {ca['min']} / {ca['max']}",
            f"{cb['avg']} / {cb['min']} / {cb['max']}", "—")
        row("Low-context chunks (<300 chars)", ca["under_300"], cb["under_300"],
            "B" if cb["under_300"] < ca["under_300"] else ("A" if ca["under_300"] < cb["under_300"] else "tie"))
        L.append("")

    if ga or gb:
        L.append("## Retrieval golden set")
        L.append("")
        L.append(f"Tier A: **{ga['passed']}/{ga['total']}** correct top source · "
                 f"Tier B: **{gb['passed']}/{gb['total']}** correct top source" if ga and gb else "")
        L.append("")
        L.append("| Question | Expected | A top source | A ok | B top source | B ok |")
        L.append("|---|---|---|---|---|---|")
        ra = {r["query"]: r for r in (ga or {}).get("results", [])}
        rb = {r["query"]: r for r in (gb or {}).get("results", [])}
        for q in [r["query"] for r in (ga or {}).get("results", [])]:
            x, y = ra.get(q, {}), rb.get(q, {})
            L.append(f"| {_esc(q[:58])} | {_esc(x.get('expected_source','—'))} | "
                     f"{_esc(x.get('top_source','—'))} | {'OK' if x.get('correct') else 'MISS'} | "
                     f"{_esc(y.get('top_source','—'))} | {'OK' if y.get('correct') else 'MISS'} |")
        L.append("")

    ing = payload.get("ingestion") or {}
    if ing:
        L.append("## Ingestion cost and durability")
        L.append("")
        L.append("| | Tier A · DI Layout | Tier B · Content Understanding |")
        L.append("|---|---|---|")
        L.append(f"| Corpus documents ingested | {ing.get('a_docs','—')} | {ing.get('b_docs','—')} |")
        L.append(f"| Last indexer run — processed / failed | {ing.get('a_succeeded','—')} / {ing.get('a_failed','—')} | {ing.get('b_succeeded','—')} / {ing.get('b_failed','—')} |")
        L.append(f"| List price / 1,000 pages | ${PRICE_PER_1K_PAGES['document-intelligence-layout']:.2f} | ${PRICE_PER_1K_PAGES['content-understanding-standard']:.2f} |")
        L.append("")
        L.append("*Last-run counters reflect the most recent (often incremental) run — a run that found")
        L.append("no changed blobs reports 0 processed. \"Corpus documents ingested\" is the durable number.*")
        L.append("")
        if ing.get("b_errors"):
            L.append("**Tier B ingestion errors.** Per-document hard limits are a first-class selection")
            L.append("criterion for a technical-manual corpus, not an operational footnote:")
            L.append("")
            for e in ing["b_errors"][:5]:
                L.append(f"- {_esc(e)}")
            L.append("")

    L.append("## How to read this")
    L.append("")
    L.append("- **Tables and figures** are where the extraction tier actually shows up. A split table")
    L.append("  loses its header row, so the surviving cells lose their meaning entirely; an empty")
    L.append("  `<figure></figure>` means a diagram was detected and then discarded.")
    L.append("- **Citation style is a real trade-off, not a tie.** A heading path is more human-readable")
    L.append("  than a page range — but only if it is correct. A wrong parent is worse than a coarse")
    L.append("  citation, because it looks authoritative while pointing somewhere else.")
    L.append("- **Chunk count alone means nothing.** More chunks is not better; check the low-context")
    L.append("  count, which is retrieval noise.")
    L.append("")
    L.append("Prices are list, US East, captured 2026-08-20 and subject to change — re-verify before")
    L.append("quoting. For the *field extraction* comparison (confidence, template drift, tiered")
    L.append("routing), see the sibling `document-intelligence-vs-content-understanding` demo; this")
    L.append("report covers the *RAG chunk quality* axis only.")
    L.append("")
    return "\n".join(L)


# --------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ids-file", required=True)
    p.add_argument("--build-cu", action="store_true",
                   help="Create the Tier B index, skillset, data source, indexer, knowledge base and start indexing")
    p.add_argument("--status", action="store_true", help="Show both indexers' status")
    p.add_argument("--audit", action="store_true", help="Score chunk quality on both indexes")
    p.add_argument("--golden", action="store_true", help="Run the golden set against both knowledge bases")
    p.add_argument("--report", action="store_true", help="Audit + golden + write the scorecard to out/")
    p.add_argument("--teardown", action="store_true", help="Delete Tier B objects only")
    p.add_argument("--only-source", default=None,
                   help="Restrict the audit to one sourceDocument. Defaults to auto-detecting the "
                        "documents both tiers actually ingested, so a document one tier rejected "
                        "cannot confound the comparison.")
    args = p.parse_args()

    ids = pds.load_ids(args.ids_file)
    admin_key = pds.get_admin_key(ids)
    n = cu_names(ids)

    if args.teardown:
        for kind, name in [("knowledgebases", n["knowledgeBase"]), ("knowledgesources", n["knowledgeSource"])]:
            r = _request("DELETE", f"/{kind}/{name}", ids, admin_key)
            print(f"  deleted {kind}/{name}: {r.status_code}")
        for kind, name in [("indexers", n["indexer"]), ("skillsets", n["skillset"]),
                           ("datasources", n["datasource"]), ("indexes", n["index"])]:
            r = _request("DELETE", f"/{kind}('{name}')", ids, admin_key)
            print(f"  deleted {kind}/{name}: {r.status_code}")
        return

    if args.build_cu:
        print("Building Tier B (Content Understanding)...")
        create_cu_index(ids, admin_key)
        create_cu_skillset(ids, admin_key)
        create_cu_datasource_and_indexer(ids, admin_key)
        create_cu_knowledge_base(ids, admin_key)
        run_cu_indexer(ids, admin_key)
        print("  [CU] build complete — poll with --status")

    if args.status:
        for label, name in [("Tier A", ids.get("searchIndexerName", "ixr-documents")),
                            ("Tier B", n["indexer"])]:
            try:
                s = indexer_status(ids, admin_key, name)
                last = s.get("lastResult") or {}
                print(f"{label} [{name}]: {last.get('status')} "
                      f"processed={last.get('itemsProcessed')} failed={last.get('itemsFailed')}")
                for e in (last.get("errors") or [])[:3]:
                    print(f"    ERROR: {e.get('errorMessage','')[:220]}")
                for w in (last.get("warnings") or [])[:2]:
                    print(f"    warn: {w.get('message','')[:160]}")
            except Exception as exc:
                print(f"{label} [{name}]: unavailable ({exc})")

    if args.audit or args.report:
        idx_a = ids.get("searchIndexName", "idx-documents")
        scope = args.only_source
        if scope is None:
            shared = common_sources(ids, admin_key, idx_a, n["index"])
            if len(shared) == 1:
                scope = shared[0]
                print(f"Scoping audit to the only document both tiers ingested: {scope}")
            elif not shared:
                print("WARNING: the two tiers share no source documents — metrics are not comparable.")
            else:
                print(f"Both tiers ingested {len(shared)} shared documents; auditing all of them.")
        print("Auditing chunk quality...")
        a = audit_index(ids, admin_key, idx_a,
                        "Document Intelligence Layout + Split", has_headings=True, only_source=scope)
        b = audit_index(ids, admin_key, n["index"], "Content Understanding (semantic)",
                        has_headings=False, only_source=scope)
        audit_scope = scope
        print(json.dumps({"tier_a": a, "tier_b": b}, indent=2)[:1800])

    if args.golden or args.report:
        # Only ask questions whose expected source exists in BOTH tiers. Asking
        # Tier B about a document it could not ingest measures the page-count
        # limit a second time, not retrieval quality.
        shared = set(common_sources(ids, admin_key, ids.get("searchIndexName", "idx-documents"), n["index"]))
        golden = [g for g in DEFAULT_GOLDEN if not shared or g["expect"] in shared]
        skipped = [g for g in DEFAULT_GOLDEN if g not in golden]
        if skipped:
            print(f"  skipping {len(skipped)} golden question(s) targeting documents only one tier ingested")
        print("Running golden set...")
        ga = golden_run(ids, admin_key, ids.get("knowledgeBaseName", "kb-documents"), golden,
                        knowledge_source=ids.get("knowledgeSourceName", "ks-documents"))
        gb = golden_run(ids, admin_key, n["knowledgeBase"], golden,
                        knowledge_source=n["knowledgeSource"])
        golden_used = golden
        print(f"  Tier A {ga['passed']}/{ga['total']} · Tier B {gb['passed']}/{gb['total']}")

    if args.report:
        ing = {}
        try:
            # Durable corpus counts, independent of whether the last run was incremental.
            idx_a_name = ids.get("searchIndexName", "idx-documents")
            ing["a_docs"] = len(common_sources(ids, admin_key, idx_a_name, idx_a_name))
            ing["b_docs"] = len(common_sources(ids, admin_key, n["index"], n["index"]))
        except Exception:
            pass
        try:
            sa = (indexer_status(ids, admin_key, ids.get("searchIndexerName", "ixr-documents")).get("lastResult") or {})
            sb = (indexer_status(ids, admin_key, n["indexer"]).get("lastResult") or {})
            ing = {
                "a_docs": ing.get("a_docs"), "b_docs": ing.get("b_docs"),
                "a_succeeded": sa.get("itemsProcessed"), "a_failed": sa.get("itemsFailed"),
                "b_succeeded": sb.get("itemsProcessed"), "b_failed": sb.get("itemsFailed"),
                "b_errors": [
                    # `errorMessage` is generic ("Could not execute skill..."); the
                    # actionable cause (e.g. InputPageCountExceeded) is in `details`.
                    " ".join(filter(None, [e.get("errorMessage", ""), e.get("details", "")]))[:600]
                    for e in (sb.get("errors") or [])
                ],
            }
        except Exception:
            pass

        payload = {
            "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "corpus_label": pds._corpus(ids).get("displayName"),
            "audit_scope": audit_scope or "all shared documents",
            "tier_a": a, "tier_b": b, "golden_a": ga, "golden_b": gb, "ingestion": ing,
        }
        OUT_DIR.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        (OUT_DIR / f"extraction-comparison-{stamp}.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8")
        md_path = OUT_DIR / f"extraction-comparison-{stamp}.md"
        md_path.write_text(render_report(payload), encoding="utf-8")
        print(f"\nWrote {md_path}")


if __name__ == "__main__":
    main()
