"""Shared transport conventions, vendored into each independently runnable project."""

from contextlib import contextmanager
from getpass import getpass
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import tempfile
import time
from typing import Any

import httpx

KEY_NAMES = (
    "GROQ_API_KEY",
    "SARVAM_API_KEY",
    "OPENAI_API_KEY",
    "STRIPE_WEBHOOK_SECRET",
)
SETTING_NAMES = KEY_NAMES + (
    "API_TOKEN",
    "REVIEWER_TOKEN",
    "GROQ_MODEL",
    "DATA_DIR",
    "WHISPER_MODEL_PATH",
    "TENANTDESK_DATABASE_URL",
    "TENANTDESK_ADMIN_DATABASE_URL",
)


class Fault(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def settings() -> dict[str, str]:
    values: dict[str, str] = {}
    shared = Path(
        os.environ.get(
            "PORTFOLIO_SECRETS_FILE", Path.home() / ".config/ai-portfolio/providers.env"
        )
    )
    for path in [shared, Path(".env")]:
        if path.is_file():
            for line in path.read_text().splitlines():
                key, separator, value = line.partition("=")
                if separator and key.strip() in SETTING_NAMES:
                    if key.strip() in KEY_NAMES and not value.strip():
                        continue
                    values[key.strip()] = value.strip()
    for name in SETTING_NAMES:
        if name in os.environ:
            values[name] = os.environ[name].strip()
    return values


def private_write(path: Path, value: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".secret-")
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(value)
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        Path(temporary).unlink(missing_ok=True)


def setup(keys: tuple[str, ...], shared: bool = False) -> dict:
    target = (
        Path(
            os.environ.get(
                "PORTFOLIO_SECRETS_FILE",
                Path.home() / ".config/ai-portfolio/providers.env",
            )
        )
        if shared
        else Path(".env")
    )
    if target.exists() and not shared:
        raise Fault(
            f"{target.name} already exists; edit its values deliberately rather than overwriting credentials.",
            409,
        )
    values = {}
    if shared and target.is_file():
        for line in target.read_text().splitlines():
            key, separator, value = line.partition("=")
            if separator and key in KEY_NAMES:
                values[key] = value.strip()
    for name in keys:
        if not values.get(name):
            values[name] = getpass(
                f"{name} (hidden; Enter to leave unconfigured): "
            ).strip()
    if any("\n" in value or "\r" in value for value in values.values()):
        raise Fault("A credential cannot contain a newline.")
    if not shared:
        values.update(
            API_TOKEN=secrets.token_urlsafe(32),
            REVIEWER_TOKEN=secrets.token_urlsafe(32),
        )
    private_write(
        target, "".join(f"{name}={value}\n" for name, value in values.items())
    )
    return {
        "saved": True,
        "shared_provider_file": shared,
        "configured": [name for name in keys if values[name]],
    }


def require(value: Any, label: str, limit: int = 4000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise Fault(f"{label} must contain 1-{limit} characters.")
    return value.strip()


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


class Store:
    def __init__(self, path: str | Path, schema: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as connection:
            for statement in schema.split(";"):
                if statement.strip():
                    connection.execute(statement)

    @contextmanager
    def transaction(self):
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=15000")
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()


class LLM:
    """No implicit retries, fixture fallback, or credential echoing."""

    def __init__(self, provider: str = "groq", client: httpx.Client | None = None):
        if provider not in {"groq", "openai"}:
            raise Fault("Unknown provider.")
        self.provider = provider
        self.client = client
        self.last_usage: dict = {}

    def ask(self, instruction: str, payload: Any, model: str | None = None) -> dict:
        config = settings()
        key = config.get(
            "GROQ_API_KEY" if self.provider == "groq" else "OPENAI_API_KEY", ""
        )
        if not key:
            raise Fault(
                f"Configure {self.provider.upper()}_API_KEY through the hidden setup prompt first.",
                503,
            )
        if self.client is None:
            self.client = httpx.Client(timeout=httpx.Timeout(30, connect=10))
        base = (
            "https://api.groq.com/openai/v1"
            if self.provider == "groq"
            else "https://api.openai.com/v1"
        )
        model = model or (
            config.get("GROQ_MODEL") or "openai/gpt-oss-20b"
            if self.provider == "groq"
            else "gpt-4.1-mini"
        )
        body = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": instruction + " Return only a JSON object.",
                },
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
            "max_completion_tokens": 1000,
        }
        started = time.perf_counter()
        try:
            response = self.client.post(
                base + "/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json=body,
            )
            if response.status_code != 200:
                raise Fault(
                    f"{self.provider} request failed (HTTP {response.status_code}).",
                    502,
                )
            data = response.json()
            value = json.loads(data["choices"][0]["message"]["content"])
            if not isinstance(value, dict):
                raise ValueError("Expected an object")
            usage = data.get("usage", {})
            self.last_usage = {
                "provider": self.provider,
                "model": model,
                "input_tokens": usage.get("prompt_tokens"),
                "output_tokens": usage.get("completion_tokens"),
                "latency_ms": round((time.perf_counter() - started) * 1000),
            }
            return value
        except Fault:
            raise
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
            raise Fault(
                f"{self.provider} returned an unavailable or invalid response; no result was fabricated.",
                502,
            ) from None


def make_app(name: str):
    from fastapi import FastAPI, Header, Request
    from fastapi.responses import JSONResponse

    app = FastAPI(title=name, version="0.1.0")

    def auth(authorization: str = Header(default="")):
        token = settings().get("API_TOKEN", "")
        if not token:
            raise Fault("Run setup to configure the operator token.", 503)
        if not secrets.compare_digest(authorization, "Bearer " + token):
            raise Fault("A valid operator bearer token is required.", 401)
        return "operator"

    def reviewer(authorization: str = Header(default="")):
        token = settings().get("REVIEWER_TOKEN", "")
        if not token or not secrets.compare_digest(authorization, "Bearer " + token):
            raise Fault("A valid reviewer bearer token is required.", 401)
        return "reviewer"

    @app.exception_handler(Fault)
    async def fault_handler(request: Request, error: Fault):
        return JSONResponse(status_code=error.status, content={"detail": str(error)})

    @app.get("/health")
    def health():
        return {"status": "ok", "project": name}

    return app, auth, reviewer


def run_cli(package: str, demo, keys: tuple[str, ...], extend=None):
    import argparse

    parser = argparse.ArgumentParser(prog=package)
    sub = parser.add_subparsers(dest="command", required=True)
    configure = sub.add_parser("setup")
    configure.add_argument("--shared", action="store_true")
    sub.add_parser("demo")
    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=8020)
    sub.add_parser("check-keys")
    if extend:
        extend(sub)
    args = parser.parse_args()
    try:
        if args.command == "setup":
            result = setup(keys, args.shared)
        elif args.command == "check-keys":
            result = {
                "configured": {name: bool(settings().get(name)) for name in keys},
                "live_validated": False,
            }
        elif args.command == "serve":
            import uvicorn

            uvicorn.run(f"{package}.api:app", host="127.0.0.1", port=args.port)
            return
        elif args.command == "demo":
            result = demo()
        else:
            result = args.action(args)
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    except Fault as error:
        parser.exit(1, f"{error}\n")
