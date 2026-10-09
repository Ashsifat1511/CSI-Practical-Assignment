# AI Usage

## Tool

An AI coding assistant (Anthropic Claude, used through the Claude Code CLI) during the build stage, as the
brief allows. No AI was used during the reading and clarification stages.

## How it was used

1. **Clarification:** the assistant read the brief and asked me questions before writing any code: candidate ID
   handling, tech stack, the batch-atomicity contradiction between §5.2/§6.1 and §7, and git identity.
   My answers are recorded in `plan.md` §0.
2. **Planning:** it wrote `plan.md` (entities, module layout, schema, transaction strategy, test list, commit plan).
3. **Implementation:** it generated the backend modules, SQL migration, MQTT worker, React dashboard, tests and
   docs, step by step, with a commit per feature.
4. **Verification:** the code was run in Docker. The REST flows were smoke tested with curl, the 18 pytest
   tests were run, and the MQTT worker was exercised against a local Mosquitto broker (challenge, replay,
   broker restart and reconnect).

## What I reviewed or changed

* Removed a second advisory lock that a VOID took on its own ID, because it could deadlock with lock ordering.
  Now each item takes exactly one lock (the COUNT ID), and lost insert races are classified explicitly.
* Fixed inconsistent `event_time` formatting in the exceptions view.
* Simplified worker shutdown: waiting for a publish inside the signal handler would block the network loop.
* Chose and documented the assumptions in `TECHNICAL_EXPLANATION.md` §7.

## I can explain

The entity model, `process_count` / `process_void` / `resolve_pending_voids`, the savepoint-per-item batch,
the advisory lock and `ON CONFLICT` duplicate strategy, and the MQTT challenge trace from `on_message` to the
stored and published response.

## Conversation record

The full AI conversation export is attached to the submission as required (exported separately from the CLI
session; it is not stored in this repository).
