The aim: Understand a real business problem, ask useful questions, design a clear solution, and show a working frontend. Fast code without understanding is not the goal.

1 The customer story: why are we building this?

A small garment factory, NorthBridge Garments (fictional), operates several production lines. Each line has a counter device that reports how many finished pieces were produced. Today, the supervisor collects numbers from different screens and handwritten notes. Some messages arrive late; sometimes the same message is sent twice after a network reconnect. A faulty sensor can also report pieces that were never made.

The factory manager wants one dashboard for current operations and, later, for each work shift. The dashboard must show the true total, events waiting for review, mistakes that need attention, and whether connected devices are responding. A supervisor must be able to review completed events and acknowledge them. If an earlier count was wrong. a correction should reverse it without deleting history.

Your job: Build the small first version that CSI Smart Tech could demonstrate to this customer. It must work now and be easy to change when the customer adds more lines, business rules or devices later.

End user

Production supervisor

Factory floor operator

Support/engineering team

What they need

See reliable totals, inspect exceptions, and acknowledge reviewed events.

Submit a count or correction when a device or report needs manual attention.

Check device connection, see the last challenge, and trace errors without losing history.

2 Understand the entities (simple examples)

An entity is a real thing or business record your software must recognize and store. A function is an action the software performs on those records. An event says that something happened.

Entity

Production source

Production event

Submission attempt

Acknowledgement

MQTT challenge

Simple meaning and example

A line or machine sending counts, such as LINE-01.

One action: COUNT \+5 (EV-101), or VOID a wrong earlier count.

Every received request, including the second copy of EV-101 or an invalid request.

A review marker saying a successfully processed event has been checked.

A device-simulator request identified by challenge\_id that needs a matching response.

Example: LINE-01 reports COUNT 5\. The total becomes 5\. The same message arrives again: the total must stay 5\. Later a valid VOID of that COUNT arrives: the total becomes 0\. Every step remains traceable.

3 Candidate instructions and working time

Stage

Time

Rule

Read the full brief

10 minutes

Do not code. Identify actors, entities, business rules, unctear points and expected outputs.

Clarity with examiner

10 minutes

Ask up to seven meaningful questions. No Al or search in these first two stages.

Build, test and demo

85 minutes

Al is allowed, but you must explain and change any generated code.

Open the provided Google Form first; follow its delivery and deadline instructions.

Use PostgreSQL as the required persistent database. SQLite and in-memory databases are not acceptable for the

submitted solution.

Use any suitable language or framework. The architecture and working behavior matter more than a framework name.

Initialize Git; make at least three genuine progress commits and push a runnable repository to GitHub.

Submit AI\_USAGE.md and the relevant Al conversation record. Never include.env secrets, passwords, build caches, node modules or virtual environments.

If two statements seem unclear or inconsistent, raise the question. If unanswered, record your chosen assumption in TECHNICAL EXPLANATION.md.

4 Required architecture: a function-based modular monolith

Build ONE backend application, with ONE deployment and ONE PostgreSQL database. Organize it into small modules that act like independent services inside the same application. Do not create separate deployed microservices for this assessment.

In simple English: The events module decides whether a count is valid. The acknowledgement module handles review. The state module calculates totals. The MQTT module receives a device message and calls the same event-processing function used by the REST API.

REST input...

validate\_event()

\-\> process\_event() \--\>

PostgreSQL

MQTT input \----/

1 business event / callback 1

state / acknowledgement / audit

Use small functions with clear inputs and outputs. Examples: validate\_event(), process\_count(), process\_void(). resolve\_pending voids(), acknowledge\_event(), get\_summary(), handle\_mqtt\_challenge().

Keep controllers/routes thin. Place business rules in services and database commands in repositories/data-access modules.

Each module owns its responsibility. Changing a rule in one function should not require editing every route or unrelated module.

Use internal event notifications or callbacks where useful (for example, EVENT ACCEPTED, VOID\_RESOLVED or EVENT\_ACKNOWLEDGED). Trigger notifications only after successful database work.

Keep data contracts and module boundaries clear so a module could later become its own microservice and the internal notifications could later use a message broker.

Do not duplicate COUNT or VOID logic in REST routes, MQTT handlers, frontend code or multiple services.

Not required today: Kafka, RabbitMQ, multiple deployments, distributed databases, Kubernetes or a real microservices platform. Explain a credible future migration path instead.

One possible folder layout (change names to suit your framework):

modules/events/(routes, validation, service, repository)

modules/ack/(routes, service, repository)

modules/state/(routes, service,queries)

modules/matt/(worker, protocol, service)

modules/audit/{service, repository)

shared/(db,contracts, domain\_events)

frontend/ migrations/ tests/

5 Production events and business rules

5.1 Event JSON

} "source\_id": "LINE-01", "event\_id": "EV-101", "type": "COUNT", "quantity": 5, "target event\_id": null, "event\_time": "2026-10-09T10:30:002"

Field

source id

Required behavior

Required non-empty production source name.

event id

type

Required non-empty string. An event ID is globally unique across all production sources.

quantity

Exactly COUNT or VOID.

COUNT: positive integer, VOID: null or omitted.

target event\_id

COUNT: null or omitted. VOID: ID of the COUNT being reversed.

event time

Required ISO 8601 timestamp with timezone; keep the event time separate from server receipt time.

5.2 Processing, retries and corrections

COUNT adds its quantity only once after successful processing.

VOID reverses one accepted COUNT; the COUNT and VOID must have the same source\_id. One COUNT can be reversed only once.

If VOID arrives before COUNT, store it as PENDING\_REFERENCE. Resolve automatically when the matching COUNT arrives.

If several pending VOID events target one COUNT, the first stored valid VOID wins. Reject the others with clear reasons.

Process items of a batch in submitted order. Preserve the same item order in the response.

An invalid item is REJECTED and recorded, but valid items in the same batch still succeed.

Keep every rejected submission, duplicate attempt and conflict attempt in persistent storage.

Use PostgreSQL constraints and transactions to prevent double counting under repeated or concurrent requests.

Incoming case

Item status

Business effect

New valid COUNT or applicable VOID

ACCEPTED

Process once and store.

Valid VOID missing its COUNT

PENDING REFERENCE

Store, wait for matching COUNT.

Same event ID, same normalized data

DUPLICATE

Record attempt; never process twice.

Same event ID, different data

CONFLICT

Record attempt; preserve original event.

Invalid event

REJECTED

Store reason; continue other batch items.

6 Three REST APIs (JSON)

6.1 POST/api/events

Accept one event object or a JSON array. Return one item result per submitted item, in the original order. Allowed statuses: ACCEPTED, DUPLICATE, CONFLICT, PENDING REFERENCE and REJECTED.

("results": \[{"event id": "EV-101", "status": "ACCEPTED", "message": "Event processed") })

Return HTTP 200 when the top-level JSON is an event or event array, even if some items are REJECTED.

Return HTTP 400 when the top-level request cannot be interpreted as an event or event array.

Do not undo valid iterns because a different item in the batch is invalid.

6.2 GET/api/state

Example: GET /api/state?source\_id=LINE-01\&view=summary, source id is optional, view must be one of summary.

pending or exceptions.

View

summary

pending

exceptions

Return

net total, processed\_events, pending ack, unresolved, duplicates, conflicts

Successfully processed COUNT/VOID events that are ready for acknowledgement and are not yet acknowledged

Unresolved references, rejected submissions and conflict attempts, with reasons

Summary value

net total

Meaning

processed events

Sum of accepted COUNT quantities minus successfully applied VOID quantities.

pending ack

Distinct completed COUNT and VOID events; unresolved VOID joins after resolution.

unresolved

Successfully processed or resolved events not yet acknowledged.

duplicates

Valid VOID events still waiting for target COUNT.

Number of stored identical duplicate submission attempts.

conflicts

Number of stored conflicting submission attempts.

With source\_id, filter original events by stored source. Duplicate/conflict attempts use the source\_id in that attempt.

Invalid submissions without a useful source\_id appear only in unfiltered exceptions.

6.3 POST/api/ack

{"event\_ids": \["EV-101", "EV-102"\]}

Return one result for every requested ID, in order. ACKED means newly acknowledged; ALREADY\_ACKED means it was acknowledged earlier; NOT\_READY means it is unresolved, rejected or not successfully processed; NOT\_FOUND means there is no original logical event with that ID.

Repeated acknowledgements must be safe. The second copy of an ID in one request becomes ALREADY\_ACKED if the first was ACKED.

Acknowledgement never deletes history or prevents a later valid VOID from reversing a COUNT.

Both completed COUNT and completed VOID events can be acknowledged through this API.

7 PostgreSQL, history and reliability

Store logical production events, all submission attempts, original payload/normalized values, processing status, failure reason, event time, receipt time, acknowledgement information and MQTT challenge responses. Data must survive server restart.

Suggested entity/table

production sources

production events

submission attempts

matt challenges

Typical columns or responsibility

source id, display name fother fields at your discretioni

event, id, source, id, type, quantity, target event id, event time, status, acknowledged at.

row payload, source id (if present), event, id (if present), classification, enor, received at

challenge id, request digest/body, serialized result, timestamps, status.

Database modelling note: use a composite unique key on (source id, everit id), allowing different production lines to use the same event ID.

For a batch import, treat the batch as one database transaction: commit the whole batch together or roll it back if any event fails validation.

Protect competing COUNT/VOID updates and acknowledgement state with appropriate transactions, constraints and database locks where necessary.

Read summary totals from durable evidence (or from a reliably updated transactional projection), not from process memory.

Never expose SQL credentials, stack traces or internal secrets in API errors. Provide a migration/initialization command and.env.example.

8 MQTT simulator integration

The examiner simulator acts like a remote factory device. Connect as an outbound MQTT client, receive a challenge, send its events through the same processing functions as REST, read state through the same query functions, and publish a matching response. MQTT topic isolation uses the assigned Candidate ID.

Setting

Required value

Broker

152.42.238.142 | port 1883 | MQTT 3.1.1 ог 5.0

Security

Plain MQTT for this synthetic assessment only

QoS/retain

QoS 1, retain false for challenge, rasporise and status.

Candidate ID

Assigned by examiner; use only your own topic paths

Client ID

fse01-(candidate\_id)-(short\_random\_suffix)

Direction

Topic

Subscribe

fse-01/(candidate\_id}/challenge

Publish response

fse-01/(candidate\_id)/response

Publish status

fse-01/(candidate\_id)/status

8.1 Challenge shape  
protocol\_version":"1.0", "candidate\_id": "CAND-017",

"challenge\_id": "CH-7e1c4a42", "command": "PROCESS\_EVENTS",

"sent\_at":"2026-10-09T10:45:00Z",

"expires\_at":"2026-10-09T10:45:152",

"events":\[{"source\_id": "LINE-01", "event\_id": "EV-101", "type":"COUNT", "quantity":5, "target\_event\_id": null, "event\_time": "2026-10-09T10:30:00Z" }\]  
}

8.2 Must-do behavior

1. Validate protocol version, candidate\_id, challenge\_id, PROCESS EVENTS command, expiry and events collection. Reject expired/mismatched challenges before processing events.

2\. Use one shared event service and PostgreSQL data model for MQTT and REST. MQTT candidate\_id is envelope metadata, not part of event identity.

3\. Publish a COMPLETED response with ordered event results and the current six-field state. An invalid item may be REJECTED while challenge status is COMPLETED.

4\. Persist each challenge jd and response. Same ID \+ same challenge body returns the original response without processing again. Same ID changed body returns FAILED/CHALLENGE CONFLICT.

5\. Publish before expires at; use stable failure codes such as VALIDATION ERROR, CANDIDATE MISMATCH, UNSUPPORTED PROTOCOL, CHALLENGE EXPIRED, CHALLENGE CONFLICT, INTERNAL ERROR

6\. Publish ONLINE after subscription, HEARTBEAT at least every 30 seconds, and OFFLINE as MQTT last will where supported. Reconnect with backoff and resubscribe after a disconnect.

8.3 Response example

{ "protocol version":"1.0", "candidate\_id": "CAND-017",

"challenge\_id": "CH-7e1c4a42", "status":"COMPLETED".

"processed\_at":"2026-10-09T10:45:022",

"results": \[{"event\_id": "EV-101", "status": "ACCEPTED"}\], "state":"net\_total": 5, "processed\_events": 1, "pending ack":1,

"unresolved": 0, "duplicates": 0, "conflicts":0}

A challenge-level failure uses status FAILED plus error code and message. A PUBACK only confirms transport delivery, the simulator must receive a correct response with a matching challenge ID and state.

9 Frontend: show the working product

Build one responsive dashboard page connected to the real backend. The examiner must be able to see how a factory

user would use it, not just inspect your APIs in Postman.

Allow input of one JSON event or a JSON event array, submission and result messages.

Show six indicators: net total, processed events, pending acknowledgement, unresolved references, duplicates and conflicts.

Include a Pending/Exceptions table switch with useful event fields, status and reason.

Allow selecting one or multiple pending events, submitting acknowledgement and refreshing visible data.

Show MQTT connectivity, assigned candidate ID, last challenge ID/time, last response status, challenge counts and last error.

Handle loading, success, empty, invalid input and API failure states; use real backend values only.

Keep the page usable on a typical laptop and a narrow mobile viewport.

Factory workflow note: VOID messages are correction records and should be automatically marked acknowledged whe they complete. The Pending table should show COUNT events for supervisor review, not VOID events.

9.1 Minimum examiner demo

1. Open the dashboard and explain who uses it and why.

2\. Send COUNT \+5 to LINE-01; show the updated state from PostgreSQL.

3\. Send the same event again; show duplicate evidence without extra production.

4\. Send a VOID before its target, then the target COUNT; show automatic resolution.

5\. Choose pending rows, acknowledge them, and show the updated pending state.

6\. Show a real MQTT challenge and correlated response on the dashboard; show an error or exception state.

10 Automated tests and review

Write at least five runnable automated tests: COUNT and total; identical duplicate without double counting VOID before-COUNT resolution; repeated acknowledgement; and repeated MQTT challenge without repeated event processing. Extra reliability credit is available for conflict handling, concurrent requests, mixed batches, restart recovery and reconnect.

In review, explain: What is your entity modet? Which function owns a COUNT? Why do REST and MQTT call the same service? Where are database transactions used? What changes to split the MQTT, event and query modules into services later? Trace one challenge from reception to stored state and published response.

The examiner may ask you to change one rule in one function and demonstrate that the other modules still work. A correct explanation and a working change are more valuable than complicated code you cannot explain.

11 Submission and documentation

Runnable backend and frontend, required migrations for PostgreSQL, three REST APIs and a working MQTT worker.

At least five automated tests and a GitHub repository with at least three meaningful commits.

README.md with exact setup, PostgreSQL initialization, run commands, tests, all REST request examples, MQTT topics and sample flow.

TECHNICAL EXPLANATION.md with entity model, module/function boundaries, transaction and duplicate strategy.

pending VOID resolution, restart behavior, future microservice migration and known assumptions.

AI\_USAGE.md plus the required Al conversation record; env.example without real secrets.

Screenshots of the working dashboard, REST API tests and successful MQTT challenge; clean source ZIP without dependencies, caches or credentials.

Submit everything through the provided Google Form on time.

12 Assessment criteria

Area

Marks

Evidence

Understanding and clarification

10

Useful questions, customer context, entities, correct assumptions

Backend and REST APIS

20

Three endpoints, validation and consistent behavior

PostgreSQL and business logic

18

Persistent data, transactions, COUNT/VOID correctness

MQTT integration

17

Topics, correlation, shared logic, replay safety and response.

Frontend integration

12

Working dashboard, ACK flow and MQTT visibility

Reliability and edge cases

10

Concurrent delivery, retries, pending resolution and restart

Automated tests

5

Five required tests with meaningful assertions

Git and documentation

4

Commits, README and reproducible commands

Code ownership and architecture

4

Modular functions, explanation and live walkthrough

100

TOTAL

13 Optional bonus / final check

After all mandatory items work, you may add a proto contract and demonstrate Protocol Buffers serialization for up to three bonus marks. Existing JSON contracts must continue working. Explain field numbering and compatibility.

Before submission: Can another engineer run your README, open the frontend, send REST and MQTT events, restart the server, and still see accurate PostgreSQL-backed state? Can you explain every important function? END OF CANDIDATE ASSESSMENT  
