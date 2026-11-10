# VaaniDesk

A Hindi appointment receptionist that asks before changing a booking.

[![Python verification](https://github.com/Swapnil-byte-798/vaanidesk/actions/workflows/ci.yml/badge.svg)](https://github.com/Swapnil-byte-798/vaanidesk/actions/workflows/ci.yml)

Project 4 in a Python and SQL AI engineering portfolio. The interface is a Python CLI and FastAPI's generated API documentation.

## What works

- Hindi and Hinglish text turns and a Python WebSocket voice protocol
- Expiring booking and cancellation proposals with explicit confirmation
- Unique active slots, request replay protection, persistent call logs and human handoff
- Streaming TTS playback with interruption epochs that discard stale audio

## Run it

Python 3.11 or 3.12. FFmpeg is included in the container; voice input must be mono, 16 kHz, 16-bit WAV.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m pytest -q
python -m vaanidesk demo
python -m vaanidesk setup
python -m vaanidesk serve --port 8020
```

Open http://127.0.0.1:8020/docs. `setup` prompts for credentials without displaying them, generates independent operator/reviewer tokens, and stores them in an ignored file with owner-only permissions. Use the operator token as `Authorization: Bearer ...`; account, client and tenant endpoints issue their own scoped tokens. Do not commit any of these values.

The demo is explicitly labelled: authored fixtures exercise the workflow, not a live provider. `check-keys` reports configuration only and never claims a provider has been validated.

## Credentials

GROQ_API_KEY for STT and intent planning; SARVAM_API_KEY for speech synthesis.

To reuse a provider key across these repositories, run `python -m vaanidesk setup --shared` before the local `setup` command. The shared prompt adds missing keys and preserves existing nonempty keys. Local environment variables take precedence. See [.env.example](.env.example) for names only; real keys are never shipped.

## Design and boundaries

WAV utterance → Groq transcription → validated intent → confirmation → appointment transaction → Sarvam speech

Speech recognition processes complete utterances; it is not incremental WebRTC recognition. Cancelling playback cannot undo a confirmed booking. The appointment ledger is local, with no clinic calendar or telephone carrier integration. Do not use the receptionist for medical advice.

## Verification

Confirmation expiry, slot contention, cancellation ownership, handoff, Hindi affirmative replay, WAV validation and speech epoch cancellation. Common tests also cover hidden setup, configuration precedence, provider response validation, API authentication and transactional rollback.

[Verification record](evidence/verification.json) separates automated checks from provider-dependent acceptance. No result represents all possible inputs, production load, or a calibrated model-quality benchmark.

## Read further

- [Requirements and acceptance](docs/REQUIREMENTS.md)
- [Architecture and decisions](docs/ARCHITECTURE.md)
- [Runbook and key setup](docs/RUNBOOK.md)
- [Study guide](docs/STUDY_GUIDE.md)

## Next engineering milestone

Measure Hindi STT error and end-to-end turn latency on consented recordings; connect a real calendar only after confirmation and recovery tests.
