# VaaniDesk: study guide

## Explain the problem

A Hindi appointment receptionist that asks before changing a booking. Explain who uses the workflow and why each confirmation, isolation or validation boundary exists before describing libraries.

## Read the implementation

1. Read `service.py` and its SQL schema. Name the durable entities and draw their valid state transitions.
2. Trace one successful API request from its Pydantic input to its committed writes.
3. Trace a provider failure and identify what can and cannot be rolled back.
4. Read the tests that enforce the acceptance conditions below. Explain why each failure would matter to a user.

1. An unconfirmed or expired proposal must never write an appointment.
2. Two confirmed calls cannot own the same active slot.
3. Replaying a request cannot book twice or invoke the planner again.
4. An interrupted speech generation cannot emit later audio chunks.

## Interview discussion

- Where is the transaction boundary, and what happens if the process dies immediately after it?
- How are retries bound to the original request? Which operation can safely repeat?
- Which inputs are untrusted, and how are externally generated fields validated?
- What is tested with fixtures, what was measured on public data, and what remains unmeasured?
- What would change with ten times the data, multiple workers, or another tenant?

## Honest project explanation

Describe the implemented behavior, then demonstrate a representative test and explain its failure condition. Do not present mock latency, authored examples, uncalibrated scores or optional integrations as measured production results.

Known boundaries: Speech recognition processes complete utterances; it is not incremental WebRTC recognition. Cancelling playback cannot undo a confirmed booking. The appointment ledger is local, with no clinic calendar or telephone carrier integration. Do not use the receptionist for medical advice.

## Practical extension

Measure Hindi STT error and end-to-end turn latency on consented recordings; connect a real calendar only after confirmation and recovery tests. Define a measurable acceptance criterion before extending the implementation.
