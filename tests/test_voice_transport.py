"""Real loopback WebSocket transport, plus voice protocol rejection boundaries."""

import json
import socket
import threading
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
import uvicorn
from websockets.sync.client import connect

from vaanidesk.api import app


def test_installed_voice_transport_accepts_interrupt_without_provider_calls(
    monkeypatch,
):
    monkeypatch.setenv("API_TOKEN", "voice-transport-fixture")
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, log_level="error", ws="websockets-sansio")
    )
    worker = threading.Thread(
        target=server.run, kwargs={"sockets": [listener]}, daemon=True
    )
    worker.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and worker.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started, "Loopback server did not start"
        with connect(
            f"ws://127.0.0.1:{port}/calls/transport-check/voice",
            additional_headers={"Authorization": "Bearer voice-transport-fixture"},
            proxy=None,
            open_timeout=5,
        ) as connection:
            connection.send(json.dumps({"type": "interrupt"}))
            assert json.loads(connection.recv(timeout=5)) == {"type": "interrupted"}
    finally:
        server.should_exit = True
        worker.join(timeout=5)
        listener.close()
    assert not worker.is_alive()


def test_voice_rejects_missing_operator_token(monkeypatch):
    monkeypatch.setenv("API_TOKEN", "voice-transport-fixture")
    with pytest.raises(WebSocketDisconnect) as error:
        with TestClient(app).websocket_connect("/calls/unauthorized/voice"):
            pytest.fail("Unauthenticated voice connection was accepted")
    assert error.value.code == 1008


@pytest.mark.parametrize("payload", ["[]", "{not-json}"])
def test_voice_rejects_malformed_frames_cleanly(monkeypatch, payload):
    monkeypatch.setenv("API_TOKEN", "voice-transport-fixture")
    with TestClient(app).websocket_connect(
        "/calls/malformed/voice",
        headers={"Authorization": "Bearer voice-transport-fixture"},
    ) as connection:
        connection.send_text(payload)
        assert connection.receive_json()["type"] == "error"
        with pytest.raises(WebSocketDisconnect) as error:
            connection.receive_json()
        assert error.value.code == 1008
