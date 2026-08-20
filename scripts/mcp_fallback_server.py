"""
mcp_fallback_server.py

A thin MCP server exposing one tool, `retrieve_technical_docs`, that grounds
answers in the AI Search index built by post_deploy_search.py. This is an
OPTIONAL wrapper around the native AI Search Knowledge Base -> MCP endpoint,
not a required fallback -- it adds defense-in-depth (token lifecycle,
custom pre/post-processing, network controls via Container Apps) on top
of a capability that is already real and documented (see
docs/06-mcp-endpoint-and-fallback-server.md).

Retrieval strategy (each step only runs if the prior one isn't available):
    1. AI Search Knowledge Base `retrieve` operation (agentic, multi-query)
    2. Direct hybrid + semantic query against the index (no Knowledge Base)

Environment variables (set by Container Apps secrets/env, or a local .env for
dev -- never hardcode a key in this file):
    SEARCH_ENDPOINT           e.g. https://srch-ddmcp-dev-eastus2.search.windows.net
    SEARCH_API_KEY            Search admin or query key
    SEARCH_INDEX_NAME         default: idx-documents
    KNOWLEDGE_BASE_NAME       default: kb-documents (empty string disables the Knowledge Base path)
    SEARCH_API_VERSION        default: 2026-05-01-preview
    SEMANTIC_CONFIG_NAME      default: semantic-config (must match post_deploy_search.py)
    MCP_TOOL_NAME             default: retrieve_documents -- set per corpus
    MCP_TOOL_DESCRIPTION      set per corpus; drives whether Copilot invokes the tool
    MCP_SERVER_NAME           default: knowledge-base-wrapper

Usage:
    python mcp_fallback_server.py --transport stdio
    python mcp_fallback_server.py --transport streamable-http --port 8080
"""
import argparse
import os

import httpx
from mcp.server.fastmcp import FastMCP

SEARCH_API_VERSION = os.environ.get("SEARCH_API_VERSION", "2026-05-01-preview")
SEARCH_ENDPOINT = os.environ["SEARCH_ENDPOINT"]
SEARCH_API_KEY = os.environ["SEARCH_API_KEY"]
SEARCH_INDEX_NAME = os.environ.get("SEARCH_INDEX_NAME", "idx-documents")
KNOWLEDGE_BASE_NAME = os.environ.get("KNOWLEDGE_BASE_NAME", "kb-documents")
# Agentic retrieval reaches the index through a knowledge source; the retrieve
# call must name it to get citation source data back.
KNOWLEDGE_SOURCE_NAME = os.environ.get("KNOWLEDGE_SOURCE_NAME", "ks-documents")

# Must match SEMANTIC_CONFIG_NAME in post_deploy_search.py. It's an
# index-scoped name, so the generic constant works for every corpus.
SEMANTIC_CONFIG_NAME = os.environ.get("SEMANTIC_CONFIG_NAME", "semantic-config")

# --- Corpus-specific tool identity (the one thing you SHOULD change) -----
# GitHub Copilot decides whether to invoke an MCP tool primarily from its
# NAME and DESCRIPTION. A generic "search documents" description gets ignored
# for domain questions, so set these per corpus (via the `corpus` block in
# demo-ids.local.json, surfaced here as env vars by deploy_mcp_server.ps1).
MCP_TOOL_NAME = os.environ.get("MCP_TOOL_NAME", "retrieve_documents")
MCP_TOOL_DESCRIPTION = os.environ.get(
    "MCP_TOOL_DESCRIPTION",
    "Search the indexed document corpus and return grounded, cited passages "
    "for the given question. Each result cites its source document, page "
    "number, and section heading.",
)
MCP_SERVER_NAME = os.environ.get("MCP_SERVER_NAME", "knowledge-base-wrapper")

mcp = FastMCP(MCP_SERVER_NAME)


def _headers() -> dict:
    return {"api-key": SEARCH_API_KEY, "Content-Type": "application/json"}


def _format_heading(source: dict) -> str:
    """Heading path (h1 > h2 > h3) from the Layout skill's markdown sections.
    Markdown mode emits headings, not page numbers -- see create_skillset in
    post_deploy_search.py for the page-number alternative."""
    parts = [source.get(f) for f in ("sectionH1", "sectionH2", "sectionH3")]
    trail = " > ".join(p for p in parts if p)
    return trail or "untitled section"


async def _retrieve_via_knowledge_base(query: str) -> str | None:
    """Tries the Knowledge Base's direct (non-MCP) retrieve operation, which
    is a more stable surface than the native /mcp endpoint. Returns None if no
    Knowledge Base is configured or the call fails, so the caller can fall
    back further."""
    if not KNOWLEDGE_BASE_NAME:
        return None
    url = f"{SEARCH_ENDPOINT}/knowledgebases/{KNOWLEDGE_BASE_NAME}/retrieve?api-version={SEARCH_API_VERSION}"
    body = {
        "messages": [{"role": "user", "content": [{"type": "text", "text": query}]}],
        # Without includeReferenceSourceData the service returns
        # references[].sourceData = null and every citation below is empty.
        "knowledgeSourceParams": [
            {
                "kind": "searchIndex",
                "knowledgeSourceName": KNOWLEDGE_SOURCE_NAME,
                "includeReferences": True,
                "includeReferenceSourceData": True,
            }
        ],
    }
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.post(url, headers=_headers(), json=body)
            if resp.status_code != 200:
                return None
            data = resp.json()
        except httpx.HTTPError:
            return None

    response_items = data.get("response") or [{}]
    content_items = response_items[0].get("content") or [{}]
    answer = content_items[0].get("text", "") if content_items else ""
    references = data.get("references") or []
    citations = "\n".join(
        f"- {(ref.get('sourceData') or {}).get('sourceDocument', 'unknown')} "
        f"({_format_heading(ref.get('sourceData') or {})})"
        for ref in references
    )
    return f"{answer}\n\nSources:\n{citations}" if answer else None


async def _retrieve_via_direct_search(query: str, top_k: int) -> str:
    """Fallback of the fallback: a direct hybrid + semantic search against the
    index, used when no Knowledge Base is provisioned at all."""
    url = f"{SEARCH_ENDPOINT}/indexes('{SEARCH_INDEX_NAME}')/docs/search?api-version={SEARCH_API_VERSION}"
    body = {
        "search": query,
        "queryType": "semantic",
        "semanticConfiguration": SEMANTIC_CONFIG_NAME,
        "top": top_k,
        "select": "content,sourceDocument,sourceUri,sectionH1,sectionH2,sectionH3",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, headers=_headers(), json=body)
        resp.raise_for_status()
        results = resp.json().get("value", [])

    if not results:
        return "No matching content found in the indexed document corpus."

    parts = []
    for doc in results:
        parts.append(
            f"[{doc.get('sourceDocument')} — {_format_heading(doc)}]\n"
            f"{doc.get('content')}"
        )
    return "\n\n---\n\n".join(parts)


async def retrieve_documents(query: str, top_k: int = 5) -> str:
    """Search the indexed document corpus and return grounded, cited results.

    Registered with a configurable name/description (see MCP_TOOL_NAME /
    MCP_TOOL_DESCRIPTION above) so the same server serves any corpus without
    a code edit. Tries agentic retrieval via the Knowledge Base first, falling
    back to a direct hybrid+semantic search if no Knowledge Base is configured
    or reachable."""
    agent_result = await _retrieve_via_knowledge_base(query)
    if agent_result:
        return agent_result
    return await _retrieve_via_direct_search(query, top_k)


# Registered explicitly (rather than via the @mcp.tool() decorator) so the
# tool's advertised name and description come from configuration.
mcp.add_tool(retrieve_documents, name=MCP_TOOL_NAME, description=MCP_TOOL_DESCRIPTION)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    if args.transport == "streamable-http":
        mcp.settings.port = args.port
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
