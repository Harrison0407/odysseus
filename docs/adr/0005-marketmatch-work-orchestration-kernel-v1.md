# ADR 0005: MarketMatch Human & AI Work Orchestration Kernel V1

- Status: Accepted
- Date: 2026-07-20
- Contract version: `marketmatch-work-orchestration-v1`
- Default policy version: `marketmatch-work-orchestration-policy-v1`

## Context

MarketMatch needs a stable vocabulary for requested work, explicit assignment,
execution attempts, retry, handoff, escalation, completion claims, and verified
completion. The committed Authority, Evidence, Operational Context, and Truth
& Accountability kernels already own authorization, artifacts, operational
subjects, Events, Decisions, Approvals, and safe accountability records.

The current application persists `ScheduledTask` and `TaskRun` rows. Tasks
contain owner, prompt/action, schedule or infrastructure-event trigger, status,
next/last run, endpoint and runtime selection, chaining, and notification
settings. TaskRun contains internal task ID, naive-UTC execution timestamps,
`running`/`success`/`error` status, result/error text, steps, and runtime name.
The task scheduler performs execution, timeouts, cancellation, notification,
and chaining. `src.event_bus` increments scheduler trigger counters; it is not
business-event provenance. `src.bg_jobs` persists detached process metadata,
commands, output paths, exit codes, timeout state, and cleanup files. Calendar
events and Cookbook actions schedule or initiate current runtime work.

These current systems do not consistently carry canonical MarketMatch scope,
Authority provenance, assignment acceptance, executor identity, Evidence,
Operational Context, Truth references, or immutable lifecycle history. Their
IDs and completed statuses are therefore not silently promoted to official
Work Items or verified completion.

## Decision

Add one pure, importable contract module. It has no database, filesystem,
network, queue, scheduler, notification, runtime-engine, workflow-engine,
provider, or public-route behavior. Existing Tasks and TaskRuns remain
authoritative for current application behavior.

## Contract boundaries

### Work Item versus Event

A Work Item represents requested and authorized work. An Event records what
occurred. Creation, assignment, start, block, completion claim, cancellation,
or verification may have linked Event IDs, but neither identity replaces the
other.

### Work Item versus Decision

A Decision may authorize, prioritize, cancel, or otherwise relate to work. A
Work Item is not that Decision and does not mutate it. Work completion never
manufactures a Decision.

### Assignment versus authority

An Assignment names an assigner and executor. It does not grant authority.
Creation requires an authentic `work.assign` Authority decision bound to the
assigner, Assignment ID, scope, policy, and version. Execution separately
requires `work.execute`. Visibility, assignment, execution, transition,
completion-claim, verification, escalation, cancellation, and audit authority
remain distinct.

### Executor kind versus executor identity

Executor identity is distinct from literal `HUMAN`, `AGENT`, `WORKFLOW`, and
`EXTERNAL_SERVICE` kinds. A human cannot carry automated provider provenance.
Automated executors require a real provider/engine identifier and version.
Credentials, cookies, sessions, headers, tokens, or credential-bearing URLs
are never executor references. Identity alone grants no capability.

### Work status versus business truth

Work status describes only the orchestration contract. `COMPLETED` does not
mean installation correctness, payment, domain acceptance, legal closure,
Evidence verification, or Approval. Status codes and work types are closed,
language-neutral enums; translated labels and product-specific states reject.

## Work Item contract

`WorkItem` uses a `wrk1:` identity, generic work type, exact Authority scope,
canonical Context IDs, requester, creation/readiness/deadline times, priority,
initial `DRAFT` or `READY` status, classification, policy, required execution
capabilities, bounded Evidence and Truth references, prior Work references,
and immutable completion criteria. It has no raw content, prompt, arbitrary
metadata, output, executor, or implicit assignment.

Supported V1 work types are `REVIEW`, `ANALYZE`, `TRANSCRIBE`, `EXTRACT`,
`CLASSIFY`, `VERIFY`, `RESEARCH`, `GENERATE`, and `GENERAL`. They reflect
generic current capabilities without importing DT Beach lifecycle states.

## Assignment and Handoff

Assignments use `asg1:` identities and literal `OFFERED`, `ASSIGNED`,
`ACCEPTED`, `DECLINED`, `SUPERSEDED`, `WITHDRAWN`, `EXPIRED`, or `INVALIDATED`
states. Acceptance is explicit; silence, booleans, numbers, and truthy strings
are not acceptance. `ACCEPTED` and `DECLINED` additionally retain a separate
Authority decision bound to the assignee and response capability; assignment
authority cannot manufacture the response. Reassignment creates a new Assignment with a
`supersedes_assignment_id`; earlier history remains intact.

A Handoff uses a separate `hnd1:` identity and source/target Assignments.
Assignment versus Handoff remains explicit: a Handoff does not rewrite either
Assignment, grant execution authority, or imply target acceptance. Self,
cross-scope, duplicate-active, and cyclic handoffs reject. An accepted Handoff
retains a separate decision bound to the target executor and
`work.handoff.accept`.

## Status transitions and cancellation

Transitions use `trn1:` identities and an explicit from/to status, actor,
canonical time, exact scope, policy, Authority provenance, and optional linked
Event, Decision, or Approval. Allowed transitions are a bounded V1 table.
History is sorted deterministically; each source status must equal the prior
materialized status and chronology is strict.

A cancellation request versus effective cancellation remains separate. V1
uses a linked Decision as the request/authority provenance; an effective
`CANCELLED` transition additionally requires `work.transition.cancelled`.
Cancellation preserves attempts and does not claim that an external process
was terminated. Completed work can only move to explicit invalidation, not
ordinary cancellation.

## Execution attempt versus retry

Each `ExecutionAttempt` has an `atm1:` identity, Work and Assignment IDs,
executor, sequence, start/end times, literal result status, safe Evidence,
Context, Event, Decision, and Approval references, policy, Authority
provenance, and bounded outcome code. Attempt versus retry is immutable: retry
creates the next Attempt and never edits failed history. Timeout is distinct
from cancellation; no prompt, raw response, stdout, stderr, or exception text
is stored.

`RetryPolicy` is a pure predicate for attempt bounds, literal retryable reason
codes, optional backoff policy code, deadline, cancellation, executor
continuity, and optional prior Approval. It starts no timer or queue.

## Dependencies and blockers

Dependency versus hierarchy remains separate. Directional `REQUIRES`,
`BLOCKED_BY`, and `FOLLOWS` edges are cycle-free. `DUPLICATES`, `SUPERSEDES`,
and `RELATED_TO` remain associations. Missing endpoints, self-relations,
duplicates, collection overflow, and cross-scope edges reject. Dependencies do
not grant authority, reveal hidden work, or complete dependent work.

## Escalation

Escalation versus Approval and assignment remains separate. `Escalation` uses
an `esc1:` identity, raiser, protected target reference/capability, stable
severity and reason, chronology, status, scope, and Authority provenance. It
does not approve, reassign, expose, or complete work. Duplicate unresolved
escalations reject.

## Completion criteria and claims

Completion criteria are bounded data, not executable expressions or callbacks.
Codes cover result, Evidence, Event, Decision, Approval, independent verifier,
dependencies, and executor declaration requirements. Empty criteria explicitly
remain unsatisfied.

A Completion Claim uses a `clm1:` identity and references a successful Attempt,
Assignment, executor, result Evidence/Events, and assertion Decision/Approval
IDs. Completion Claim versus verified completion remains explicit: a claim is
only an executor declaration and does not mutate Work or equal approval.

Approval verification uses committed Truth & Accountability records. Approval
must be literal `APPROVED`, correct-scope, correct-policy, sufficiently
restrictive, and capability-compatible. Rejected, expired, withdrawn,
invalidated, malformed, or self-issued Approval cannot satisfy independent
verification unless explicit policy permits self-verification. The Approval
must target a canonical Context of the Work Item or a Decision explicitly
asserted by the claim; an unrelated same-scope Approval cannot satisfy work.
Evidence
attestation is not Approval. Verification preserves the claim and Approval.

## Evidence, Context, and Truth integration

Only Evidence IDs are stored; bytes, transcripts, document content, filenames,
and arbitrary provenance are absent. Visibility of Work, Assignment, or Attempt
does not authorize Evidence. Derived views fail if Evidence/source authority is
denied or scope/policy/classification conflicts.

Operational Context targeting uses canonical `ctx1:` IDs. Names, aliases,
database rows, PO/BL/container numbers, and hierarchy cannot grant work
authority or replace identity. Cross-organization, product, workspace, project,
or owner references fail closed.

Truth & Accountability links retain separate `evt1:`, `dec1:`, `apr1:`, and
audit identities. A transition may reference an Event without becoming one; an
Approval may verify a claim without becoming a transition. No accountability
contract is duplicated.

## Immutable history and lifecycle validation

Collection validation bounds every registry; checks unique typed IDs,
reference existence, exact scope/policy/classification, chronology, transition
validity, assignment supersession, active-assignment conflicts, attempt
sequence/executor/acceptance, dependency and Handoff cycles, unresolved
blockers, duplicate escalations and claims, and source integrity. Conflicts are
reported through stable codes and never automatically resolved.

## Safe projection and safe audit

All record projectors are flat-only and require authentic Authority decisions
bound to exact record ID, type, scope, policy, and version. Output is a new
scalar mapping. Hidden requester, assigner, executor, target, Evidence, Context,
result, reason, and unknown fields remain absent. Mapping callers cannot supply
computed current status, completion state, or counts. Hostile exceptions become
fixed errors without rejected values.

Safe work audit uses `waud1:` IDs and bounded action, record, outcome, reason,
policy, scope-reference, timestamp, and correlation codes. Executor kind is
included only when explicitly authorized. Audit contains no raw Evidence,
transcript, document, prompt, generated response, work note, result text,
stdout, stderr, filename, hidden identity, supplier/factory identity, address,
cost, margin, credential, cookie, session, token, header, request body,
arbitrary metadata, or exception text. Audit grants no authority.

## Derived workload views

Pure builders support assigned, active, blocked, overdue, waiting-Approval,
completion-claimed, failed, and unresolved-escalation views. Every supplied
record must validate and be authorized before filtering; denied or malformed
records cannot be silently omitted. Authority inheritance enforces compatible
scope/policy and the most restrictive classification/field intersection.
Ordering uses canonical Work IDs. A derived workload view is not an official
Work Item, Event, Decision, or Approval.

## Locale and timezone independence

Locale changes labels only. Timezone changes display only. Neither changes
IDs, enums, assignment, executor kind, status, dependency, authority, canonical
UTC chronology, policy, Evidence links, or audit meaning.

## Current implementation versus target state

Current Tasks, TaskRuns, calendar entries, event-bus triggers, background jobs,
Cookbook actions, analysis drafts, Documents, and notifications retain their
existing runtime semantics. The target state may add narrowly proven adapters
and persistence after canonical scope, actor, assignment, attempt chronology,
Evidence, Context, Authority, rollback, and non-fabrication are established.

## Compatibility adapters

No current adapter is approved. `adapt_current_task` fails with
`UNSUPPORTED_ADAPTER`: current task IDs, owner strings, status, result/error
text, and runtime fields cannot honestly produce canonical scope, Assignment,
acceptance, Evidence, Context, or verified completion. No application log or
infrastructure event becomes official work history.

## Persistence and runtime-engine deferral

V1 adds no registry, database model, migration, queue, worker, scheduler,
notification, automatic retry, event sourcing, materialized dashboard, public
API, UI, browser storage, JSON registry, or runtime-data write. LangGraph,
DeerFlow, CrewAI, runtime execution, provider routing, and the AI Control Plane
are deferred. No route imports the kernel.
Public route and UI integration are explicitly deferred.

## Stable code glossary

- IDs: `wrk1`, `asg1`, `atm1`, `trn1`, `hnd1`, `esc1`, `clm1`, `waud1`.
- Work priority: `LOW`, `NORMAL`, `HIGH`, `CRITICAL`.
- Work status: `DRAFT`, `READY`, `ASSIGNED`, `ACCEPTED`, `IN_PROGRESS`,
  `BLOCKED`, `WAITING`, `WAITING_APPROVAL`, `COMPLETION_CLAIMED`, `COMPLETED`,
  `FAILED`, `CANCELLED`, `EXPIRED`, `INVALIDATED`.
- Attempt status: `STARTED`, `SUCCEEDED`, `FAILED`, `CANCELLED`, `TIMED_OUT`,
  `INTERRUPTED`, `INVALIDATED`.
- Escalation severity: `INFO`, `WARNING`, `HIGH`, `CRITICAL`.
- Claim outcome: `CLAIMED`, `PARTIAL`.

## Reason-code glossary

Stable reasons include `INVALID_IDENTIFIER`, `INVALID_EXECUTOR`,
`INVALID_WORK_ITEM`, `INVALID_ASSIGNMENT`, `INVALID_TRANSITION`,
`INVALID_ATTEMPT`, `INVALID_RETRY_POLICY`, `INVALID_DEPENDENCY`,
`INVALID_HANDOFF`, `INVALID_ESCALATION`, `INVALID_COMPLETION_CLAIM`,
`INVALID_CRITERIA`, `INVALID_AUTHORITY_DECISION`, `INVALID_PROJECTION`,
`INVALID_AUDIT`, `INVALID_VERSION`, `INVALID_TIMESTAMP`, `INVALID_SCOPE`,
`MISSING_REFERENCE`, `DUPLICATE_IDENTIFIER`, `SCOPE_CONFLICT`,
`POLICY_CONFLICT`, `VISIBILITY_CONFLICT`, `CHRONOLOGY_CONFLICT`,
`INVALID_STATUS_TRANSITION`, `DEPENDENCY_CYCLE`, `HANDOFF_CYCLE`,
`DUPLICATE_ACTIVE_ASSIGNMENT`, `DUPLICATE_ACTIVE_ATTEMPT`,
`DUPLICATE_UNRESOLVED_ESCALATION`, `DUPLICATE_COMPLETION_CLAIM`,
`ASSIGNMENT_NOT_ACCEPTED`, `EXECUTOR_MISMATCH`, `RETRY_NOT_ALLOWED`,
`CRITERIA_UNSATISFIED`, `SELF_VERIFICATION_DENIED`,
`SOURCE_NOT_AUTHORIZED`, `SOURCE_SCOPE_CONFLICT`,
`SOURCE_POLICY_CONFLICT`, `COLLECTION_LIMIT_EXCEEDED`, and
`UNSUPPORTED_ADAPTER`.

## Fictional example

Fictional Project Alpha creates `wrk1:review:alpha` to review an authorized
document. An authorized coordinator records `asg1:worker:alpha`; the human
explicitly accepts. `atm1:run:alpha` cites its Evidence input and produces a
new Evidence result. `clm1:result:alpha` claims completion. A different
authorized verifier records an Approval. Only then may validated transition
history derive `COMPLETED`; none of those records determines domain acceptance
or legal truth.

## Limitations

This milestone does not execute humans or automated executors, route providers,
start queues, retry automatically, terminate workers, send notifications,
implement DT Beach or BTC workflows, create Asset Passports, or determine legal
or operational acceptance.
