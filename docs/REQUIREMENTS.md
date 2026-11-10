# VaaniDesk: requirements and acceptance

## Product goal

A Hindi appointment receptionist that asks before changing a booking. A reviewer should be able to install the package, run its tests, inspect its SQL boundaries and exercise the core workflow without a custom frontend.

## Functional requirements

- Hindi and Hinglish text turns and a Python WebSocket voice protocol
- Expiring booking and cancellation proposals with explicit confirmation
- Unique active slots, request replay protection, persistent call logs and human handoff
- Streaming TTS playback with interruption epochs that discard stale audio

## Acceptance conditions

1. An unconfirmed or expired proposal must never write an appointment.
2. Two confirmed calls cannot own the same active slot.
3. Replaying a request cannot book twice or invoke the planner again.
4. An interrupted speech generation cannot emit later audio chunks.

## Quality requirements

Persist domain state transactionally; validate untrusted inputs; keep provider failures visible; avoid credential and prompt disclosure in diagnostic errors; provide an installable Python package, authenticated API, container and repeatable automated checks. Keep fixture evidence distinct from public-data and live-provider evidence.

## Scope boundary

Speech recognition processes complete utterances; it is not incremental WebRTC recognition. Cancelling playback cannot undo a confirmed booking. The appointment ledger is local, with no clinic calendar or telephone carrier integration. Do not use the receptionist for medical advice.

## Evaluation plan

Confirmation expiry, slot contention, cancellation ownership, handoff, Hindi affirmative replay, WAV validation and speech epoch cancellation. External checks need the credentials, model files or network access described in the runbook. Before publishing quality claims, create a labelled evaluation set and report sample size, metric definition, model version and measured failures. Measure Hindi STT error and end-to-end turn latency on consented recordings; connect a real calendar only after confirmation and recovery tests.
