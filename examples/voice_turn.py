"""Send a WAV utterance to the local API; requires real configured providers."""

import argparse
import base64
import json
from pathlib import Path
from uuid import uuid4

import httpx
from websockets.sync.client import connect

from vaanidesk.runtime import Fault, settings
from vaanidesk.speech import validate_wav


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument(
        "--call-id", help="Continue an existing call, including its confirmation"
    )
    parser.add_argument("--port", type=int, default=8020)
    parser.add_argument("--output", type=Path, default=Path("artifacts/reply.mp3"))
    args = parser.parse_args()
    token = settings().get("API_TOKEN", "")
    if not token:
        parser.exit(1, "Run python -m vaanidesk setup first.\n")
    if not args.audio.is_file() or args.audio.stat().st_size > 8_000_000:
        parser.exit(1, "Choose an existing WAV utterance of at most 8 MB.\n")
    audio = args.audio.read_bytes()
    try:
        validate_wav(audio)
    except Fault as error:
        parser.exit(1, str(error) + "\n")
    headers = {"Authorization": "Bearer " + token}
    identifier = args.call_id
    if not identifier:
        with httpx.Client(trust_env=False, timeout=30) as client:
            response = client.post(
                f"http://127.0.0.1:{args.port}/calls", headers=headers
            )
            response.raise_for_status()
            identifier = response.json()["call_id"]
    print(json.dumps({"call_id": identifier, "mode": "live_provider_voice"}))
    reply = bytearray()
    with connect(
        f"ws://127.0.0.1:{args.port}/calls/{identifier}/voice",
        additional_headers=headers,
        proxy=None,
        open_timeout=10,
    ) as connection:
        connection.send(
            json.dumps(
                {
                    "type": "utterance",
                    "audio": base64.b64encode(audio).decode(),
                    "request_key": str(uuid4()),
                }
            )
        )
        while True:
            frame = json.loads(connection.recv(timeout=90))
            if frame["type"] == "audio":
                reply.extend(base64.b64decode(frame["data"], validate=True))
            elif frame["type"] == "audio_end":
                break
            elif frame["type"] == "error":
                parser.exit(1, frame["message"] + "\n")
            else:
                print(json.dumps(frame, ensure_ascii=False))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(reply)
    args.output.chmod(0o600)
    print(json.dumps({"speech_file": str(args.output), "bytes": len(reply)}))


if __name__ == "__main__":
    main()
