import asyncio, io, wave
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from vaanidesk.runtime import Fault
from vaanidesk.service import Receptionist, normalize_slot
from vaanidesk.speech import SpeechEpoch, validate_wav


class Planner:
    def __init__(self):
        self.calls = 0
        self.intent = "book"

    def ask(self, *args):
        self.calls += 1
        return {
            "intent": self.intent,
            "patient": "Maya",
            "slot": "2026-11-12T10:00:00+00:00",
        }


class BookingTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.now = datetime(2026, 11, 11, tzinfo=timezone.utc).timestamp()
        self.planner = Planner()
        self.receptionist = Receptionist(
            Path(self.directory.name) / "test.db", self.planner, clock=lambda: self.now
        )
        self.call = self.receptionist.start_call()["call_id"]

    def tearDown(self):
        self.directory.cleanup()

    def proposal(self, key="one", call=None):
        return self.receptionist.turn(
            call or self.call, "Book Maya for tomorrow", "one" if key is None else key
        )

    def test_proposal_does_not_create_booking(self):
        result = self.proposal()
        self.assertEqual(result["state"], "awaiting_confirmation")
        self.assertEqual(self.receptionist.log(self.call)["appointments"], [])

    def test_confirmation_records_booking(self):
        proposal = self.proposal()
        result = self.receptionist.confirm(
            self.call, proposal["proposal_id"], "confirm"
        )
        self.assertEqual(result["action"], "book")
        self.assertEqual(len(self.receptionist.log(self.call)["appointments"]), 1)

    def test_confirmation_retry_is_idempotent(self):
        proposal = self.proposal()
        first = self.receptionist.confirm(self.call, proposal["proposal_id"], "confirm")
        self.assertEqual(
            first,
            self.receptionist.confirm(self.call, proposal["proposal_id"], "confirm"),
        )

    def test_double_booking_is_prevented(self):
        first = self.proposal()
        other = self.receptionist.start_call()["call_id"]
        second = self.proposal(call=other)
        self.receptionist.confirm(self.call, first["proposal_id"], "confirm")
        with self.assertRaises(Fault):
            self.receptionist.confirm(other, second["proposal_id"], "confirm")

    def test_cross_call_proposal_rejected(self):
        proposal = self.proposal()
        other = self.receptionist.start_call()["call_id"]
        with self.assertRaises(Fault):
            self.receptionist.confirm(other, proposal["proposal_id"], "confirm")

    def test_expired_proposal_rejected(self):
        proposal = self.proposal()
        self.now += 301
        with self.assertRaises(Fault):
            self.receptionist.confirm(self.call, proposal["proposal_id"], "confirm")

    def test_new_utterance_supersedes_old_proposal(self):
        first = self.proposal()
        self.proposal("two")
        with self.assertRaises(Fault):
            self.receptionist.confirm(self.call, first["proposal_id"], "confirm")

    def test_yes_confirms_without_another_model_call(self):
        self.proposal()
        result = self.receptionist.turn(self.call, "haan", "confirm")
        self.assertEqual(result["action"], "book")
        self.assertEqual(self.planner.calls, 1)
        self.assertEqual(result, self.receptionist.turn(self.call, "haan", "confirm"))

    def test_human_handoff_skips_model(self):
        result = self.receptionist.turn(self.call, "I want a human", "one")
        self.assertEqual(result["state"], "human")
        self.assertEqual(self.planner.calls, 0)

    def test_handoff_prevents_new_booking(self):
        self.receptionist.turn(self.call, "human please", "one")
        self.assertEqual(
            self.receptionist.turn(self.call, "Book Maya tomorrow", "two")["state"],
            "human",
        )

    def test_request_key_is_payload_bound(self):
        self.proposal()
        with self.assertRaises(Fault):
            self.receptionist.turn(self.call, "Different utterance", "one")

    def test_identical_request_skips_second_model_call(self):
        first = self.proposal()
        self.assertEqual(first, self.proposal())
        self.assertEqual(self.planner.calls, 1)

    def test_invented_patient_is_not_accepted(self):
        with self.assertRaises(Fault):
            self.receptionist.turn(self.call, "Book a slot tomorrow", "one")

    def test_cancel_requires_confirmation(self):
        proposal = self.proposal()
        self.receptionist.confirm(self.call, proposal["proposal_id"], "book")
        self.planner.intent = "cancel"
        cancel = self.receptionist.turn(self.call, "Cancel the appointment", "cancel")
        self.assertEqual(
            self.receptionist.log(self.call)["appointments"][0]["state"], "booked"
        )
        self.receptionist.confirm(self.call, cancel["proposal_id"], "confirm-cancel")
        self.assertEqual(
            self.receptionist.log(self.call)["appointments"][0]["state"], "cancelled"
        )

    def test_invalid_slot_formats(self):
        now = datetime(2026, 11, 11, tzinfo=timezone.utc)
        for value in [
            "2026-11-12T10:00:00",
            "2026-11-12T10:07:00+00:00",
            "2020-01-01T10:00:00+00:00",
        ]:
            with self.subTest(value=value), self.assertRaises(Fault):
                normalize_slot(value, now)

    def test_timezone_normalization(self):
        result = normalize_slot(
            "2026-11-12T15:30:00+05:30", datetime(2026, 11, 11, tzinfo=timezone.utc)
        )
        self.assertEqual(result, "2026-11-12T10:00:00+00:00")

    def test_actual_wav_validation(self):
        data = io.BytesIO()
        with wave.open(data, "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(16000)
            stream.writeframes(b"\0\0" * 16000)
        validate_wav(data.getvalue())
        with self.assertRaises(Fault):
            validate_wav(b"not wave audio")

    def test_barge_in_discards_stale_chunks(self):
        async def run():
            epoch = SpeechEpoch()

            async def chunks():
                yield b"first"
                epoch.interrupt()
                yield b"stale"

            return [chunk async for chunk in epoch.current_chunks(chunks())]

        self.assertEqual(asyncio.run(run()), [b"first"])

    def test_call_log_survives_restart(self):
        self.proposal()
        reopened = Receptionist(self.receptionist.store.path, self.planner)
        self.assertEqual(len(reopened.log(self.call)["turns"]), 1)

    def test_unknown_call_is_not_exposed(self):
        with self.assertRaises(Fault):
            self.receptionist.log("missing")
