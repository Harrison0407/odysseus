"""CONFOTUR reconciliation (spec section 25): detects when the same
underlying exemption basis (a `Quotation` or a `ManifestLine`) is
claimed by more than one still-unresolved `ConfoturLine` — the "quotation
split across containers/liquidations, risk of duplicate exemption"
concern from Markeris's interview (see `BUSINESS_REQUIREMENTS.md`).

Never auto-resolved: a duplicate candidate only leaves the reconciliation
list once a human explicitly confirms it via `confirm_duplicate`, which
sets `ConfoturLine.is_duplicate_of` — the same field the data model
already carried for this since Priority 0, just never previously
populated by any code path. Matches the project-wide rule that a
discrepancy stays visible until genuinely resolved, never silently
dismissed (core principle 4.4)."""

from __future__ import annotations

from collections import defaultdict

from django.db import transaction

from apps.audit import services as audit
from apps.audit.models import AuditEvent

from .models import ConfoturLine


def detect_duplicate_candidates(organization):
    """Groups every still-unresolved (`is_duplicate_of` is None)
    `ConfoturLine` for this organization by shared `quotation` or
    `manifest_line`; any group with 2+ lines is a duplicate-exemption
    candidate requiring human reconciliation."""
    lines = list(
        ConfoturLine.objects.filter(confotur_list__organization=organization, is_duplicate_of__isnull=True)
        .select_related("confotur_list", "quotation", "manifest_line")
    )
    groups = defaultdict(list)
    for line in lines:
        if line.quotation_id:
            groups[("quotation", line.quotation_id)].append(line)
        elif line.manifest_line_id:
            groups[("manifest_line", line.manifest_line_id)].append(line)

    return [
        {"basis": key[0], "lines": grouped}
        for key, grouped in groups.items()
        if len(grouped) > 1
    ]


@transaction.atomic
def confirm_duplicate(line: ConfoturLine, duplicate_of: ConfoturLine, user) -> ConfoturLine:
    if line.pk == duplicate_of.pk:
        raise ValueError("Una línea CONFOTUR no puede ser duplicado de sí misma.")
    if line.is_duplicate_of_id is not None:
        raise ValueError("Esta línea ya fue marcada como duplicada.")
    line.is_duplicate_of = duplicate_of
    line.save(update_fields=["is_duplicate_of"])
    audit.log(
        AuditEvent.Action.OTHER, instance=line, actor=user,
        summary=f"Línea CONFOTUR marcada como duplicado de {duplicate_of}",
    )
    return line
