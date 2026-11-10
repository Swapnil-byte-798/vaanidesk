# VaaniDesk: architecture

## Request and data path

WAV utterance → Groq transcription → validated intent → confirmation → appointment transaction → Sarvam speech

`api.py` validates transport input and credentials. `service.py` owns domain invariants and SQL transitions. `runtime.py` supplies small, vendored transport/configuration conventions so the repository can install independently. `cli.py` exposes setup, a labelled demo and serving commands. SQL schemas live beside the service that owns them.

## Decisions

Python and SQL keep the project aligned with the portfolio's existing skills. FastAPI supplies an inspectable contract without authored web code. SQLite provides a durable, low-cost local path; it is not a distributed database. The service owns transaction boundaries rather than spreading writes across API handlers. Domain services accept provider fixtures for tests; production commands never replace failed upstream calls with fixtures.

Provider keys come from owner-only files or the process environment. Tokens are distinct from provider keys. Never put a provider key into a public API client's configuration. Concurrent SQL writes are admitted transactionally; processes that die during external work can require operator recovery rather than unsafe automatic replay.

## Failure boundaries

1. An unconfirmed or expired proposal must never write an appointment.
2. Two confirmed calls cannot own the same active slot.
3. Replaying a request cannot book twice or invoke the planner again.
4. An interrupted speech generation cannot emit later audio chunks.

Missing credentials return a configuration failure. Invalid provider structures fail validation. Network requests have timeouts and no silent retries through the common JSON client. An upstream timeout can still consume provider tokens; local rollback cannot undo external billing.

## Capacity and security limits

Speech recognition processes complete utterances; it is not incremental WebRTC recognition. Cancelling playback cannot undo a confirmed booking. The appointment ledger is local, with no clinic calendar or telephone carrier integration. Do not use the receptionist for medical advice.

Bind locally for development. Before external hosting, add TLS, gateway request-size limits, restricted egress, protected persistent storage, retention policies and a process-specific secret store. No internet-facing deployment or production security certification is claimed by this repository.
