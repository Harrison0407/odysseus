# Workflow, Gates, and Handoffs

## End-to-end chain (spec section 9)

`Shipment.Status` implements the full chain verbatim:

```
requirement → purchasing_preparation → financial_review →
china_origin_preparation → pre_shipment_matching →
storage_receiving_readiness → authorized_to_ship →
shipment_documentation → operationally_verified →
released_to_receiving → receiving_in_progress →
received | received_with_exceptions → warehouse_custody →
reconciled_closed
```

(The requested-by-project → picked/dispatched → delivered → installed →
inspected → accepted tail of the chain lives on `MaterialRequest.Status`
and the `apps.requests` chain of records, since it applies per-request
rather than per-shipment.)

## Gates implemented today

- **Storage and receiving readiness** (spec 9.4): `ReceivingPlan.status`
  starts `MISSING`; a shipment reaching `AUTHORIZED_TO_SHIP` without a
  `SUITABLE` plan is the condition the model exists to catch. The
  override path (`override_reason`, `overridden_by`) is modeled; the UI
  button to actually block/override at that transition is a near-term
  follow-up (`KNOWN_LIMITATIONS.md`) — today the gate is enforceable at
  the data layer and via a management-level query, not yet a hard
  in-app block on the status change itself.
- **Operational verification** (spec 9.5): `ReleasePacketVersion` freezes
  a `ShipmentManifestVersion`; `Receipt` can only be created against a
  `ReleasePacket`, and `ReceiptLine` can only reference a `ManifestLine`
  that came from that frozen version — there is no code path to receive
  against an unfrozen or nonexistent manifest.
- **Financial finalization** (spec 9.6): `LandedCostVersion.is_final`
  is explicitly distinct from `Shipment.status = OPERATIONALLY_VERIFIED`
  — the two are separate booleans on separate models, never conflated.

## Handoff engine (spec section 8)

`apps.workflow.Handoff` requires three separate events, never a single
status flip:

1. Outgoing user submits (`status → SUBMITTED`, `submitted_at` set).
2. Mandatory controls for the stage pass (checked via `HandoffChecklist`
   items, each optionally `is_warning`/`is_critical`).
3. Incoming user explicitly accepts (`HandoffDecision.decision = accepted`,
   `status → ACCEPTED`, `decided_at` set).

`StageAssignment` carries every role spec section 8 requires
(`primary_user`, `backup_user`, `preparer`, `reviewer`, `approver`,
`observers`, `escalation_user`) attached generically to any record via
`content_type`/`object_id`, so the same mechanism covers a
`PurchaseOrder`, a `Shipment`, or a `MaterialRequest` without a
per-model handoff table.

A rejected or returned handoff (`HandoffDecision.decision = rejected` /
`returned`) does not delete the handoff — a new `HandoffDecision` row is
added and `Handoff.version` increments, preserving the full history
(core principle 4.4).

## What still needs a UI (tracked, not hidden)

The gate-blocking UI (a literal "cannot advance" message tied to the
`Shipment.status` transition) and the handoff inbox/accept-reject screens
are modeled completely but do not yet have dedicated views — today they
are demonstrated via direct ORM/test verification
(`tests/test_live_container_fixture.py`, `tests/test_receiving_and_inventory.py`)
rather than a clickable "Reject this handoff" button. See
`KNOWN_LIMITATIONS.md` item 3.
