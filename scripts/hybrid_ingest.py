#!/usr/bin/env python3
"""Hybrid ingestion: route each document to the extraction tier that handles it best.

Neither extraction tier wins outright, so this pattern uses both and routes per
document. The measurements behind this live in docs/08-extraction-tier-comparison.md.

    Tier CU   (<= 300 pages)   Content Understanding, semantic chunking
                               Best chunk quality: 100% table integrity, native
                               figure verbalization, consistent chunk sizes,
                               half the list price. HARD LIMIT: 300 pages/file.

    Tier DI+  (> 300 pages)    Document Intelligence Layout (markdown + images)
                               + Split + a GenAI Prompt image-verbalization skill.
                               No page ceiling, and the added vision skill fills
                               in the figure descriptions CU would have given us.

IMPORTANT — the routing here is INVERTED relative to the sibling
`document-intelligence-vs-content-understanding` demo. There, Document
Intelligence is the cheap/fast tier 1 and Content Understanding is the expensive
escalation tier, and the trigger is a per-field confidence score. Here Content
Understanding is BOTH cheaper AND higher quality, so Document Intelligence is not
tier 1 at all -- it is the capability fallback for documents Content
Understanding structurally cannot accept. The escalation trigger is a hard input
constraint (page count), known before any spend, not a confidence signal
discovered after it.

That distinction matters: a confidence-based cascade pays for tier 1 on every
document and then pays again on escalation. A constraint-based router pays once,
because the routing decision is free -- page count is local metadata.

Both tiers write to ONE unified index with an `extractionTier` provenance field,
so a single knowledge base (and therefore a single MCP endpoint) serves the whole
corpus and the coding assistant never sees which tier produced an answer.

Usage:
    python hybrid_ingest.py --ids-file ../demo-ids.local.json --plan --source-dir ../samples/corpus
    python hybrid_ingest.py --ids-file ../demo-ids.local.json --upload --source-dir ../samples/corpus
    python hybrid_ingest.py --ids-file ../demo-ids.local.json --build
    python hybrid_ingest.py --ids-file ../demo-ids.local.json --status
    python hybrid_ingest.py --ids-file ../demo-ids.local.json --teardown
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import post_deploy_search as pds  # noqa: E402

API_VERSION = "2026-05-01-preview"

# Content Understanding rejects any file above this with InputPageCountExceeded.
# Verified live 2026-08-20 on a 906-page specification.
CU_MAX_PAGES = 300

# Blob prefixes the router sorts documents into. Two data sources scope to these
# folders, which is how one container feeds two indexers -- AI Search indexers
# cannot filter by page count, but they can scope to a folder.
PREFIX_CU = "cu"
PREFIX_DI = "di"

TIER_CU = "content-understanding"
TIER_DI = "di-layout-verbalized"


def h_names(ids: dict) -> dict:
    base = ids.get("searchIndexName", "idx-documents")
    return {
        "index": base + "-hybrid",
        "skillsetCu": "skillset-hybrid-cu",
        "skillsetDi": "skillset-hybrid-di",
        "dsCu": "ds-hybrid-cu",
        "dsDi": "ds-hybrid-di",
        "indexerCu": "ixr-hybrid-cu",
        "indexerDi": "ixr-hybrid-di",
        "knowledgeSource": "ks-hybrid",
        "knowledgeBase": "kb-hybrid",
    }


def _req(method: str, path: str, ids: dict, key: str, body: dict | None = None) -> requests.Response:
    url = f"{ids['searchEndpoint']}{path}?api-version={API_VERSION}"
    return requests.request(method, url, headers={"api-key": key, "Content-Type": "application/json"},
                            json=body, timeout=120)


# --------------------------------------------------------------------------
# routing
# --------------------------------------------------------------------------

def page_count(path: Path) -> int | None:
    try:
        from pypdf import PdfReader
    except ImportError:
        print("  ! pypdf not installed -- run: pip install pypdf")
        return None
    try:
        return len(PdfReader(str(path)).pages)
    except Exception as exc:
        print(f"  ! could not read {path.name}: {exc}")
        return None


def split_oversized(source_dir: str, out_dir: str, max_pages: int = CU_MAX_PAGES) -> list[dict]:
    """Split PDFs larger than `max_pages` into page-ranged parts.

    This is the remedy for the two independent problems that oversized documents
    cause, and it fixes both at once:

      1. Content Understanding hard-rejects files over 300 pages, so an
         unsplit reference manual can only ever use the lower-quality tier.
      2. Figure verbalization fires one vision call per figure. A single
         900-page document produces a burst large enough to exhaust the
         deployment's requests-per-minute ceiling (surfacing as a misleading
         30s *timeout*), and long runs additionally hit transient upstream
         500s. Because AI Search treats a document as one unit, a late failure
         discards the whole document's enrichment.

    Splitting makes every part eligible for the better tier AND makes any
    failure cheap and localised instead of catastrophic.

    Part filenames carry their source page range -- `manual__p001-300.pdf` --
    so a citation remains traceable to a page in the ORIGINAL document. Without
    that, page numbers silently become part-relative and every citation is
    quietly wrong by an offset.
    """
    from pypdf import PdfReader, PdfWriter

    src = Path(source_dir)
    dst = Path(out_dir)
    dst.mkdir(parents=True, exist_ok=True)
    produced: list[dict] = []

    for pdf in sorted(src.glob("*.pdf")):
        pages = page_count(pdf)
        if pages is None:
            print(f"  ! skipping {pdf.name} (page count unavailable)")
            continue
        if pages <= max_pages:
            target = dst / pdf.name
            if target.resolve() != pdf.resolve():
                shutil.copy2(pdf, target)
            produced.append({"file": target.name, "path": str(target), "pages": pages, "split": False})
            print(f"  {pdf.name}: {pages} pages -- no split needed")
            continue

        reader = PdfReader(str(pdf))
        parts = (pages + max_pages - 1) // max_pages
        print(f"  {pdf.name}: {pages} pages -> {parts} parts of <= {max_pages}")
        stem = pdf.stem
        for i in range(parts):
            first = i * max_pages
            last = min(first + max_pages, pages)
            writer = PdfWriter()
            for p in range(first, last):
                writer.add_page(reader.pages[p])
            # 1-based, inclusive -- matches how a human cites a page.
            name = f"{stem}__p{first + 1:04d}-{last:04d}.pdf"
            target = dst / name
            with open(target, "wb") as fh:
                writer.write(fh)
            produced.append({"file": name, "path": str(target), "pages": last - first, "split": True})
            print(f"    wrote {name} ({last - first} pages)")

    return produced


def plan(source_dir: str) -> list[dict]:
    """Decide a tier per document. Free -- page count is local metadata, so the
    routing decision costs nothing and is made before any service is called."""
    rows = []
    for p in sorted(Path(source_dir).glob("*.pdf")):
        pages = page_count(p)
        if pages is None:
            tier, why = TIER_DI, "page count unavailable -- defaulting to the tier with no page ceiling"
        elif pages <= CU_MAX_PAGES:
            tier, why = TIER_CU, f"{pages} pages <= {CU_MAX_PAGES} limit"
        else:
            tier, why = TIER_DI, f"{pages} pages exceeds Content Understanding's {CU_MAX_PAGES}-page limit"
        rows.append({"file": p.name, "path": str(p), "pages": pages, "tier": tier, "reason": why})
    return rows


def print_plan(rows: list[dict]) -> None:
    print(f"\n{'Document':<44} {'Pages':>7}  {'Tier':<26} Why")
    print("-" * 118)
    for r in rows:
        print(f"{r['file'][:43]:<44} {str(r['pages']):>7}  {r['tier']:<26} {r['reason']}")
    cu = sum(1 for r in rows if r["tier"] == TIER_CU)
    di = len(rows) - cu
    print(f"\n  {cu} document(s) -> Content Understanding · {di} -> Document Layout + verbalization")
    if di:
        print(f"  Consider splitting the {di} oversized document(s) into <= {CU_MAX_PAGES}-page parts to keep")
        print("  the whole corpus on the higher-quality tier (see docs/08 § options).")


def oversized_guard(rows: list[dict], split: bool, no_split: bool, uploading: bool) -> None:
    """Refuse to upload an oversized document unless the operator chose how to handle it.

    An oversized PDF silently routed to Tier DI+ still works, but it forfeits
    Content Understanding's quality AND concentrates hundreds of vision calls in
    one document, where a single late failure discards the whole document's
    enrichment (docs/09). That is a decision, not a default -- so make it explicit.
    """
    oversized = [r for r in rows if r["pages"] is not None and r["pages"] > CU_MAX_PAGES]
    if not oversized or split or no_split:
        return
    names = ", ".join(f"{r['file']} ({r['pages']} pages)" for r in oversized)
    msg = (f"\n  ! {len(oversized)} document(s) exceed Content Understanding's {CU_MAX_PAGES}-page limit: {names}\n"
           "    Choose explicitly:\n"
           "      --split     split into page-ranged parts (recommended: every part gets the CU tier, and a\n"
           "                  failure costs one part instead of the whole document)\n"
           "      --no-split  keep them whole and route to Document Layout + verbalization (Tier DI+)\n")
    if uploading:
        raise SystemExit(msg + "    Upload stopped -- nothing was uploaded.")
    print(msg)


def upload(ids: dict, rows: list[dict]) -> None:
    """Upload each document under the blob prefix its tier's data source scopes to."""
    from azure.core.exceptions import HttpResponseError
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobServiceClient

    account = ids["storageAccount"]
    container = ids.get("rawContainer", "raw")
    svc = BlobServiceClient(f"https://{account}.blob.core.windows.net",
                            credential=DefaultAzureCredential())
    cc = svc.get_container_client(container)
    for r in rows:
        prefix = PREFIX_CU if r["tier"] == TIER_CU else PREFIX_DI
        blob = f"{prefix}/{r['file']}"
        try:
            with open(r["path"], "rb") as fh:
                cc.upload_blob(name=blob, data=fh, overwrite=True)
        except HttpResponseError as exc:
            if "AuthorizationFailure" in str(exc) or "not authorized" in str(exc).lower():
                print(
                    f"\n  ! Blob upload denied for '{account}'.\n"
                    "\n"
                    "    This is almost always a NETWORK rule, not an RBAC problem -- governed\n"
                    "    subscriptions apply a policy that forces publicNetworkAccess=Disabled on\n"
                    "    Storage, and an explicit re-enable is silently reverted. Confirm with:\n"
                    f"      az storage account show -n {account} -g {ids.get('resourceGroup','<rg>')} --query publicNetworkAccess -o tsv\n"
                    "\n"
                    "    If it reports 'Disabled', associate the account with a Network Security\n"
                    "    Perimeter and set publicNetworkAccess=SecuredByPerimeter -- full steps in\n"
                    "    docs/05-troubleshooting.md section 1 (Foundation). Propagation takes a\n"
                    "    few minutes before the data plane accepts requests.\n"
                )
                raise SystemExit(1)
            raise
        print(f"  uploaded {blob}  ({r['tier']})")


# --------------------------------------------------------------------------
# unified index
# --------------------------------------------------------------------------

def create_index(ids: dict, key: str) -> None:
    """One index for both tiers.

    `sectionLabel` holds the DEEPEST heading only -- never a path. The Document
    Layout skill's `sections` dictionary is the most-recent-heading-seen at each
    level, not a validated ancestor chain, so rendering h1 > h2 > h3 asserts a
    parent relationship that is wrong ~21.7% of the time. The deepest heading on
    its own is accurate; only the implied ancestry is false. Dropping the path
    fixes the defect at zero cost.
    """
    n = h_names(ids)
    dims = ids.get("embeddingDimensions", pds.DEFAULT_EMBEDDING_DIMENSIONS)
    body = {
        "name": n["index"],
        "fields": [
            {"name": "chunkId", "type": "Edm.String", "key": True, "searchable": True,
             "analyzer": "keyword", "filterable": True},
            {"name": "parent_id", "type": "Edm.String", "filterable": True, "retrievable": True},
            # A second parent key is required, not cosmetic: AI Search rejects two
            # index projection selectors that target the same index with the same
            # parentKeyFieldName. Text chunks and image-description rows are
            # projected from different enrichment contexts, so they need distinct
            # parent keys.
            {"name": "image_parent_id", "type": "Edm.String", "filterable": True, "retrievable": True},
            {"name": "content", "type": "Edm.String", "searchable": True, "retrievable": True},
            {"name": "contentVector", "type": "Collection(Edm.Single)", "searchable": True,
             "retrievable": False, "dimensions": dims,
             "vectorSearchProfile": pds.VECTOR_PROFILE_NAME},
            {"name": "sourceDocument", "type": "Edm.String", "searchable": True, "filterable": True,
             "retrievable": True, "facetable": True},
            {"name": "sourceUri", "type": "Edm.String", "retrievable": True},
            # Provenance: which tier produced this chunk. Lets you audit quality
            # per tier after the fact, and filter if one tier proves unreliable.
            {"name": "extractionTier", "type": "Edm.String", "filterable": True, "facetable": True,
             "retrievable": True},
            # "text" | "image-description" -- image descriptions are projected as
            # their own rows (the documented multimodal pattern), because they
            # live at a different enrichment context than the text chunks.
            {"name": "contentKind", "type": "Edm.String", "filterable": True, "facetable": True,
             "retrievable": True},
            {"name": "sectionLabel", "type": "Edm.String", "searchable": True, "filterable": True,
             "retrievable": True},
            {"name": "pageNumberFrom", "type": "Edm.Int32", "filterable": True, "sortable": True,
             "retrievable": True},
            {"name": "pageNumberTo", "type": "Edm.Int32", "filterable": True, "sortable": True,
             "retrievable": True},
        ],
        "vectorSearch": {
            "profiles": [{"name": pds.VECTOR_PROFILE_NAME, "algorithm": pds.VECTOR_ALGORITHM_NAME,
                          "vectorizer": pds.VECTORIZER_NAME}],
            "algorithms": [{"name": pds.VECTOR_ALGORITHM_NAME, "kind": "hnsw"}],
            "vectorizers": [{
                "name": pds.VECTORIZER_NAME, "kind": "azureOpenAI",
                "azureOpenAIParameters": {
                    "resourceUri": ids["foundryOpenAIEndpoint"],
                    "deploymentId": ids.get("embeddingDeployment", "embedding"),
                    "modelName": ids.get("embeddingModel", "text-embedding-3-large"),
                },
            }],
        },
        "semantic": {"configurations": [{
            "name": pds.SEMANTIC_CONFIG_NAME,
            "prioritizedFields": {
                "titleField": {"fieldName": "sourceDocument"},
                "prioritizedContentFields": [{"fieldName": "content"}],
                "prioritizedKeywordsFields": [{"fieldName": "sectionLabel"}],
            },
        }]},
    }
    r = _req("PUT", f"/indexes('{n['index']}')", ids, key, body)
    pds.raise_with_detail(r)
    print(f"  unified index '{n['index']}': {r.status_code}")


def _frontier_model(ids: dict) -> tuple[str, str]:
    """(deployment, modelName) for the general frontier reasoning/vision model.

    Used by the DI+ image verbalizer (ChatCompletionSkill, no model allowlist)
    and knowledge-base query planning. Kept in one place so "are we on a frontier
    model?" has a single answer rather than several drifting ones.

    `modelName` MUST name the model actually behind the deployment."""
    return (ids.get("frontierDeployment", "sol"),
            ids.get("frontierModel", "gpt-5.6-sol"))


def _cu_model(ids: dict) -> tuple[str, str]:
    """(deployment, modelName) for Content Understanding's OWN figure-description
    feature.

    RETAINED FOR REFERENCE ONLY — this pattern no longer uses it. Figures on both
    tiers are handled by the GA `ChatCompletionSkill` (see `_vision_skill`),
    because CU's native figure description is preview and failed consistently on
    a clean rebuild.

    Kept because the constraint is worth remembering if you revisit it: the
    Content Understanding skill enforces its own model allowlist that lags the
    Foundry catalog, and the rejection error helpfully enumerates the ceiling:

        'modelName' has value 'gpt-5.6-sol' which is not supported by Content
        Understanding. Supported completion models: 'gpt-4.1', 'gpt-4.1-mini',
        'gpt-4.1-nano', 'gpt-4o', 'gpt-4o-mini', 'gpt-5', 'gpt-5-mini',
        'gpt-5-nano', 'gpt-5.1', 'gpt-5.2', 'gpt-5.4', 'gpt-5.4-mini', 'gpt-5.5'
    """
    return (ids.get("cuModelDeployment", "cu-frontier"),
            ids.get("cuModelName", "gpt-4.1"))


def _constant_skill(name: str, context: str, value: str, target: str) -> dict:
    """Emit a literal into the enrichment tree so it can be projected.

    Index projection `mappings` do NOT accept the `='literal'` expression syntax
    that skill `inputs` accept -- attempting it fails the whole indexer run with
    "Parsing failure: unexpected '='". A ConditionalSkill with a constant-true
    condition is the supported way to materialise a constant at a given context.
    """
    return {
        "@odata.type": "#Microsoft.Skills.Util.ConditionalSkill",
        "name": name,
        "context": context,
        "inputs": [
            {"name": "condition", "source": "= true"},
            {"name": "whenTrue", "source": f"= '{value}'"},
            {"name": "whenFalse", "source": "= null"},
        ],
        "outputs": [{"name": "output", "targetName": target}],
    }


def _vision_skill(ids: dict) -> dict:
    """The GA figure-verbalization skill, shared by BOTH ingestion tiers.

    MODEL CHOICE IS A RELIABILITY DECISION, NOT A CAPABILITY ONE.

    The ChatCompletionSkill request timeout is **fixed at 30 seconds** and is no
    longer configurable (`timeout` was removed from the skill schema in Search
    REST API 2026-04-01). Critically, a single figure that exceeds it fails the
    ENTIRE document -- a 906-page specification produced **zero** rows because
    one image timed out.

    A frontier *reasoning* model is therefore the wrong tool here, even though it
    benchmarks well. Measured on a real register-diagram page:

        gpt-5.6-sol, 4000 tok, effort=low   10.7s   785 chars
        gpt-5.6-sol, 4000 tok, default      14.3s  1005 chars
        gpt-4.1-mini, 800 tok               17.2s  1296 chars

    gpt-5.6-sol looks comfortably inside 30s on an average figure — but variance
    on complex figures pushed it past the limit in a real run, and the failure
    mode is total. A **non-reasoning** model has far tighter latency variance,
    which is what actually matters against a hard per-call ceiling.

    So: frontier reasoning models stay on knowledge-base query planning (no
    timeout pressure); this skill runs a non-reasoning vision model.

    Budget still goes through `extraParameters`, never `commonModelParameters` —
    the latter serializes to the legacy `max_tokens`, which current API versions
    reject ("Use 'max_completion_tokens' instead").
    """
    openai_base = ids["foundryOpenAIEndpoint"].rstrip("/")
    vision_deploy = ids.get("visionDeployment", "vision")
    # The api-version query parameter is REQUIRED. Without it Azure OpenAI
    # returns 404, surfaced by the indexer as the generic "Web Api skill
    # response is invalid" -- easy to misread as a bad host or missing
    # deployment. The Microsoft doc sample omits it.
    api_version = ids.get("chatApiVersion", "2025-04-01-preview")
    extra = {"max_completion_tokens": ids.get("visionMaxTokens", 1200)}
    # Only send reasoning_effort when pointed at a reasoning model; non-reasoning
    # deployments reject the parameter.
    effort = ids.get("visionReasoningEffort")
    if effort:
        extra["reasoning_effort"] = effort
    return {
        "@odata.type": "#Microsoft.Skills.Custom.ChatCompletionSkill",
        "name": "image-verbalizer",
        "context": "/document/normalized_images/*",
        "uri": (f"{openai_base}/openai/deployments/{vision_deploy}/chat/completions"
                f"?api-version={api_version}"),
        "extraParameters": extra,
        "inputs": [
            {"name": "image", "source": "/document/normalized_images/*/data"},
            {"name": "imageDetail", "source": "='high'"},
            {"name": "systemMessage",
             "source": "='You describe technical figures from engineering documentation precisely and literally.'"},
            {"name": "userMessage",
             "source": "='Describe this figure for a search index. If it is a register bit-field diagram, "
                       "list every field name in order with its bit width. If it is a table or flowchart, "
                       "state its structure and the values it contains. Do not speculate beyond the image.'"},
        ],
        "outputs": [{"name": "response", "targetName": "imageDescription"}],
    }


def _embedding_skill(ids: dict, context: str, text_source: str) -> dict:
    return {
        "@odata.type": "#Microsoft.Skills.Text.AzureOpenAIEmbeddingSkill",
        "name": f"embed-{context.strip('/').replace('/', '-').replace('*', 'x')}",
        "context": context,
        "resourceUri": ids["foundryOpenAIEndpoint"],
        "deploymentId": ids.get("embeddingDeployment", "embedding"),
        "modelName": ids.get("embeddingModel", "text-embedding-3-large"),
        "dimensions": ids.get("embeddingDimensions", pds.DEFAULT_EMBEDDING_DIMENSIONS),
        "inputs": [{"name": "text", "source": text_source}],
        "outputs": [{"name": "embedding", "targetName": "contentVector"}],
    }


def create_cu_skillset(ids: dict, key: str) -> None:
    """Tier CU: Content Understanding for extraction + semantic chunking, with
    figures handled by the SAME GA vision skill the DI+ tier uses.

    Deliberately does NOT use Content Understanding's own figure-description
    feature (`modelName` / `modelDeployment`). That is a preview capability and
    it failed consistently on a clean 2026-08-21 rebuild with:

        FigureUnderstandingSkipped: Figure understanding was skipped because
        the model deployment returned an error.

    — reproducibly, across both a reasoning model (gpt-5.5) and a non-reasoning
    model (gpt-4.1), with both deployments verified serving 200s on direct
    chat-completions calls. Worse, in an earlier build it degraded to a silent
    warning rather than an error, so the tier appeared to succeed while
    producing zero figure descriptions.

    Using the `ChatCompletionSkill` (GA) for figures on BOTH tiers means: one
    figure mechanism instead of two, a GA dependency instead of a preview one,
    and uniform `contentKind: image-description` rows regardless of which tier
    ingested the document.
    """
    n = h_names(ids)
    vision = _vision_skill(ids)
    body = {
        "name": n["skillsetCu"],
        "description": "Hybrid tier CU: Content Understanding semantic chunking + GA vision skill for figures.",
        "skills": [
            {
                "@odata.type": "#Microsoft.Skills.Util.ContentUnderstandingSkill",
                "name": "content-understanding",
                "context": "/document",
                "chunkingProperties": {"method": "semantic", "unit": "tokens",
                                       "maximumLength": ids.get("cuChunkTokens", 500)},
                "extractionOptions": ["images", "locationMetadata"],
                "inputs": [{"name": "file_data", "source": "/document/file_data"}],
                "outputs": [
                    {"name": "text_sections", "targetName": "text_sections"},
                    {"name": "normalized_images", "targetName": "normalized_images"},
                ],
            },
            _embedding_skill(ids, "/document/text_sections/*", "/document/text_sections/*/content"),
            vision,
            _embedding_skill(ids, "/document/normalized_images/*",
                             "/document/normalized_images/*/imageDescription"),
            _constant_skill("tier-tag", "/document/text_sections/*", TIER_CU, "extractionTier"),
            _constant_skill("kind-tag", "/document/text_sections/*", "text", "contentKind"),
            _constant_skill("tier-tag-img", "/document/normalized_images/*", TIER_CU, "extractionTier"),
            _constant_skill("kind-tag-img", "/document/normalized_images/*", "image-description", "contentKind"),
        ],
        "cognitiveServices": {
            "@odata.type": "#Microsoft.Azure.Search.AIServicesByIdentity",
            "subdomainUrl": pds._ai_services_subdomain_url(ids), "identity": None,
        },
        "indexProjections": {
            "selectors": [
                {
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
                        {"name": "extractionTier", "source": "/document/text_sections/*/extractionTier"},
                        {"name": "contentKind", "source": "/document/text_sections/*/contentKind"},
                    ],
                },
                {
                    "targetIndexName": n["index"],
                    "parentKeyFieldName": "image_parent_id",
                    "sourceContext": "/document/normalized_images/*",
                    "mappings": [
                        {"name": "content", "source": "/document/normalized_images/*/imageDescription"},
                        {"name": "contentVector", "source": "/document/normalized_images/*/contentVector"},
                        {"name": "sourceDocument", "source": "/document/metadata_storage_name"},
                        {"name": "sourceUri", "source": "/document/metadata_storage_path"},
                        {"name": "extractionTier", "source": "/document/normalized_images/*/extractionTier"},
                        {"name": "contentKind", "source": "/document/normalized_images/*/contentKind"},
                    ],
                },
            ],
            "parameters": {"projectionMode": "skipIndexingParentDocuments"},
        },
    }
    r = _req("PUT", f"/skillsets('{n['skillsetCu']}')", ids, key, body)
    pds.raise_with_detail(r)
    print(f"  skillset '{n['skillsetCu']}' (tier CU): {r.status_code}")


def create_di_skillset(ids: dict, key: str) -> None:
    """Tier DI+: Layout + Split + embedding, PLUS a vision skill that verbalizes
    extracted figures so this tier is not blind to diagrams the way plain
    Document Layout is (it emits `<figure></figure>` and nothing else).

    Constraint found live: `extractionOptions: ["locationMetadata"]` is rejected
    with `outputFormat: "markdown"` -- location metadata is text-mode only. So
    this tier gets images but no page numbers, and cites by heading instead.
    """
    n = h_names(ids)
    corpus = pds._corpus(ids)
    chunk = corpus.get("chunkSizeTokens", pds.DEFAULT_CHUNK_SIZE_TOKENS)
    overlap = corpus.get("chunkOverlapTokens", pds.DEFAULT_CHUNK_OVERLAP_TOKENS)
    body = {
        "name": n["skillsetDi"],
        "description": "Hybrid tier DI+: Document Layout (no page ceiling) + image verbalization.",
        "skills": [
            {
                "@odata.type": "#Microsoft.Skills.Util.DocumentIntelligenceLayoutSkill",
                "name": "layout",
                "context": "/document",
                "outputMode": "oneToMany",
                "outputFormat": "markdown",
                # h6 keeps deep numbered sections (5.5.4.1) addressable instead of
                # rolling them up into h3 and losing them as citation metadata.
                "markdownHeaderDepth": "h6",
                "extractionOptions": ["images"],
                "inputs": [{"name": "file_data", "source": "/document/file_data"}],
                "outputs": [
                    {"name": "markdown_document", "targetName": "markdownDocument"},
                    {"name": "normalized_images", "targetName": "normalized_images"},
                ],
            },
            {
                "@odata.type": "#Microsoft.Skills.Text.SplitSkill",
                "name": "split",
                "context": "/document/markdownDocument/*",
                "textSplitMode": "pages",
                "maximumPageLength": chunk,
                "pageOverlapLength": overlap,
                "unit": "azureOpenAITokens",
                "azureOpenAITokenizerParameters": {"encoderModelName": "cl100k_base"},
                "inputs": [{"name": "text", "source": "/document/markdownDocument/*/content"}],
                "outputs": [{"name": "textItems", "targetName": "chunks"}],
            },
            _embedding_skill(ids, "/document/markdownDocument/*/chunks/*",
                             "/document/markdownDocument/*/chunks/*"),
            # Same GA figure-verbalization skill the CU tier uses -- one figure
            # mechanism across both tiers.
            _vision_skill(ids),
            _embedding_skill(ids, "/document/normalized_images/*",
                             "/document/normalized_images/*/imageDescription"),
            _constant_skill("tier-tag-text", "/document/markdownDocument/*/chunks/*", TIER_DI, "extractionTier"),
            _constant_skill("kind-tag-text", "/document/markdownDocument/*/chunks/*", "text", "contentKind"),
            _constant_skill("tier-tag-img", "/document/normalized_images/*", TIER_DI, "extractionTier"),
            _constant_skill("kind-tag-img", "/document/normalized_images/*", "image-description", "contentKind"),
        ],
        "cognitiveServices": {
            "@odata.type": "#Microsoft.Azure.Search.AIServicesByIdentity",
            "subdomainUrl": pds._ai_services_subdomain_url(ids), "identity": None,
        },
        "indexProjections": {
            "selectors": [
                {
                    "targetIndexName": n["index"],
                    "parentKeyFieldName": "parent_id",
                    "sourceContext": "/document/markdownDocument/*/chunks/*",
                    "mappings": [
                        {"name": "content", "source": "/document/markdownDocument/*/chunks/*"},
                        {"name": "contentVector",
                         "source": "/document/markdownDocument/*/chunks/*/contentVector"},
                        {"name": "sourceDocument", "source": "/document/metadata_storage_name"},
                        {"name": "sourceUri", "source": "/document/metadata_storage_path"},
                        # Deepest heading only -- see create_index docstring.
                        {"name": "sectionLabel",
                         "source": "/document/markdownDocument/*/sections/h3"},
                        {"name": "extractionTier",
                         "source": "/document/markdownDocument/*/chunks/*/extractionTier"},
                        {"name": "contentKind",
                         "source": "/document/markdownDocument/*/chunks/*/contentKind"},
                    ],
                },
                {
                    # Image descriptions become their own retrievable rows.
                    "targetIndexName": n["index"],
                    "parentKeyFieldName": "image_parent_id",
                    "sourceContext": "/document/normalized_images/*",
                    "mappings": [
                        {"name": "content", "source": "/document/normalized_images/*/imageDescription"},
                        {"name": "contentVector",
                         "source": "/document/normalized_images/*/contentVector"},
                        {"name": "sourceDocument", "source": "/document/metadata_storage_name"},
                        {"name": "sourceUri", "source": "/document/metadata_storage_path"},
                        {"name": "extractionTier",
                         "source": "/document/normalized_images/*/extractionTier"},
                        {"name": "contentKind",
                         "source": "/document/normalized_images/*/contentKind"},
                    ],
                },
            ],
            "parameters": {"projectionMode": "skipIndexingParentDocuments"},
        },
    }
    r = _req("PUT", f"/skillsets('{n['skillsetDi']}')", ids, key, body)
    pds.raise_with_detail(r)
    print(f"  skillset '{n['skillsetDi']}' (tier DI+): {r.status_code}")


def create_datasources_and_indexers(ids: dict, key: str) -> None:
    """Two data sources scoped to blob folder prefixes -- that is how one
    container feeds two tiers. AI Search indexers cannot filter on page count,
    but the router already sorted the blobs into folders."""
    n = h_names(ids)
    container = ids.get("rawContainer", "raw")
    for prefix, ds_name, skillset, ixr_name in [
        (PREFIX_CU, n["dsCu"], n["skillsetCu"], n["indexerCu"]),
        (PREFIX_DI, n["dsDi"], n["skillsetDi"], n["indexerDi"]),
    ]:
        ds = {
            "name": ds_name,
            "type": "azureblob",
            "credentials": {"connectionString": f"ResourceId={pds._storage_resource_id(ids)};"},
            "container": {"name": container, "query": prefix},
        }
        r = _req("PUT", f"/datasources('{ds_name}')", ids, key, ds)
        pds.raise_with_detail(r)
        print(f"  data source '{ds_name}' (folder '{prefix}/'): {r.status_code}")

        ixr = {
            "name": ixr_name,
            "dataSourceName": ds_name,
            "targetIndexName": n["index"],
            "skillsetName": skillset,
            # Tolerate a few failed documents instead of halting the whole run.
            # Figure verbalization makes hundreds of vision calls, and transient
            # upstream 500s are a normal occurrence at that volume. With the
            # default (maxFailedItems: 0) a single transient failure stops the
            # indexer, so every remaining document goes unprocessed -- observed
            # live: one flaky document blocked four healthy ones for 45 minutes.
            # A small non-zero budget keeps the run going; re-run without
            # --reset afterwards to pick up whatever was skipped. Deliberately
            # NOT -1 (unlimited), which would hide a genuinely broken corpus.
            "parameters": {
                "batchSize": 1,
                "maxFailedItems": ids.get("maxFailedItems", 10),
                "maxFailedItemsPerBatch": ids.get("maxFailedItemsPerBatch", 5),
                "configuration": {
                    "dataToExtract": "contentAndMetadata",
                    "parsingMode": "default",
                    "allowSkillsetToReadFileData": True,
                },
            },
        }
        r = _req("PUT", f"/indexers('{ixr_name}')", ids, key, ixr)
        pds.raise_with_detail(r)
        print(f"  indexer '{ixr_name}': {r.status_code}")


def create_knowledge_base(ids: dict, key: str) -> None:
    """One knowledge base over the unified index -- so one MCP endpoint serves
    the whole corpus and the client never sees the tier split."""
    n = h_names(ids)
    frontier_deploy, frontier_model = _frontier_model(ids)
    src = {
        "name": n["knowledgeSource"], "kind": "searchIndex",
        "description": "Hybrid (Content Understanding + Document Layout) knowledge source.",
        "searchIndexParameters": {
            "searchIndexName": n["index"],
            "semanticConfigurationName": pds.SEMANTIC_CONFIG_NAME,
            "sourceDataFields": [
                {"name": "chunkId"}, {"name": "content"}, {"name": "sourceDocument"},
                {"name": "sourceUri"}, {"name": "sectionLabel"},
                {"name": "pageNumberFrom"}, {"name": "pageNumberTo"},
                {"name": "extractionTier"}, {"name": "contentKind"},
            ],
        },
    }
    r = _req("PUT", f"/knowledgesources/{n['knowledgeSource']}", ids, key, src)
    pds.raise_with_detail(r)
    print(f"  knowledge source '{n['knowledgeSource']}': {r.status_code}")

    kb = {
        "name": n["knowledgeBase"],
        "description": "Agentic retrieval over the hybrid-tier index.",
        "knowledgeSources": [{"name": n["knowledgeSource"]}],
        # Query planning runs on EVERY MCP call and decides what gets retrieved,
        # so it gets the frontier model too -- not a smaller default.
        "models": [{"kind": "azureOpenAI", "azureOpenAIParameters": {
            "resourceUri": ids["foundryOpenAIEndpoint"],
            "deploymentId": frontier_deploy,
            "modelName": frontier_model,
        }}],
        "outputMode": ids.get("knowledgeBaseOutputMode", "extractiveData"),
    }
    r = _req("PUT", f"/knowledgebases/{n['knowledgeBase']}", ids, key, kb)
    pds.raise_with_detail(r)
    print(f"  knowledge base '{n['knowledgeBase']}': {r.status_code}")


def run_indexers(ids: dict, key: str) -> None:
    n = h_names(ids)
    for name in (n["indexerCu"], n["indexerDi"]):
        r = _req("POST", f"/indexers('{name}')/search.run", ids, key)
        # 409 = already running. Indexers auto-run on creation, so a freshly
        # built pipeline conflicts here; that is success, not failure.
        if r.status_code == 409:
            print(f"  already running: {name}")
            continue
        pds.raise_with_detail(r)
        print(f"  run triggered: {name}")


def status(ids: dict, key: str) -> None:
    n = h_names(ids)
    for label, name in [("tier CU ", n["indexerCu"]), ("tier DI+", n["indexerDi"])]:
        r = _req("GET", f"/indexers('{name}')/search.status", ids, key)
        if r.status_code >= 400:
            print(f"{label} [{name}]: unavailable ({r.status_code})")
            continue
        last = r.json().get("lastResult") or {}
        print(f"{label} [{name}]: {last.get('status')} "
              f"processed={last.get('itemsProcessed')} failed={last.get('itemsFailed')}")
        for e in (last.get("errors") or [])[:2]:
            print(f"      ERROR: {(e.get('errorMessage','') + ' ' + e.get('details',''))[:240]}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ids-file", required=True)
    p.add_argument("--source-dir")
    p.add_argument("--split", action="store_true",
                   help="Split PDFs over the 300-page limit into page-ranged parts before "
                        "planning/uploading. Writes to --split-dir. Strongly recommended for "
                        "reference manuals: it makes every part eligible for the higher-quality "
                        "Content Understanding tier AND makes vision failures cheap instead of "
                        "discarding a whole document's enrichment.")
    p.add_argument("--split-dir", default=None,
                   help="Destination for split output (default: <source-dir>/../corpus-split)")
    p.add_argument("--no-split", action="store_true",
                   help="Explicitly keep documents over the 300-page limit whole and route them to Tier DI+. "
                        "Without --split or --no-split, --upload refuses to upload an oversized document.")
    p.add_argument("--plan", action="store_true", help="Show the routing decision (no Azure calls, no cost)")
    p.add_argument("--upload", action="store_true", help="Upload documents into their tier's blob prefix")
    p.add_argument("--build", action="store_true", help="Create the unified index, both tiers, and the knowledge base")
    p.add_argument("--status", action="store_true")
    p.add_argument("--teardown", action="store_true")
    args = p.parse_args()

    ids = pds.load_ids(args.ids_file)
    if args.split and args.no_split:
        p.error("--split and --no-split are mutually exclusive")

    if args.plan or args.upload or args.split:
        if not args.source_dir:
            p.error("--plan/--upload/--split require --source-dir")
        source = args.source_dir
        if args.split:
            split_dir = args.split_dir or str(Path(args.source_dir).parent / "corpus-split")
            print(f"Splitting oversized documents into {split_dir} ...")
            split_oversized(args.source_dir, split_dir)
            source = split_dir
            print()
        rows = plan(source)
        print_plan(rows)
        oversized_guard(rows, args.split, args.no_split, uploading=args.upload)
        if args.upload:
            print()
            upload(ids, rows)
        if not (args.build or args.status or args.teardown):
            return

    key = pds.get_admin_key(ids)
    n = h_names(ids)

    if args.teardown:
        for kind, name in [("knowledgebases", n["knowledgeBase"]), ("knowledgesources", n["knowledgeSource"])]:
            print(f"  deleted {kind}/{name}: {_req('DELETE', f'/{kind}/{name}', ids, key).status_code}")
        for kind, names in [("indexers", (n["indexerCu"], n["indexerDi"])),
                            ("skillsets", (n["skillsetCu"], n["skillsetDi"])),
                            ("datasources", (n["dsCu"], n["dsDi"])),
                            ("indexes", (n["index"],))]:
            for name in names:
                print(f"  deleted {kind}/{name}: {_req('DELETE', f'/{kind}(\'{name}\')', ids, key).status_code}")
        return

    if args.build:
        print("Building hybrid ingestion...")
        create_index(ids, key)
        create_cu_skillset(ids, key)
        create_di_skillset(ids, key)
        create_datasources_and_indexers(ids, key)
        create_knowledge_base(ids, key)
        run_indexers(ids, key)
        print("  build complete -- poll with --status")

    if args.status:
        status(ids, key)


if __name__ == "__main__":
    main()
