# ADR 0007: MarketMatch Billing, Entitlements & Usage Attribution Kernel V1

- Status: Accepted
- Date: 2026-07-20
- Contract version: `marketmatch-billing-entitlements-usage-v1`
- Default policy version: `marketmatch-billing-policy-v1`

## Context and current state

MarketMatch needs a tenant-safe vocabulary for commercial capability access and
honest execution attribution before durable billing is designed. The current
application has no authoritative Billing Profile, Plan, Plan Version,
Entitlement, Allowance, Credit ledger, Provider Cost, Customer Charge, Budget,
invoice, or accounting model. `Session.total_input_tokens`,
`Session.total_output_tokens`, streamed provider usage, `TaskRun.tokens_used`,
TaskRun timestamps, upload byte counts, and STT duration are useful runtime
measurements, but lack the exact Tenant Context, commercial policy, Authority,
and immutable provenance required to become official billing records.

`chatgpt_subscription` names an OAuth-backed execution provider. It is not a
MarketMatch Plan or customer subscription. Existing privileges are Authority
inputs, not commercial Entitlements. Provider endpoints contain credentials
and runtime configuration, not pricing. No provider Cost is known merely from
a provider name or a public price list.

ADRs 0001–0006 remain authoritative for scope and field projection, Evidence,
Operational Context, Events/Decisions/Approvals/audit, Work and Attempts, and
Tenant Context and Membership. This ADR adds a pure contract layer and changes
none of those implementations.

The named MarketMatch Master Context, Foundation and Core Roadmap, and Platform
Vision and Architecture Blueprint are not present in this repository. The
committed ADRs, `ROADMAP.md`, `specs/architecture-runtime-inventory.md`, code,
and tests are the available governing evidence.

## Decision

Add one pure, dependency-free Billing, Entitlements & Usage Attribution V1
module. It uses exact supplied values and immutable or effectively immutable
records. It performs no metering, provider lookup, model call, payment,
currency conversion, persistence, database access, filesystem access, route
integration, or runtime mutation.

Tenant-specific Plans are the smallest fail-closed V1 rule. A Plan is stable
identity and presentation; an immutable Plan Version owns effective dates and
references Entitlements, Allowances, pricing policies, and thresholds. A new
commercial configuration creates a new Plan Version. Overlapping active
versions are conflicts, never newest-record wins.

## Entitlement, membership, and Authority

Membership establishes a principal relationship to a Tenant. Entitlement says
that an exact Tenant Context may consume one named commercial capability.
Authority says whether a principal may perform a protected action. These are
three independent gates. Membership never grants a capability, Entitlement
never authorizes execution, and Authority never manufactures Entitlement.

An Entitlement binds the full Tenant Context: Tenant, Organization, Product,
Workspace, Project, owner, classification, and policy. Omitted dimensions are
not wildcards. Only literal `ACTIVE` records inside their effective interval
satisfy evaluation. Conflicting active Entitlements return `CONFLICT`. The
factory-sealed result always reports `authority_granted=False`.

Entitlement history is immutable. Suspension, revocation, expiry,
invalidation, replacement, and supersession are new records or future explicit
lineage; the source is not rewritten.

## Plan, allowance, and credits

Plan and Plan Version are distinct. A future Plan Version does not activate
early. Plan inclusion grants neither Authority nor Membership.

Allowance is a non-monetary included quantity for a capability, unit, exact
Tenant Context, canonical UTC period, and Plan Version. V1 supports `ONE_TIME`,
`MONTHLY`, and `CONTRACT_PERIOD`, but never infers period boundaries from a
locale or display timezone. Rollover is literal `NONE`; there is no silent
rollover. Consumption is derived from validated actual Usage Events and never
mutates the Allowance.

Credits are not currency. `EXPLORATION` and `PREPAID` accounts declare one usage
unit. Immutable `GRANT`, `PURCHASE`, `CONSUMPTION`, `REVERSAL`, `EXPIRATION`,
and `ADJUSTMENT` movements derive a balance. There is no mutable balance source
of truth. Duplicate movements, unit mismatch, expired grants, and consumption
beyond balance fail closed.

## Exact Money and usage quantity

Money contains a Python `Decimal` and explicit closed V1 currency code (`USD`,
`DOP`, or `EUR`). Binary floats, booleans, NaN, Infinity, negative base values,
more than six fractional digits, and excessive magnitude reject. Values are
normalized to six decimal places with round-half-even for deterministic
serialization and calculations. Arithmetic across currencies rejects; V1 does
not convert currencies.

Usage Quantity is an exact nonnegative Decimal and one evidence-backed unit:
`TOKEN` (provider/model reports), `MILLISECOND` (STT and duration), `BYTE`
(upload/media size), or `EXECUTION` (explicit run count). It has the same
bounded six-place precision. Units never convert implicitly.

## Pricing policy and margin

The closed mechanisms are `INCLUDED_ALLOWANCE`, `EXPLORATION_CREDIT`,
`PREPAID_CREDIT`, `PASS_THROUGH`, `PASS_THROUGH_WITH_MARGIN`,
`ENTERPRISE_CHARGEBACK`, and `NO_CHARGE`. One Usage Event is evaluated under
one policy reference. Credits do not become Money, allowance does not become a
credit balance, and no-charge does not assert Provider Cost is zero.

Pass-through and enterprise chargeback require known Provider Cost.
Pass-through-with-margin additionally requires exactly one explicit margin:
fixed Money in the Provider Cost currency or a bounded nonnegative percentage
of Provider Cost. Calculation uses exact Decimal and a single documented
round-half-even normalization. There is no tax, discount, currency conversion,
or hidden compounding.

## Usage Estimate versus Usage Event

A Usage Estimate is an expiring pre-execution record. It may contain explicit
estimated usage, duration, Cost, Charge, provider/engine, Work, threshold, and
Approval references. Absent values remain absent. It cannot become actual by a
status mutation.

A Usage Event is immutable actual attribution with exact Tenant Context,
principal/executor, capability, Work Item, Attempt, optional Assignment,
honestly reported provider/engine pair, input/output quantities, optional
duration, status, occurred and recorded timestamps, optional Truth Event and
Evidence references, policy, Approval, Estimate, and an idempotency reference.
Recorded time cannot precede occurrence. Failed and cancelled Attempts may
still consume resources. No prompt, model response, transcript, document,
credential, stdout, stderr, or arbitrary metadata is present. Usage Event is
neither a Truth Event nor a Customer Charge.

Existing TaskRun or session counters are not adapted: they do not prove exact
Tenant Context, capability, Work/Attempt identity, Authority, commercial
policy, actual-versus-estimate provenance, or Provider Cost. The compatibility
adapter therefore returns `UNSUPPORTED_ADAPTER`.

## Provider Cost versus Customer Charge

Provider Cost is a separate record linked to one Usage Event. Source is
`REPORTED_BY_PROVIDER`, `CONTRACT_RATE`, `INTERNAL_RATE`,
`MEASURED_LOCAL_COST`, or `UNKNOWN`. `UNKNOWN` carries no amount. Local Cost may
remain unavailable. V1 never infers Cost from public prices or copies provider
contracts.

Customer Charge is an immutable calculated commercial attribution linked to
Usage, Billing Profile, Plan Version, and pricing policy. Included, credit, or
no-charge policy can calculate zero even when Provider Cost is nonzero.
Waiver/reversal preserves Provider Cost and the original Charge. Charge is not
an invoice, payment, receivable, journal entry, or official financial
statement.

## Approval thresholds and budgets

Threshold bases distinguish estimated Cost, estimated Charge, estimated Usage,
actual Cost, and actual Charge. Below-threshold evaluation explicitly says no
Approval is required. Above threshold fails closed unless exactly one active,
authorized, policy-compatible committed Approval exists. V1 Approval targets
the exact Project Operational Context and its Authority provenance must carry
the threshold's separate approval capability. Rejected, conditional,
withdrawn, expired, invalidated, wrong-scope, wrong-policy, or duplicate
Approvals do not satisfy. Administrator identity is no implicit override.

A Budget Policy is an evaluation boundary, not payment reservation. It
distinguishes Provider Cost budgets from Customer Charge budgets, exact
currency, UTC period, warning amount, hard/soft enforcement, and explicit
override capability. Cross-currency totals reject. Soft exceedance reports a
warning and grants no Authority; hard exceedance denies absent valid Approval.

## Adjustments, corrections, and reversals

Base Usage, Cost, Charge, and Credit records are nonnegative and immutable.
Correction, reversal, waiver, and invalidation use a separate Adjustment with
exact target type/ID, Tenant, unit or Money, chronology, stable reason,
Authority provenance, and optional Decision/Approval references. A duplicate
reversal, cross-Tenant target, currency/unit mismatch, or reversal beyond the
original fails. Original history remains present.

## Cross-kernel integration

- Tenancy: every commercial record has an exact Tenant or Tenant Context. No
  Tenant is inferred from owner, email, hostname, folder, provider, or model.
- Operational Context: Product, Workspace, and Project remain canonical IDs;
  aliases never substitute and visibility grants no billing access.
- Authority: creation, grant, usage, Cost, Charge, adjustment, threshold,
  budget, and projection capabilities remain distinct and reuse authentic
  ADR 0001 decisions bound to exact resource, actor, scope, and policy.
- Truth & Accountability: Usage is distinct from Operational Event; Decisions
  may authorize adjustments without becoming adjustments; committed Approvals
  satisfy thresholds without becoming Charges; audit grants nothing.
- Work: attribution validates exact Work, Assignment, Attempt, executor, scope,
  and Tenant compatibility. Completion does not validate Charge. Failed work
  may have actual Usage.
- Evidence: records store bounded Evidence IDs only. Visibility of Cost,
  Charge, or Usage does not authorize Evidence; attestation is not threshold
  Approval.

## Collection validation

Bounded pure validation checks record integrity, unique IDs, references,
Tenant containment, Plan/Plan-Version chronology and overlap, Entitlement
conflicts, credit linkage, idempotent Usage, Cost/Charge linkage, currency,
Adjustment bounds and chronology, policy, classification, and contract major
version. Conflicts are reported and never silently resolved or double-charged.

## Safe projection

Flat-only projectors reuse ADR 0001's authentic decision and scalar projector,
bind exact ID, resource type, scope, policy and version, and return a new
mapping. Separate capabilities protect Billing Profile, Plan, Entitlement,
Allowance, credit, Usage, Provider Cost, Customer Charge, Budget, and
Adjustment. Provider Cost permission does not follow from Charge permission.
Margin/formula, provider/model, principal, Evidence, Work, Truth, Approval, and
Context references are omitted unless explicitly represented and authorized.
Caller-supplied computed balances, Charges, remaining Allowance, Budget state,
or effective Entitlement cannot enter a factory-sealed record.

## Safe audit

Safe billing audits contain only typed bounded IDs, action/outcome/reason codes,
Tenant ID, optional safe capability/unit/currency codes, policy/version,
canonical timestamp, and correlation code. They exclude prompts, responses,
transcripts, documents, filenames, provider credentials/API keys, cookies,
sessions, tokens, headers, request bodies, card/bank/tax/address/invoice data,
hidden Provider Cost, margin, pricing formula, hidden Charge, protected human
identity, metadata, and exception text. Audit is not Authority, Entitlement,
credit, Charge, invoice, payment, or accounting truth.

## Derived views

Restrictive pure views require every source to validate and project under an
authentic decision. Denied records cannot be silently omitted. Cross-Tenant
input denies. Money is kept in separate currency buckets without conversion.
Estimate and actual identities remain distinct; reversal netting must be
explicit in the selected sources. Results are deterministic and are not an
invoice, payment, accounting ledger, Authority decision, or official financial
statement.

## Production and persistence boundary

No safe production seam is approved. Existing routes, providers, authentication,
privileges, Documents, Library, Capture, uploads, media, STT, analysis, Tasks,
TaskRuns, scheduler, and background jobs do not import this kernel and remain
unchanged. No table, migration, registry, JSON ledger, browser storage, runtime
meter, or public response changes.

Durable Billing Profiles, Plans, Entitlements, credits, usage, Costs, Charges,
Budgets, payment methods, invoices, taxes, discounts, subscriptions, checkout,
Stripe, QuickBooks, provider-price ingestion, currency conversion, collections,
revenue recognition, accounting export, billing API, billing UI, AI routing,
and DT Beach cost workflows are deferred.

## Stable-code glossary

- IDs: `bprof1`, `plan1`, `planv1`, `ent1`, `allow1`, `cracct1`, `crmov1`,
  `uest1`, `usage1`, `pcost1`, `charge1`, `adj1`, `thresh1`, `budget1`, `baudit1`.
- Currency: `USD`, `DOP`, `EUR`.
- Units: `TOKEN`, `MILLISECOND`, `BYTE`, `EXECUTION`.
- Entitlement status: `ACTIVE`, `SUSPENDED`, `REVOKED`, `EXPIRED`,
  `SUPERSEDED`, `INVALIDATED`.
- Charge status: `CALCULATED`, `WAIVED`, `REVERSED`, `INVALIDATED`.
- Stable reasons include `ENTITLED`, `NOT_ENTITLED`, `CONFLICT`,
  `WITHIN_ALLOWANCE`, `ALLOWANCE_EXCEEDED`, `APPROVAL_NOT_REQUIRED`,
  `APPROVAL_REQUIRED`, `APPROVAL_SATISFIED`, `WITHIN_BUDGET`,
  `BUDGET_WARNING`, `BUDGET_DENIED`, and `OVERRIDE_SATISFIED`, plus the
  uppercase `BillingErrorCode` values exposed by contract failures.

## Fictional examples

1. Fictional Tenant Alpha receives one active `ANALYZE` Entitlement. Its member
   still needs separate Work execution Authority.
2. A fictional 1,000-token Allowance and a 50-credit exploration account remain
   different quantities; neither is USD.
3. A provider reports a fictional USD 1.250000 Cost. A versioned 20 percent
   margin policy calculates USD 1.500000 Customer Charge without changing Cost.
4. A projected estimate over a fictional threshold remains blocked until an
   exact project Approval exists. The Approval is not the Charge.
5. Reversing a fictional Charge creates an Adjustment. The Usage Event,
   Provider Cost, and original Charge remain historical.

## Limitations

V1 does not meter current runtime activity, persist billing data, process money,
invoice, tax, convert currency, fetch prices, route models, execute work,
determine legal liability, or establish accounting truth.
