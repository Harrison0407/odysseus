# ADR 0008: MarketMatch Operations & Reliability Foundations V1

- Status: Accepted
- Date: 2026-07-21
- Contract version: `marketmatch-operations-reliability-v1`
- Default policy version: `marketmatch-operations-policy-v1`

## Context and repository evidence

MarketMatch needs a tenant-safe vocabulary for configuration, feature rollout,
runtime jobs, delivery intent, health, observability, backups, restore planning,
and rollback decisions before those concerns become durable platform services.
ADRs 0001–0007 govern Authority, Evidence, Operational Context, Truth &
Accountability, Work Orchestration, Tenancy & Membership, and Billing. This ADR
adds a pure contract layer and changes none of those implementations.

The named MarketMatch Master Context, Foundation and Core Roadmap, Platform
Vision and Architecture Blueprint, and separate current-state/capability
documents are not committed in this repository. `ROADMAP.md`, ADRs 0001–0007,
code, tests, `docs/backup-restore.md`, and `THREAT_MODEL.md` are the available
evidence.

Current implementation is heterogeneous:

- `src/config.py`, `src/constants.py`, and `src/settings.py` load Pydantic,
  environment, constant, and JSON settings. Validation is partly centralized
  and partly local. `settings.json` includes provider keys alongside ordinary
  settings; `src/settings_scrub.py` masks secret-shaped fields for restricted
  responses. Environment variables are runtime inputs, not business records.
- `features.json` is a global boolean mapping merged with
  `DEFAULT_FEATURES`. It has no immutable versions, exact Tenant Context,
  percentage rollout, Authority provenance, or conflict detection.
- `src/secret_storage.py` encrypts selected database secrets with a local Fernet
  key. It is not a central vault. Some current logging uses raw exception text,
  so this V1 never adapts logs into a trusted ledger or Secret Reference.
- `ScheduledTask` and `TaskRun` persist scheduler definitions and executions.
  TaskRun uses literal `queued`, `running`, `success`, `error`, `aborted`, and
  `skipped` states and can store result, error, steps, model, and token counts.
  `src/bg_jobs.py` has restart-safe detached-process state, a hard runtime cap,
  cancellation, output files, and follow-up retry behavior. Scheduler in-memory
  guards and the event bus provide bounded duplicate avoidance, but there is no
  canonical tenant-scoped idempotency record, distributed lease, durable queue
  acknowledgement, or dead-letter queue.
- Notification behavior exists through browser reminders, SMTP, ntfy, and
  generic webhooks. Scheduled tasks expose notification switches. Those paths
  send real messages and retain destinations/bodies, so this kernel models only
  intent and delivery attempt metadata.
- `/api/health` is liveness. `/api/ready` checks database and data-directory
  writability. `src/service_health.py` probes ChromaDB, SearXNG, ntfy, email, and
  model providers with bounded timeouts and sanitizes returned URLs and error
  categories. Existing logging is standard-library, mostly free-form; no
  repository-wide correlation-ID or immutable operational-signal ledger exists.
- `scripts/odysseus-backup` snapshots `data/`, uses SQLite's backup API, verifies
  archive structure/integrity, performs destructive restore only with `--yes`,
  and preserves the prior data directory as a rollback stash. The backup
  includes secret material and does not include Docker ChromaDB. A successful
  archive walk is not independent restored-state verification. No automatic
  schedule, off-site guarantee, RPO/RTO enforcement, restore authorization,
  or production rollback service exists.

## Decision and boundary

Add `src/marketmatch_operations.py`, a pure immutable Operations & Reliability
V1 kernel. It takes all clocks, contexts, policies, decisions, observations,
and histories as inputs. It performs no environment or secret read, I/O,
persistence, scheduling, sleeping, queue operation, network call, health probe,
notification send, backup, restore, rollback, logging, billing calculation, or
model invocation.

No production route imports the kernel. No table, migration, JSON registry,
browser storage, external dependency, or runtime-data write is introduced.
Existing routes, authentication, privileges, Documents, Library, Capture,
uploads, STT, long analysis, RAG, providers, local models, Tasks, TaskRuns,
scheduler, event bus, background jobs, health endpoints, notification senders,
and backup script remain authoritative for their current behavior.

## Configuration versus secrets

`ConfigurationDefinition` describes a stable key, closed value type, explicit
scope kind, required/default presence, derived secret-bearing indicator,
validation code, optional enum domain, effective interval, classification,
policy, and contract version. V1 value types are `BOOLEAN`, `INTEGER`,
`DECIMAL`, `STRING`, `ENUM`, `DURATION`, `BYTE_SIZE`, and
`SECRET_REFERENCE`. Validators are stable codes, never executable callables.

`ConfigurationSnapshot` is one factory-validated value for an exact
`OperationsScope`. Scalar type, bounds, definition compatibility, chronology,
scope, policy, and version are validated. Active overlap is a conflict; future
and expired snapshots do not silently win. There is no caller-supplied
`validated` flag, arbitrary metadata, environment dump, or mutable registry.

Configuration is not a secret store. A secret-bearing definition accepts only
a valid active `SecretReference` for the exact scope. It never accepts a string
secret. `SecretReference` contains ID, provider/storage-class code, purpose,
exact tenant or explicitly global scope, rotation reference, literal status,
creation/expiry, policy, classification, and version. It has no value, API key,
password, token, cookie, private key, certificate body, connection string,
header, filesystem contents, or lookup behavior. Viewing its metadata requires
the separate `operations.secret_reference.view` capability.

## Feature flags versus Authority

`FeatureFlagDefinition` is an immutable version with exact scope, status,
mode, default, effective interval, supersession reference, policy, and authentic
`operations.feature.admin` provenance. Modes are `OFF`, `ON`, `EXACT_SCOPE`,
`PERCENTAGE`, and `ALLOWLIST`. Missing Tenant dimensions are never wildcards.
Conflicting active versions reject and future versions do not activate early.

Percentage rollout hashes stable, safe identifiers and policy version with
SHA-256 and maps the first eight digest bytes into 10,000 basis-point buckets;
it never uses process-randomized Python `hash()`. The digest is only an internal
deterministic calculation. Factory-sealed `FeatureEvaluation` always states
`authority_granted=False` and `entitlement_granted=False`. An enabled flag can
neither authenticate a principal nor grant membership, visibility, approval,
execution, capability, or commercial entitlement.

## Job, Request, Run, and Attempt

An operational `JobDefinition` names a bounded runtime job type/version,
required scope, timeout, retry policy, idempotency-principal rule, cancellation
policy, lifecycle status, and policy. It is not a Work Item.

`JobRequest` binds the Job to exact Tenant Context or explicit global scope,
requesting principal, optional Work ID, safe input reference, requested time,
idempotency key, policy, and authentic `operations.job.request` provenance. It
has no arbitrary payload. A Work reference does not make Job status into Work
status; Job success does not prove Work acceptance.

`JobRun` distinguishes `REQUESTED`, `ACCEPTED`, `RUNNING`, `SUCCEEDED`,
`FAILED`, `TIMED_OUT`, `CANCELLED`, `ABANDONED`, `DEAD_LETTERED`, and
`INVALIDATED`. Transitions and chronology are closed. Terminal history cannot
be rewritten. Cancellation requires a stable cancellation reference. Timeout,
failure, and cancellation never collapse into one label.

`JobAttempt` binds a run, gap-free ordinal, start/finish, literal outcome,
stable error code, derived retry permission, and optional Billing Usage Event
reference. It stores no result or exception. Duplicate IDs/ordinals reject.
One Usage Event ID cannot be silently attributed twice across retry attempts.
Usage attribution remains Billing input and this kernel calculates no Cost or
Customer Charge.

## Retries, idempotency, leases, and dead-letter disposition

`RetryPolicy` bounds attempts to 64, delay, elapsed deadline, retryable stable
reason codes, and fixed or exponential backoff. V1 jitter is literal `NONE` so
evaluation is deterministic. Delay is capped before datetime construction;
there is no sleeping. Unknown failure reasons, success, cancellation, attempt
exhaustion, or missed deadlines deny retry. A timeout is retryable only when
its supplied stable reason is explicitly allowed.

Idempotency evaluation compares exact Job scope, Tenant Context, key, principal
when policy requires it, safe input/Work reference, and policy. A compatible
prior request becomes an explicit duplicate; conflicting history or collision
fails closed. Different Tenants never collide. No process-global registry is
used and a caller cannot attach an unrelated completed result.

Abandonment evaluation is possible only with explicit heartbeat, lease expiry,
and supplied `as_of`. Missing lease data remains `lease_unknown`, not
abandoned. Dead-letter evaluation is a disposition decision only; it creates
no queue and schedules nothing.

## Notification intent versus delivery

`NotificationIntent` retains exact scope, type, recipient reference, safe
template/version reference, source record reference, request time, priority,
classification, policy, and authentic `operations.notification.request`
provenance. `DeliveryAttempt` separately records `IN_APP`, `EMAIL`, or
`WEBHOOK`, ordinal, time, literal outcome, acknowledgement code, safe provider
reference, and stable error code. These channels match committed behavior; SMS
and push are not claimed.

Neither record contains a raw destination, message body, credential, token, or
linked protected content. Notification visibility does not authorize its
source Event, Work, Job, or Evidence. The kernel never sends.

## Health, dependencies, operational signals, and metrics

`HealthCheckDefinition` binds a dependency/check type to expected interval,
stale threshold, criticality, scope, and policy. `HealthObservation` contains a
safe signal ID, check ID, observation time, optional bounded finite latency,
literal outcome, stable reason, and bounded scalar measurements.

Factory-sealed `ServiceHealth` derives `HEALTHY`, `DEGRADED`, `UNAVAILABLE`, or
`UNKNOWN`. A stale observation cannot remain healthy. A missing critical check
is unavailable; an explicitly stale critical result is unknown. A noncritical
failure degrades rather than silently reporting total availability. Health
grants no Authority.

`OperationalSignal` models bounded operational facts such as job acceptance,
failure/retry, notification attempt, dependency degradation, backup recording,
verification failure, restore attempt, and rollback proposal. Measurements are
finite bounded integer/Decimal scalars. Prompts, model output, transcripts,
documents, filenames, secrets, cookies, tokens, headers, request bodies,
database rows, credentials, financial margins, paths, and uncontrolled
exceptions are not representable. `official_event=False` makes the separation
from Truth & Accountability explicit.

## Backup policy, record, and verification

`BackupPolicy` expresses exact scope/resource, trigger description, retention,
encryption and verification requirements, optional explicit RPO/RTO,
effective interval, policy, and authentic administration provenance. A policy
does not claim that a schedule is running.

`BackupRecord` models requested, in-progress, completed, failed, invalidated,
or expired history. Completed records require real supplied artifact reference,
chronology, optional real SHA-256 digest/size, encryption status, producer,
scope, resource, classification, and policy. Artifact references are bounded
opaque codes, never filesystem paths. Raw bytes and keys are impossible.
`verified=False` is sealed into every Backup Record: completion never implies
verification.

`BackupVerification` is a distinct factory-sealed record created only for a
completed backup and after completion. Integrity, compatibility, and
restorability must all literally be `PASSED` before `verified=True` is derived.
Unknown or incomplete checks cannot pass. Verification stores only a bounded
Evidence reference and does not restore data. Mutation is detected by record
integrity validation.

## Restore completion versus verification and rollback authorization

`RestorePlan` requires the exact completed Backup Record and matching authentic
passed Backup Verification, exact compatible Tenant/resource scope, an expiry,
expected Rollback Point, `operations.restore.request` Authority, and a committed
Truth Approval when policy requires one. The Approval targets the canonical
Project Operational Context, carries the distinct `approval.restore`
capability, and binds the exact plan ID through its closed conditions code.
Possessing a backup grants nothing.

`RestoreAttempt` models an attempt outcome separately from validation outcome.
`COMPLETED` with `NOT_PERFORMED` remains possible and explicitly does not mean
validated. No filesystem or database mutation occurs.

`RollbackPoint` records exact scope/resource, one source backup or deployment
reference, integrity, compatibility, policy, and authentic
`operations.rollback.propose` provenance. Pure evaluation permits proposal
only for valid integrity and known compatibility; a backup-sourced point also
requires the matching passed Backup Verification. Evaluation requires Approval when
policy says so. Rollback Approval carries `approval.rollback` and binds the
exact Rollback Point ID. It always reports `executed=False`; no automatic rollback or
current-state mutation exists and original history remains.

## Incident scope

A generic Incident contract is deferred. Current code has failures, health
reports, scheduler/background state, and UI notifications, but no authoritative
tenant-scoped incident lifecycle, severity policy, responsible Party binding,
or acknowledgement model. Inventing one would exceed repository evidence.

## Authority, Tenancy, Context, Truth, Evidence, Work, and Billing

The kernel imports the committed `AuthorizationDecision` and its sealed
projection validator; it contains no duplicate capability evaluator. Exact
actor, resource type/ID, full scope, policy, version, and capability must match.
Capabilities remain separate for configuration view/admin, feature view/admin,
secret reference view/admin, Job request/cancel/status, notification
request/status, health view, backup-policy administration, backup metadata,
verification, restore request/approval/view, rollback proposal/view, and
protected operational metrics. This V1 implements only capabilities used by
its current factories/projectors and does not manufacture grants.

Tenant records reuse exact committed `TenantContext`: Tenant, Organization,
Product, Workspace, Project, owner, classification, policy, and version.
Aliases reject. Tenant is never inferred from hostname, directory, environment,
email, owner string, database name, or process. Global scope must be literal and
cannot carry a Tenant. Cross-Tenant records and derived views reject.

Operational Signal is not a Truth Event. Decision/Approval may authorize
restore/rollback without executing them. Evidence is referenced, never
embedded, and visibility remains separately authorized. A backup digest is not
an Evidence attestation. Job may support Work but does not replace Work,
Attempt, completion claim, or acceptance. Usage references do not calculate
Cost/Charge; retries cannot silently reuse one Usage Event reference.

## Safe projections, audit, and derived views

Projectors accept only authentic, integrity-valid records and exact ADR 0001
decisions bound to record type/ID, full scope, policy, version, and a distinct
view capability. Output is a new scalar-only mapping. Nested mutable values,
arbitrary mappings, raw secret values, environment dumps, raw exceptions,
notification bodies/destinations, backup artifact references/digests, and
hidden dependency measurements are absent. Feature effectiveness, health,
retry, backup verification, and rollback eligibility come only from sealed
factories/evaluators.

Safe audit uses a closed action/outcome vocabulary and contains only bounded
record IDs/types, stable codes, Tenant ID, service/capability, policy/version,
canonical UTC timestamp, and safe correlation ID. It executes nothing and
grants nothing.

Derived views cover effective flags, configuration, active/failed/retryable/
abandoned/dead-letter jobs, pending/failed notifications, service health,
verified/unverified backups, restore plans, and rollback candidates. Every
source must validate and project under an allowed decision; denial blocks the
entire view rather than becoming silent omission. Scope and policy conflict
reject. IDs are deterministically sorted. A view is explicitly not an Event,
Authority decision, backup, restore, rollback, queue, or execution.

## Compatibility adapters

The `features.json` adapter accepts only one literal boolean with an explicitly
global Operations Scope and authentic feature-admin decision. It does not infer
a Tenant or import arbitrary settings.

The TaskRun adapter accepts only literal status and timestamps after the caller
supplies a canonical authorized Job Request and safe operations Run ID. Raw
result, error, steps, model, token count, owner, database ID, and unknown status
cause `UNSUPPORTED_ADAPTER`. It fabricates neither Tenant Context nor
idempotency. Existing TaskRun/routes remain authoritative. Application logs and
backup files are not adapted into canonical Signals, Backups, or Verification.

## Stable-code glossary

- IDs: `cfgdef1`, `cfgsnap1`, `flag1`, `feval1`, `secref1`, `job1`, `jreq1`,
  `jrun1`, `jatt1`, `nint1`, `datt1`, `hchk1`, `osig1`, `bpol1`, `brec1`,
  `bver1`, `rplan1`, `ratt1`, `rbp1`, and `oaudit1`.
- Job terminal reasons distinguish failure, timeout, cancellation, abandonment,
  dead-letter, and invalidation.
- Evaluation reasons include `no_active_flag`, `mode_off`, `mode_on`,
  `exact_scope`, `allowlist_match`, `allowlist_miss`, `percentage_match`,
  `percentage_miss`, `accepted`, `duplicate`, `collision`, `retry_allowed`,
  `attempt_limit`, `deadline_exceeded`, `lease_unknown`, `lease_expired`,
  `all_checks_healthy`, `critical_unavailable`, `critical_unknown`,
  `dependency_degraded`, `backup_not_verified`, `rollback_eligible`, and
  `approval_required`, plus the uppercase `OperationsErrorCode` values.

## Fictional generic examples

1. Fictional Tenant Alpha has an enabled analysis flag. Its operator still
   requires independent membership, entitlement when applicable, and Authority.
2. A fictional provider credential configuration points to
   `secref1:fictional:provider`; the credential value never enters the kernel.
3. A fictional Job times out with `worker.timeout`. Retry evaluation permits a
   bounded second Attempt without changing its related Work Item.
4. A fictional archive reaches `COMPLETED` but remains unverified until all
   three independent verification outcomes pass.
5. A fictional restore attempt completes extraction but reports validation
   `NOT_PERFORMED`; no dashboard may call it verified.

## Deferrals and limitations

Deferred: durable configuration/flag/secret-reference registries; secret vault;
queue backend and distributed leases; notification delivery; external metrics,
log aggregation, dashboards, tracing, formal SLOs and paging; backup scheduling
and execution; restore execution and independent restored-state validation;
deployment rollback; incident management; multi-region failover; operations
API/UI; and AI Control Plane or product-specific operations modules.

V1 does not claim secrets are centrally stored, feature flags are deployed,
notifications are delivered, monitoring is production-grade, backups run
automatically, restores are performed, formal SLOs exist, or multi-region
resilience exists. Locale and timezone may change presentation only; every
operational truth uses supplied canonical UTC.
