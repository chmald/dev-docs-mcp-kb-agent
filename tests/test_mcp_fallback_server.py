"""Smoke tests for scripts/mcp_fallback_server.py -- confirm the module
imports cleanly (with required env vars stubbed) and its retrieval helpers
return a correctly-shaped result against a mocked AI Search response.

NOTE: these tests target the module's internal retrieval helpers directly
rather than invoking the @mcp.tool()-decorated function through the MCP
protocol layer -- that round-trip is exercised by the functional test in
04-testing.md § A instead."""
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# mcp_fallback_server.py reads its Search connection settings from environment
# variables at import time -- stub them before importing so this test doesn't
# need a live Search service.
os.environ.setdefault("SEARCH_ENDPOINT", "https://srch-ddmcp-dev-eastus2.search.windows.net")
os.environ.setdefault("SEARCH_API_KEY", "fake-key")
os.environ.setdefault("SEARCH_INDEX_NAME", "idx-documents")
os.environ.setdefault("KNOWLEDGE_BASE_NAME", "kb-documents")

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import mcp_fallback_server as server  # noqa: E402


def test_module_imports():
    assert server is not None
    assert server.SEARCH_INDEX_NAME == "idx-documents"


def test_tool_is_defined():
    assert server.retrieve_documents is not None


def test_tool_identity_is_configurable():
    """GitHub Copilot decides whether to call an MCP tool from its name and
    description, so both must be settable per corpus without a code edit."""
    assert server.MCP_TOOL_NAME
    assert server.MCP_TOOL_DESCRIPTION
    # Defaults must stay corpus-neutral; a real corpus overrides them via env.
    for domain_term in ("datasheet", "firmware", "hardware"):
        assert domain_term not in server.MCP_TOOL_DESCRIPTION.lower()


def test_tool_registered_under_configured_name():
    """The registered tool name should follow MCP_TOOL_NAME, not the Python
    function name -- that's what makes the server reusable across corpora."""
    import asyncio

    tools = asyncio.run(server.mcp.list_tools())
    assert [t.name for t in tools] == [server.MCP_TOOL_NAME]


def test_semantic_config_matches_index_builder_constant():
    """mcp_fallback_server queries with a semantic configuration name that
    post_deploy_search creates -- a mismatch silently breaks direct search."""
    sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
    import post_deploy_search as pds

    assert server.SEMANTIC_CONFIG_NAME == pds.SEMANTIC_CONFIG_NAME


@pytest.mark.asyncio
async def test_retrieve_via_knowledge_base_returns_none_on_http_error():
    with patch("mcp_fallback_server.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.side_effect = server.httpx.HTTPError("boom")
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        result = await server._retrieve_via_knowledge_base("test question")
        assert result is None


@pytest.mark.asyncio
async def test_retrieve_via_direct_search_formats_citations():
    fake_response = MagicMock()
    fake_response.raise_for_status = lambda: None
    fake_response.json.return_value = {
        "value": [
            {
                "content": "Timer1 resets to 0x00.",
                "sourceDocument": "manual.pdf",
                "sectionH1": "Peripherals",
                "sectionH2": "Timer Registers",
            }
        ]
    }

    with patch("mcp_fallback_server.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = fake_response
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        result = await server._retrieve_via_direct_search("what is the reset value of Timer1?", top_k=5)

    assert "manual.pdf" in result
    # Heading path, not page number -- the Layout skill's markdown mode emits
    # section headings; page numbers require text mode + locationMetadata.
    assert "Peripherals > Timer Registers" in result
    assert "Timer1 resets to 0x00." in result


def test_heading_formatter_handles_partial_and_empty_headings():
    assert server._format_heading({"sectionH1": "A", "sectionH2": "B", "sectionH3": "C"}) == "A > B > C"
    assert server._format_heading({"sectionH1": "A", "sectionH3": "C"}) == "A > C"
    assert server._format_heading({}) == "untitled section"


@pytest.mark.asyncio
async def test_retrieve_via_direct_search_handles_empty_results():
    fake_response = MagicMock()
    fake_response.raise_for_status = lambda: None
    fake_response.json.return_value = {"value": []}

    with patch("mcp_fallback_server.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = fake_response
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        result = await server._retrieve_via_direct_search("a question the corpus can't answer", top_k=5)

    assert "No matching content" in result
