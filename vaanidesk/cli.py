from datetime import datetime, timedelta
from pathlib import Path
import tempfile
from .runtime import run_cli
from .service import Receptionist


class FixturePlanner:
    def ask(self, instruction, payload):
        now = datetime.fromisoformat(payload["now_utc"])
        slot = (now + timedelta(days=1)).replace(
            hour=10, minute=0, second=0, microsecond=0
        )
        return {"intent": "book", "patient": "Maya", "slot": slot.isoformat()}


def demo():
    with tempfile.TemporaryDirectory() as directory:
        receptionist = Receptionist(Path(directory) / "demo.db", FixturePlanner())
        call = receptionist.start_call()
        proposal = receptionist.turn(
            call["call_id"], "Maya ke liye kal appointment book karna hai", "one"
        )
        decision = receptionist.confirm(
            call["call_id"], proposal["proposal_id"], "confirm-one"
        )
        return {
            "mode": "injected_planner_fixture",
            "proposal": proposal,
            "confirmed": decision,
            "speech_provider_tested": False,
        }


def main():
    run_cli("vaanidesk", demo, ("GROQ_API_KEY", "SARVAM_API_KEY"))
