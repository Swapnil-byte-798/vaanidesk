"""A Hindi receptionist whose booking effects require explicit confirmation."""

from datetime import datetime, timezone, timedelta
import json
import re
import sqlite3
import time
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from .runtime import Fault, LLM, Store, digest, require

SCHEMA = """
CREATE TABLE IF NOT EXISTS calls(id TEXT PRIMARY KEY,state TEXT NOT NULL DEFAULT 'active',generation INTEGER NOT NULL DEFAULT 0,created DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS turns(call_id TEXT NOT NULL REFERENCES calls(id),request_key TEXT NOT NULL,fingerprint TEXT NOT NULL,transcript TEXT NOT NULL,result TEXT NOT NULL,created DOUBLE PRECISION NOT NULL,PRIMARY KEY(call_id,request_key));
CREATE TABLE IF NOT EXISTS proposals(id TEXT PRIMARY KEY,call_id TEXT NOT NULL REFERENCES calls(id),kind TEXT NOT NULL,slot TEXT,patient TEXT,generation INTEGER NOT NULL,state TEXT NOT NULL DEFAULT 'pending',expires DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS appointments(id TEXT PRIMARY KEY,call_id TEXT NOT NULL REFERENCES calls(id),slot TEXT NOT NULL,patient TEXT NOT NULL,state TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS active_slot ON appointments(slot) WHERE state='booked';
CREATE TABLE IF NOT EXISTS decisions(call_id TEXT NOT NULL,request_key TEXT NOT NULL,fingerprint TEXT NOT NULL,result TEXT NOT NULL,PRIMARY KEY(call_id,request_key))
"""


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent: str = Field(pattern="^(book|cancel|status|handoff|clarify)$")
    patient: str = Field(default="", max_length=100)
    slot: str | None = None


def normalize_slot(value: str, now: datetime) -> str:
    try:
        parsed = datetime.fromisoformat(value)
        if (
            parsed.tzinfo is None
            or parsed.second
            or parsed.microsecond
            or parsed.minute % 15
        ):
            raise ValueError("Specify a timezone and a quarter-hour slot")
        parsed = parsed.astimezone(timezone.utc)
        if not now + timedelta(minutes=15) <= parsed <= now + timedelta(days=30):
            raise ValueError("Outside booking window")
        return parsed.isoformat()
    except (ValueError, TypeError):
        raise Fault(
            "Choose a timezone-aware 15-minute slot between 15 minutes and 30 days from now."
        ) from None


class Receptionist:
    def __init__(self, path=".data/vaanidesk.db", llm=None, clock=time.time):
        self.store = Store(path, SCHEMA)
        self.llm = llm or LLM()
        self.clock = clock

    def start_call(self):
        identifier = str(uuid4())
        with self.store.transaction() as connection:
            connection.execute(
                "INSERT INTO calls(id,created) VALUES(?,?)", (identifier, self.clock())
            )
        return {
            "call_id": identifier,
            "greeting": "नमस्ते। मैं AI सहायक हूँ। अपॉइंटमेंट के लिए मदद कर सकती हूँ। किसी व्यक्ति से बात करनी हो तो बताइए।",
        }

    def turn(self, identifier, transcript, request_key):
        transcript = require(transcript, "Transcript", 2000)
        request_key = require(request_key, "Request key", 100)
        fingerprint = digest(transcript)
        now = self.clock()
        started = time.perf_counter()
        with self.store.transaction() as connection:
            call = connection.execute(
                "SELECT * FROM calls WHERE id=?", (identifier,)
            ).fetchone()
            if call is None:
                raise Fault("Call not found.", 404)
            previous = connection.execute(
                "SELECT * FROM turns WHERE call_id=? AND request_key=?",
                (identifier, request_key),
            ).fetchone()
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise Fault("Request key belongs to a different utterance.", 409)
                return json.loads(previous["result"])
            proposal = connection.execute(
                "SELECT * FROM proposals WHERE call_id=? AND state='pending' ORDER BY expires DESC LIMIT 1",
                (identifier,),
            ).fetchone()
            if call["state"] == "human":
                return {
                    "reply": "आपकी बात मानव सहायता टीम को भेज दी गई है।",
                    "state": "human",
                    "llm_calls": 0,
                }
            generation = call["generation"] + 1
        if proposal and re.fullmatch(
            r"(?:yes|haan|ha|confirm|हाँ|हां|जी हाँ|हाँ बुक करें)[.! ]*", transcript, re.I
        ):
            result = self.confirm(identifier, proposal["id"], request_key)
            with self.store.transaction() as connection:
                connection.execute(
                    "INSERT OR IGNORE INTO turns VALUES(?,?,?,?,?,?)",
                    (
                        identifier,
                        request_key,
                        fingerprint,
                        transcript,
                        json.dumps(result),
                        now,
                    ),
                )
            return result
        with self.store.transaction() as connection:
            connection.execute(
                "UPDATE calls SET generation=? WHERE id=?", (generation, identifier)
            )
            connection.execute(
                "UPDATE proposals SET state='superseded' WHERE call_id=? AND state='pending'",
                (identifier,),
            )
        if re.search(r"human|person|agent|इंसान|व्यक्ति|मानव", transcript, re.I):
            plan = Plan(intent="handoff")
            calls = 0
        else:
            try:
                plan = Plan.model_validate(
                    self.llm.ask(
                        "You are a Hindi/Hinglish clinic receptionist, not a clinician. Extract a proposed appointment intent only. Never diagnose or claim a booking was made. Patient must be an exact substring of the utterance. Return schema: "
                        + json.dumps(Plan.model_json_schema()),
                        {
                            "transcript": transcript,
                            "now_utc": datetime.fromtimestamp(
                                now, timezone.utc
                            ).isoformat(),
                        },
                    )
                )
            except ValidationError:
                raise Fault(
                    "The voice planner returned an invalid intent.", 502
                ) from None
            calls = 1
        result = {
            "state": "active",
            "llm_calls": calls,
            "plan_latency_ms": round((time.perf_counter() - started) * 1000),
        }
        with self.store.transaction() as connection:
            latest = connection.execute(
                "SELECT generation FROM calls WHERE id=?", (identifier,)
            ).fetchone()
            if latest["generation"] != generation:
                raise Fault("A newer utterance superseded this plan.", 409)
            if plan.intent == "book":
                if (
                    not plan.patient
                    or plan.patient.casefold() not in transcript.casefold()
                    or not plan.slot
                ):
                    raise Fault(
                        "Please state the patient name and appointment time before a proposal is created."
                    )
                slot = normalize_slot(
                    plan.slot, datetime.fromtimestamp(now, timezone.utc)
                )
                proposal_id = str(uuid4())
                connection.execute(
                    "INSERT INTO proposals(id,call_id,kind,slot,patient,generation,expires) VALUES(?,?,'book',?,?,?,?)",
                    (
                        proposal_id,
                        identifier,
                        slot,
                        plan.patient,
                        generation,
                        now + 300,
                    ),
                )
                result.update(
                    proposal_id=proposal_id,
                    reply=f"{plan.patient} के लिए {slot} का प्रस्ताव तैयार है। अभी बुकिंग नहीं हुई है। पुष्टि के लिए हाँ कहें।",
                    state="awaiting_confirmation",
                )
            elif plan.intent == "cancel":
                booking = connection.execute(
                    "SELECT * FROM appointments WHERE call_id=? AND state='booked' ORDER BY slot LIMIT 1",
                    (identifier,),
                ).fetchone()
                if not booking:
                    result["reply"] = "इस कॉल में कोई सक्रिय अपॉइंटमेंट नहीं है।"
                else:
                    proposal_id = str(uuid4())
                    connection.execute(
                        "INSERT INTO proposals(id,call_id,kind,slot,patient,generation,expires) VALUES(?,?,'cancel',?,?,?,?)",
                        (
                            proposal_id,
                            identifier,
                            booking["slot"],
                            booking["patient"],
                            generation,
                            now + 300,
                        ),
                    )
                    result.update(
                        proposal_id=proposal_id,
                        reply=f"{booking['slot']} की बुकिंग रद्द करने के लिए हाँ कहें।",
                        state="awaiting_confirmation",
                    )
            elif plan.intent == "status":
                booking = connection.execute(
                    "SELECT * FROM appointments WHERE call_id=? AND state='booked' ORDER BY slot LIMIT 1",
                    (identifier,),
                ).fetchone()
                result["reply"] = (
                    f"आपकी अपॉइंटमेंट {booking['slot']} पर बुक है।"
                    if booking
                    else "इस कॉल में अभी कोई अपॉइंटमेंट बुक नहीं है।"
                )
            elif plan.intent == "handoff":
                connection.execute(
                    "UPDATE calls SET state='human' WHERE id=?", (identifier,)
                )
                result.update(
                    state="human", reply="आपकी बात मानव सहायता टीम को भेज दी गई है।"
                )
            else:
                result["reply"] = (
                    "कृपया अपॉइंटमेंट का दिन, समय और नाम बताइए। चिकित्सा सलाह के लिए डॉक्टर से बात करें।"
                )
            connection.execute(
                "INSERT INTO turns VALUES(?,?,?,?,?,?)",
                (
                    identifier,
                    request_key,
                    fingerprint,
                    transcript,
                    json.dumps(result),
                    now,
                ),
            )
        return result

    def confirm(self, identifier, proposal_id, request_key):
        fingerprint = digest(proposal_id)
        now = self.clock()
        with self.store.transaction() as connection:
            previous = connection.execute(
                "SELECT * FROM decisions WHERE call_id=? AND request_key=?",
                (identifier, request_key),
            ).fetchone()
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise Fault("Confirmation key belongs to another proposal.", 409)
                return json.loads(previous["result"])
            proposal = connection.execute(
                "SELECT p.*,c.generation AS current_generation,c.state AS call_state FROM proposals p JOIN calls c ON p.call_id=c.id WHERE p.id=? AND p.call_id=?",
                (proposal_id, identifier),
            ).fetchone()
            if proposal is None:
                raise Fault("Proposal not found for this call.", 404)
            if (
                proposal["state"] != "pending"
                or proposal["expires"] < now
                or proposal["generation"] != proposal["current_generation"]
                or proposal["call_state"] != "active"
            ):
                raise Fault(
                    "Proposal expired, was superseded, or is already decided.", 409
                )
            if proposal["kind"] == "book":
                normalize_slot(
                    proposal["slot"], datetime.fromtimestamp(now, timezone.utc)
                )
                try:
                    connection.execute(
                        "INSERT INTO appointments VALUES(?,?,?,?,'booked')",
                        (
                            str(uuid4()),
                            identifier,
                            proposal["slot"],
                            proposal["patient"],
                        ),
                    )
                except sqlite3.IntegrityError:
                    raise Fault(
                        "This slot was booked by another call. Choose another time.",
                        409,
                    ) from None
                reply = f"अपॉइंटमेंट {proposal['slot']} पर बुक हो गई है।"
            else:
                if not connection.execute(
                    "UPDATE appointments SET state='cancelled' WHERE call_id=? AND slot=? AND state='booked'",
                    (identifier, proposal["slot"]),
                ).rowcount:
                    raise Fault("This appointment is no longer active.", 409)
                reply = "अपॉइंटमेंट रद्द कर दी गई है।"
            connection.execute(
                "UPDATE proposals SET state='confirmed' WHERE id=?", (proposal_id,)
            )
            result = {
                "state": "confirmed",
                "action": proposal["kind"],
                "slot": proposal["slot"],
                "reply": reply,
                "llm_calls": 0,
            }
            connection.execute(
                "INSERT INTO decisions VALUES(?,?,?,?)",
                (identifier, request_key, fingerprint, json.dumps(result)),
            )
            return result

    def log(self, identifier):
        with self.store.transaction() as connection:
            call = connection.execute(
                "SELECT * FROM calls WHERE id=?", (identifier,)
            ).fetchone()
            if call is None:
                raise Fault("Call not found.", 404)
            return {
                "call": dict(call),
                "turns": [
                    {
                        "transcript": row["transcript"],
                        "result": json.loads(row["result"]),
                    }
                    for row in connection.execute(
                        "SELECT * FROM turns WHERE call_id=? ORDER BY created",
                        (identifier,),
                    )
                ],
                "appointments": [
                    dict(row)
                    for row in connection.execute(
                        "SELECT * FROM appointments WHERE call_id=?", (identifier,)
                    )
                ],
            }
