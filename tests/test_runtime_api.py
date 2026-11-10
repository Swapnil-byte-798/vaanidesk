"""Configuration and API boundary tests; providers use an explicit HTTP fixture."""

import importlib
import json
from pathlib import Path
from unittest.mock import patch
import httpx
import pytest
from fastapi.testclient import TestClient
from vaanidesk import runtime


def test_settings_precedence_and_secret_allowlist(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    shared = tmp_path / "providers.env"
    shared.write_text("GROQ_API_KEY=shared\nUNRECOGNIZED=ignored\n")
    monkeypatch.setenv("PORTFOLIO_SECRETS_FILE", str(shared))
    Path(".env").write_text("GROQ_API_KEY=local\n")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert runtime.settings()["GROQ_API_KEY"] == "local"
    monkeypatch.setenv("GROQ_API_KEY", "environment")
    assert runtime.settings()["GROQ_API_KEY"] == "environment"
    assert "UNRECOGNIZED" not in runtime.settings()


def test_hidden_setup_preserves_shared_credentials(tmp_path, monkeypatch):
    shared = tmp_path / "providers.env"
    shared.write_text("GROQ_API_KEY=existing\n")
    monkeypatch.setenv("PORTFOLIO_SECRETS_FILE", str(shared))
    with patch.object(runtime, "getpass", return_value="new-speech-key") as prompt:
        result = runtime.setup(("GROQ_API_KEY", "SARVAM_API_KEY"), shared=True)
    assert prompt.call_count == 1
    assert "GROQ_API_KEY=existing" in shared.read_text()
    assert "SARVAM_API_KEY=new-speech-key" in shared.read_text()
    assert "existing" not in json.dumps(result)
    assert shared.stat().st_mode & 0o777 == 0o600


def test_local_setup_refuses_to_replace_credentials(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path(".env").write_text("API_TOKEN=existing\n")
    with pytest.raises(runtime.Fault) as error:
        runtime.setup(())
    assert error.value.status == 409
    assert Path(".env").read_text() == "API_TOKEN=existing\n"


def test_provider_contract_and_usage():
    def respond(request):
        assert request.headers["authorization"] == "Bearer fixture-key"
        body = json.loads(request.content)
        assert body["response_format"] == {"type": "json_object"}
        assert body["temperature"] == 0
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"answer":"fixture"}'}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 4},
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(respond))
    with patch.object(
        runtime, "settings", return_value={"GROQ_API_KEY": "fixture-key"}
    ):
        llm = runtime.LLM(client=client)
        assert llm.ask("Instruction", {"input": "Example"}) == {"answer": "fixture"}
        assert llm.last_usage["input_tokens"] == 12


@pytest.mark.parametrize(
    "payload", [{}, {"choices": []}, {"choices": [{"message": {"content": "[]"}}]}]
)
def test_bad_provider_response_never_becomes_fake_success(payload):
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    )
    with patch.object(
        runtime, "settings", return_value={"GROQ_API_KEY": "fixture-secret"}
    ):
        with pytest.raises(runtime.Fault) as error:
            runtime.LLM(client=client).ask("Test", {})
    assert error.value.status == 502
    assert "fixture-secret" not in str(error.value)


def test_missing_key_does_not_send_a_request():
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: pytest.fail("unexpected network call")
        )
    )
    with patch.object(runtime, "settings", return_value={}):
        with pytest.raises(runtime.Fault) as error:
            runtime.LLM(client=client).ask("Test", {})
    assert error.value.status == 503


def test_api_schema_and_operator_boundary(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PORTFOLIO_SECRETS_FILE", str(tmp_path / "missing"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("API_TOKEN", "operator-fixture-token")
    monkeypatch.delenv("TENANTDESK_DATABASE_URL", raising=False)
    api = importlib.import_module("vaanidesk.api")
    client = TestClient(api.app)
    assert client.get("/health").status_code == 200
    schema = client.get("/openapi.json")
    assert schema.status_code == 200 and schema.json()["paths"]
    method, path, body = ("POST", "/calls", None)
    options = {} if body is None else {"json": body}
    denied = client.request(method, path, **options)
    assert denied.status_code == 401
    admitted = client.request(
        method,
        path,
        headers={"Authorization": "Bearer operator-fixture-token"},
        **options,
    )
    assert admitted.status_code not in (401, 403, 500)


def test_database_rollback_is_atomic(tmp_path):
    store = runtime.Store(
        tmp_path / "transaction.db", "CREATE TABLE values_table(value TEXT PRIMARY KEY)"
    )
    with pytest.raises(RuntimeError):
        with store.transaction() as connection:
            connection.execute("INSERT INTO values_table VALUES(?)", ("one",))
            raise RuntimeError("simulated failure")
    with store.transaction() as connection:
        assert connection.execute("SELECT * FROM values_table").fetchall() == []


def test_blank_local_key_does_not_hide_shared_provider_key(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    shared = tmp_path / "shared.env"
    shared.write_text("GROQ_API_KEY=shared-key\n")
    Path(".env").write_text("GROQ_API_KEY=\n")
    monkeypatch.setenv("PORTFOLIO_SECRETS_FILE", str(shared))
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert runtime.settings()["GROQ_API_KEY"] == "shared-key"
