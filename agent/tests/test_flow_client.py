"""Unit tests for FlowClient correlation + callback resolution.

We don't exercise a real WebSocket in these tests — we inject a FakeWs that
records outgoing JSON so we can verify the protocol shape, and we resolve
futures via ``flow_client.resolve_callback`` (same code path the HTTP
callback handler uses).
"""
import asyncio
import json

import pytest

from flowboard.services.flow_client import FlowClient


class FakeWs:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))


@pytest.mark.asyncio
async def test_api_request_round_trip_via_callback():
    client = FlowClient()
    ws = FakeWs()
    client.set_extension(ws)

    async def later_resolve() -> None:
        # give _send a chance to register the future
        await asyncio.sleep(0)
        assert len(client._pending) == 1
        req_id = next(iter(client._pending))
        client.resolve_callback({"id": req_id, "status": 200, "data": {"ok": True}})

    asyncio.create_task(later_resolve())

    result = await client.api_request(
        url="https://aisandbox-pa.googleapis.com/v1/ping",
        method="GET",
    )

    assert result == {"id": result["id"], "status": 200, "data": {"ok": True}}
    assert ws.sent[0]["method"] == "api_request"
    assert ws.sent[0]["params"]["url"].startswith("https://aisandbox-pa.googleapis.com/")
    assert client._pending == {}
    assert client.ws_stats["request_count"] == 1
    assert client.ws_stats["success_count"] == 1


@pytest.mark.asyncio
async def test_api_request_without_extension_returns_disconnected():
    client = FlowClient()
    result = await client.api_request(url="https://aisandbox-pa.googleapis.com/v1/ping")
    assert result == {"error": "extension_disconnected"}


@pytest.mark.asyncio
async def test_clear_extension_fails_pending_futures():
    client = FlowClient()
    ws = FakeWs()
    client.set_extension(ws)

    async def run_call() -> dict:
        return await client.api_request(
            url="https://aisandbox-pa.googleapis.com/v1/ping"
        )

    task = asyncio.create_task(run_call())
    await asyncio.sleep(0)  # let _send register future
    assert len(client._pending) == 1

    client.clear_extension()
    result = await task
    assert result == {"error": "extension_disconnected"}


def test_stale_extension_disconnect_does_not_clear_current_socket():
    client = FlowClient()
    old_ws = FakeWs()
    new_ws = FakeWs()
    client.set_extension(old_ws)
    client.set_extension(new_ws)

    client.clear_extension(old_ws)

    assert client.connected is True
    assert client._ws is new_ws


@pytest.mark.asyncio
async def test_token_socket_stays_active_when_other_socket_reports_no_token():
    client = FlowClient()
    token_ws = FakeWs()
    empty_ws = FakeWs()
    client.set_extension(token_ws)
    client.set_extension(empty_ws)

    await client.handle_message(
        {"type": "token_captured", "flowKey": "ya29.token"},
        token_ws,
    )
    await client.handle_message({"type": "extension_ready", "flowKeyPresent": False}, empty_ws)

    assert client.connected is True
    assert client._ws is token_ws
    assert client.ws_stats["flow_key_present"] is True


@pytest.mark.asyncio
async def test_extension_ready_with_token_promotes_socket():
    client = FlowClient()
    empty_ws = FakeWs()
    token_ws = FakeWs()
    client.set_extension(empty_ws)
    client.set_extension(token_ws)

    await client.handle_message({"type": "extension_ready", "flowKeyPresent": True}, token_ws)

    assert client._ws is token_ws
    assert client.ws_stats["flow_key_present"] is True


@pytest.mark.asyncio
async def test_handle_token_captured_updates_stats():
    client = FlowClient()
    await client.handle_message({"type": "token_captured", "flowKey": "ya29.xxx"})
    stats = client.ws_stats
    assert stats["flow_key_present"] is True
    assert stats["token_age_s"] is not None
    assert stats["token_age_s"] >= 0


def test_callback_secret_is_unique_per_instance():
    a = FlowClient()
    b = FlowClient()
    assert a.callback_secret != b.callback_secret
    assert len(a.callback_secret) >= 32


@pytest.mark.asyncio
async def test_api_request_passes_captcha_action_through():
    client = FlowClient()
    ws = FakeWs()
    client.set_extension(ws)

    async def resolve_soon() -> None:
        await asyncio.sleep(0)
        req_id = next(iter(client._pending))
        client.resolve_callback({"id": req_id, "status": 200, "data": {}})

    asyncio.create_task(resolve_soon())
    await client.api_request(
        url="https://aisandbox-pa.googleapis.com/v1/video:batchGenerateImages",
        captcha_action="IMAGE_GENERATION",
    )
    assert ws.sent[0]["method"] == "api_request"
    assert ws.sent[0]["params"]["captchaAction"] == "IMAGE_GENERATION"


@pytest.mark.asyncio
async def test_trpc_request_uses_method_trpc_and_correlates():
    client = FlowClient()
    ws = FakeWs()
    client.set_extension(ws)

    async def resolve_soon() -> None:
        await asyncio.sleep(0)
        req_id = next(iter(client._pending))
        client.resolve_callback({"id": req_id, "status": 200, "data": {"ok": True}})

    asyncio.create_task(resolve_soon())
    result = await client.trpc_request(
        url="https://labs.google/fx/api/trpc/project.createProject",
        body={"json": {"projectTitle": "t", "toolName": "PINHOLE"}},
    )
    assert ws.sent[0]["method"] == "trpc_request"
    assert ws.sent[0]["params"]["url"].startswith("https://labs.google/")
    assert result["status"] == 200


@pytest.mark.asyncio
async def test_api_request_4xx_counts_as_failed():
    client = FlowClient()
    ws = FakeWs()
    client.set_extension(ws)

    async def resolve_soon() -> None:
        await asyncio.sleep(0)
        req_id = next(iter(client._pending))
        client.resolve_callback({"id": req_id, "status": 403, "data": {"e": "CAPTCHA_FAILED"}})

    asyncio.create_task(resolve_soon())
    await client.api_request(url="https://aisandbox-pa.googleapis.com/v1/ping")
    stats = client.ws_stats
    assert stats["failed_count"] == 1
    assert stats["success_count"] == 0
    assert stats["last_error"] == "API_403"
