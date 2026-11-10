import asyncio, base64, json, os, secrets
from fastapi import Depends, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field
from .runtime import Fault, make_app, settings
from .service import Receptionist
from .speech import SpeechEpoch, synthesize, transcribe

app, auth, reviewer = make_app("VaaniDesk")


def service():
    return Receptionist(
        os.path.join(os.environ.get("DATA_DIR", ".data"), "vaanidesk.db")
    )


class Turn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transcript: str = Field(min_length=1, max_length=2000)
    request_key: str = Field(min_length=1, max_length=100)


class Confirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proposal_id: str
    request_key: str = Field(min_length=1, max_length=100)


@app.post("/calls", dependencies=[Depends(auth)])
def create():
    return service().start_call()


@app.post("/calls/{identifier}/turns", dependencies=[Depends(auth)])
def turn(identifier: str, body: Turn):
    return service().turn(identifier, **body.model_dump())


@app.post("/calls/{identifier}/confirm", dependencies=[Depends(auth)])
def confirm(identifier: str, body: Confirmation):
    return service().confirm(identifier, **body.model_dump())


@app.get("/calls/{identifier}", dependencies=[Depends(auth)])
def log(identifier: str):
    return service().log(identifier)


@app.websocket("/calls/{identifier}/voice")
async def voice(websocket: WebSocket, identifier: str):
    token = settings().get("API_TOKEN", "")
    if not token or not secrets.compare_digest(
        websocket.headers.get("authorization", ""), "Bearer " + token
    ):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    epoch = SpeechEpoch()
    pending = None

    async def process(audio, key):
        try:
            transcript = await asyncio.to_thread(transcribe, audio)
            result = await asyncio.to_thread(
                service().turn, identifier, transcript, key
            )
            await websocket.send_json(
                {"type": "reply", "transcript": transcript, **result}
            )
            async for chunk in epoch.current_chunks(synthesize(result["reply"])):
                await websocket.send_json(
                    {
                        "type": "audio",
                        "codec": "mp3",
                        "data": base64.b64encode(chunk).decode(),
                    }
                )
            await websocket.send_json({"type": "audio_end"})
        except Fault as error:
            await websocket.send_json({"type": "error", "message": str(error)})

    try:
        while True:
            message = await websocket.receive_text()
            if len(message) > 11_000_000:
                raise Fault("Voice frame exceeded its size limit.")
            value = json.loads(message)
            if not isinstance(value, dict):
                raise Fault("Voice frames must be JSON objects.")
            if value.get("type") == "interrupt":
                epoch.interrupt()
                if pending:
                    pending.cancel()
                await websocket.send_json({"type": "interrupted"})
            elif value.get("type") == "utterance":
                epoch.interrupt()
                if pending:
                    pending.cancel()
                try:
                    audio = base64.b64decode(value.get("audio", ""), validate=True)
                except ValueError:
                    raise Fault("Invalid base64 utterance.") from None
                key = value.get("request_key", "")
                pending = asyncio.create_task(process(audio, key))
            else:
                await websocket.send_json(
                    {"type": "error", "message": "Use utterance or interrupt frames."}
                )
    except WebSocketDisconnect:
        pass
    except (Fault, ValueError) as error:
        await websocket.send_json(
            {
                "type": "error",
                "message": str(error)
                if isinstance(error, Fault)
                else "Invalid JSON voice frame.",
            }
        )
        await websocket.close(code=1008)
    finally:
        epoch.interrupt()
        if pending:
            pending.cancel()
