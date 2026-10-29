import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from mcp import Client, StdioServerParameters
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError

from app import mcp_server
from app.mcp_server import MCPSettings, create_server

TEST_TOKEN = "offline-test-jwt"


def settings(**kwargs: object) -> MCPSettings:
    return MCPSettings(api_token=TEST_TOKEN, **kwargs)


def test_settings_only_accept_explicit_loopback_port() -> None:
    assert settings().api_base == "http://127.0.0.1:8010"
    assert settings(api_base="http://[::1]:8010/").api_base == "http://[::1]:8010"
    assert TEST_TOKEN not in repr(settings())


@pytest.mark.parametrize(
    "base",
    [
        "https://example.com:8010",
        "http://127.0.0.1:8000",
        "http://localhost:8010",
        "http://user:password@127.0.0.1:8010",
        "http://127.0.0.1:8010/api",
        "http://127.0.0.1:8010?forward=https://example.com",
        "http://127.0.0.1:8010/#x",
        "file:///etc/passwd",
        "http://127.0.0.1:wrong",
        " http://127.0.0.1:8010",
        "http://127.0.\n0.1:8010",
    ],
)
def test_remote_or_ambiguous_base_is_rejected(base: str) -> None:
    with pytest.raises(ValidationError):
        settings(api_base=base)


@pytest.mark.parametrize(
    "token",
    [
        "",
        " ",
        "secret\r\nInjected: header",
        "x" * 8193,
        "non-ascii-秘密",
        "null\x00byte",
    ],
)
def test_invalid_token_is_rejected_without_echoing_input(token: str) -> None:
    with pytest.raises(ValidationError) as error:
        MCPSettings(api_token=token)
    assert "input_value" not in str(error.value)


def test_configuration_does_not_read_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text("GROUNDED_API_TOKEN=must-not-be-read\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GROUNDED_API_TOKEN", raising=False)
    with pytest.raises(ValidationError):
        MCPSettings()


def test_official_protocol_discovery_and_evidence_call() -> None:
    seen: list[httpx.Request] = []
    kb_id = str(uuid4())

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.headers["Authorization"] == f"Bearer {TEST_TOKEN}"
        assert request.url.host == "127.0.0.1"
        assert request.url.port == 8010
        assert request.url.path == "/api/v1/integrations/search-evidence"
        assert json.loads(request.content) == {
            "knowledge_base_ids": [kb_id],
            "query": "budget",
            "k": 3,
        }
        return httpx.Response(
            200,
            json={
                "sources": [
                    {
                        "source_id": "K1",
                        "kind": "knowledge",
                        "content": "Budget is 30.",
                        "title": "Plan A",
                        "url": None,
                    }
                ]
            },
        )

    server = create_server(settings(), transport=httpx.MockTransport(handler))

    async def scenario() -> None:
        async with Client(server, raise_exceptions=True) as client:
            listed = await client.list_tools()
            assert len(listed.tools) == 6
            for tool in listed.tools:
                assert tool.annotations is not None
                assert tool.annotations.read_only_hint
                assert not tool.annotations.destructive_hint
                assert tool.annotations.idempotent_hint
                assert not tool.annotations.open_world_hint
                assert not {
                    "user_id",
                    "url",
                    "api_token",
                    "path",
                    "command",
                }.intersection(tool.input_schema.get("properties", {}))
            result = await client.call_tool(
                "grounded_search_evidence",
                {
                    "knowledge_base_ids": [kb_id, kb_id],
                    "query": " budget ",
                    "k": 3,
                },
            )
            assert not result.is_error
            assert result.structured_content["sources"][0]["source_id"] == "K1"

    asyncio.run(scenario())
    assert len(seen) == 1


@pytest.mark.parametrize(
    ("name", "arguments", "path", "query"),
    [
        (
            "grounded_list_knowledge_bases",
            {"skip": 2, "limit": 3},
            "/api/v1/knowledge-bases/",
            "skip=2&limit=3",
        ),
        (
            "grounded_list_conversations",
            {},
            "/api/v1/assistant/conversations",
            "skip=0&limit=20",
        ),
        (
            "grounded_get_conversation",
            {"conversation_id": "00000000-0000-0000-0000-000000000001"},
            "/api/v1/assistant/conversations/00000000-0000-0000-0000-000000000001",
            "",
        ),
        (
            "grounded_list_messages",
            {"conversation_id": "00000000-0000-0000-0000-000000000001"},
            "/api/v1/assistant/conversations/00000000-0000-0000-0000-000000000001/messages",
            "",
        ),
        (
            "grounded_get_research_run",
            {"run_id": "00000000-0000-0000-0000-000000000002"},
            "/api/v1/research/runs/00000000-0000-0000-0000-000000000002",
            "",
        ),
    ],
)
def test_read_tools_use_fixed_authorized_routes(
    name: str, arguments: dict, path: str, query: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == path
        assert request.url.query.decode() == query
        assert request.headers["Authorization"] == f"Bearer {TEST_TOKEN}"
        return httpx.Response(200, json={"data": [], "count": 0})

    server = create_server(settings(), transport=httpx.MockTransport(handler))
    result = asyncio.run(server.call_tool(name, arguments))
    assert result.structured_content == {"data": [], "count": 0}


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("grounded_list_knowledge_bases", {"limit": 101}),
        ("grounded_list_conversations", {"skip": -1}),
        ("grounded_get_conversation", {"conversation_id": "../../etc/passwd"}),
        ("grounded_get_research_run", {"run_id": "https://example.com"}),
        ("grounded_search_evidence", {"knowledge_base_ids": [], "query": "q"}),
        (
            "grounded_search_evidence",
            {"knowledge_base_ids": [str(uuid4())], "query": " "},
        ),
        (
            "grounded_search_evidence",
            {"knowledge_base_ids": [str(uuid4())], "query": "q", "k": 21},
        ),
    ],
)
def test_invalid_arguments_never_reach_backend(name: str, arguments: dict) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={})

    server = create_server(settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(ToolError):
        asyncio.run(server.call_tool(name, arguments))
    assert not calls


@pytest.mark.parametrize("status", [302, 401, 403, 404, 429, 500])
def test_upstream_errors_are_safe_and_redirects_are_not_followed(status: int) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            status,
            headers={"Location": "https://example.com/leak"},
            text=f"private server detail {TEST_TOKEN}",
        )

    server = create_server(settings(), transport=httpx.MockTransport(handler))

    async def scenario() -> None:
        async with Client(server, raise_exceptions=True) as client:
            result = await client.call_tool("grounded_list_conversations", {})
            assert result.is_error
            serialized = result.model_dump_json()
            assert TEST_TOKEN not in serialized
            assert "private server detail" not in serialized
            assert "example.com" not in serialized

    asyncio.run(scenario())
    assert len(calls) == 1


@pytest.mark.parametrize("content", [b"not-json", b"[]", b"x" * (2 * 1024 * 1024 + 1)])
def test_invalid_or_oversized_response_is_a_safe_tool_failure(content: bytes) -> None:
    server = create_server(
        settings(),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=content)
        ),
    )
    with pytest.raises(ToolError):
        asyncio.run(server.call_tool("grounded_list_conversations", {}))


@pytest.mark.parametrize("error_type", [httpx.ConnectError, httpx.ReadTimeout])
def test_network_failures_do_not_leak_transport_details(error_type: type) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error_type(f"private detail {TEST_TOKEN}", request=request)

    server = create_server(settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(ToolError) as error:
        asyncio.run(server.call_tool("grounded_list_conversations", {}))
    assert TEST_TOKEN not in str(error.value)
    assert "private detail" not in str(error.value)


def test_whole_request_deadline_bounds_a_slow_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.1)
        return httpx.Response(200, json={})

    monkeypatch.setattr(mcp_server, "_REQUEST_TIMEOUT_SECONDS", 0.005)
    server = create_server(settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(ToolError, match="timed out"):
        asyncio.run(server.call_tool("grounded_list_conversations", {}))


def test_stdio_entrypoint_negotiates_with_official_client() -> None:
    backend = Path(__file__).resolve().parents[2]

    async def scenario() -> None:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "app.mcp_server"],
            cwd=backend,
            env={
                "GROUNDED_API_TOKEN": TEST_TOKEN,
                "GROUNDED_API_BASE": "http://127.0.0.1:8010",
            },
        )
        async with Client(params, read_timeout_seconds=10) as client:
            listed = await client.list_tools()
            assert "grounded_search_evidence" in {tool.name for tool in listed.tools}

    # Discovery only: no database, backend, model provider or external network is used.
    asyncio.run(scenario())
