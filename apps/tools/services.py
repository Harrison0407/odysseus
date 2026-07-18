"""Tool custody service layer (Priority 1 — Manuel's interview: "no
formal indicators" / informal tool tracking). Enforces the one
invariant the data model implies but never enforced before this
session: a tool cannot be checked out twice at once."""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.audit import services as audit
from apps.audit.models import AuditEvent

from .models import Tool, ToolAssignment, ToolCheckout, ToolRepair, ToolReturn


class ToolCustodyError(ValueError):
    pass


def is_checked_out(tool: Tool) -> bool:
    return ToolCheckout.objects.filter(
        assignment__tool=tool, assignment__is_active=True, tool_return__isnull=True
    ).exists()


def current_checkout(tool: Tool):
    return ToolCheckout.objects.filter(
        assignment__tool=tool, assignment__is_active=True, tool_return__isnull=True
    ).select_related("assignment__assigned_to").first()


@transaction.atomic
def checkout_tool(tool: Tool, assigned_to, user, *, expected_return_date=None) -> ToolCheckout:
    if is_checked_out(tool):
        raise ToolCustodyError(f"La herramienta {tool} ya está entregada y no ha sido devuelta todavía.")
    assignment = ToolAssignment.objects.create(tool=tool, assigned_to=assigned_to, is_active=True, created_by=user)
    checkout = ToolCheckout.objects.create(
        assignment=assignment, checkout_date=timezone.now().date(),
        expected_return_date=expected_return_date, created_by=user,
    )
    audit.log(
        AuditEvent.Action.OTHER, instance=checkout, actor=user,
        summary=f"Herramienta {tool} entregada a {assigned_to}",
    )
    return checkout


@transaction.atomic
def return_tool(checkout: ToolCheckout, user, *, damage_noted=False, damage_description="") -> ToolReturn:
    if ToolReturn.objects.filter(checkout=checkout).exists():
        raise ToolCustodyError("Esta entrega de herramienta ya fue registrada como devuelta.")
    tool_return = ToolReturn.objects.create(
        checkout=checkout, actual_return_date=timezone.now().date(),
        damage_noted=damage_noted, damage_description=damage_description, created_by=user,
    )
    assignment = checkout.assignment
    assignment.is_active = False
    assignment.save(update_fields=["is_active"])
    if damage_noted:
        tool = assignment.tool
        tool.condition = "Dañada — pendiente de reparación"
        tool.save(update_fields=["condition"])
    audit.log(
        AuditEvent.Action.OTHER, instance=tool_return, actor=user,
        summary=f"Herramienta {assignment.tool} devuelta" + (" con daño reportado" if damage_noted else ""),
    )
    return tool_return


@transaction.atomic
def record_repair(tool: Tool, user, *, description, cost=None, resulted_in_write_off=False) -> ToolRepair:
    repair = ToolRepair.objects.create(
        tool=tool, description=description, cost=cost,
        resulted_in_write_off=resulted_in_write_off, repaired_at=timezone.now().date(), created_by=user,
    )
    if resulted_in_write_off:
        tool.condition = "Dada de baja"
        tool.save(update_fields=["condition"])
    audit.log(
        AuditEvent.Action.OTHER, instance=repair, actor=user,
        summary=f"Reparación registrada para {tool}" + (" (dada de baja)" if resulted_in_write_off else ""),
    )
    return repair
