"""QR label generation and controlled scanning (spec section 12/17/18/
21/22).

Every supported entity type (inventory lot, warehouse location,
receiving unit, dispatch, delivery, installation material record, tool,
container) is registered in `_ENTITY_REGISTRY` below with three pure
functions: how to find its organization (for isolation), what
human-readable label to print, and which existing, already-permission-
checked page a scan should land on. No new permission system is
introduced — a scan only ever forwards into a URL that is already
behind its own `login_required`/organization-scoped view.
"""

from __future__ import annotations

import base64
import io

import qrcode
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.audit import services as audit
from apps.audit.models import AuditEvent

from .models import QRLabel, QRLabelPrintEvent, QRScanEvent


class LabelError(ValueError):
    """Raised for any label-lifecycle precondition that isn't met."""


def _lot_organization(lot):
    return lot.item.organization


def _location_organization(location):
    return location.zone.site.organization


def _receipt_organization(receipt):
    return receipt.release_packet.shipment.organization


def _tool_organization(tool):
    return tool.organization


def _container_organization(container):
    return container.shipment.organization


def _dispatch_organization(dispatch):
    return dispatch.pick_list.request.project.organization


def _delivery_organization(delivery):
    return delivery.dispatch.pick_list.request.project.organization


def _installation_organization(installation):
    return installation.project_receipt.delivery.dispatch.pick_list.request.project.organization


_ENTITY_REGISTRY = {
    "inventorylot": {
        "label": "Lote de inventario",
        "organization": _lot_organization,
        "human_label": lambda o: o.lot_code or str(o.pk)[:8],
        "context": lambda o: str(o.item),
        "url": lambda o: reverse("inventory:lot-detail", args=[o.pk]),
    },
    "warehouselocation": {
        "label": "Ubicación de almacén",
        "organization": _location_organization,
        "human_label": lambda o: o.code,
        "context": lambda o: str(o.zone.site),
        "url": lambda o: reverse("inventory:location-detail", args=[o.pk]),
    },
    "receipt": {
        "label": "Recepción",
        "organization": _receipt_organization,
        "human_label": lambda o: f"Recibo {o.container}",
        "context": lambda o: str(o.release_packet.shipment),
        "url": lambda o: reverse("receiving:detail", args=[o.pk]),
    },
    "tool": {
        "label": "Herramienta / equipo",
        "organization": _tool_organization,
        "human_label": lambda o: f"{o.code} — {o.description}",
        "context": lambda o: str(o.current_location) if o.current_location_id else "",
        "url": lambda o: reverse("tools:detail", args=[o.pk]),
    },
    "container": {
        "label": "Contenedor / referencia de carga",
        "organization": _container_organization,
        "human_label": lambda o: o.container_number,
        "context": lambda o: str(o.shipment),
        "url": lambda o: reverse("shipments:detail", args=[o.shipment_id]),
    },
    "dispatch": {
        "label": "Despacho",
        "organization": _dispatch_organization,
        "human_label": lambda o: f"Despacho {str(o.pk)[:8]}",
        "context": lambda o: str(o.pick_list.request.project),
        "url": lambda o: reverse("requests:detail", args=[o.pick_list.request_id]),
    },
    "delivery": {
        "label": "Entrega",
        "organization": _delivery_organization,
        "human_label": lambda o: f"Entrega {str(o.pk)[:8]}",
        "context": lambda o: str(o.dispatch.pick_list.request.project),
        "url": lambda o: reverse("requests:delivery-detail", args=[o.pk]),
    },
    "installationrecord": {
        "label": "Material de instalación",
        "organization": _installation_organization,
        "human_label": lambda o: f"Instalación {str(o.pk)[:8]}",
        "context": lambda o: str(o.project_receipt.delivery.dispatch.pick_list.request.project),
        "url": lambda o: reverse("requests:installation-detail", args=[o.pk]),
    },
}


def _entity_key(instance) -> str:
    return instance.__class__.__name__.lower()


def registry_entry(instance) -> dict:
    key = _entity_key(instance)
    entry = _ENTITY_REGISTRY.get(key)
    if entry is None:
        raise LabelError(f"Tipo de entidad no soportado para etiquetas QR: {key}")
    return entry


@transaction.atomic
def get_or_create_active_label(instance, user) -> QRLabel:
    """One active (non-invalidated) label per entity — calling this
    again for the same entity returns the existing label rather than
    creating a duplicate."""
    content_type = ContentType.objects.get_for_model(instance)
    existing = QRLabel.objects.filter(
        content_type=content_type, object_id=instance.pk, is_invalidated=False
    ).first()
    if existing is not None:
        return existing
    entry = registry_entry(instance)
    label = QRLabel.objects.create(
        content_type=content_type, object_id=instance.pk, organization=entry["organization"](instance),
        human_label=entry["human_label"](instance), entity_type_label=entry["label"],
        context_label=entry["context"](instance), created_by=user,
    )
    audit.log(AuditEvent.Action.OTHER, instance=label, actor=user, summary=f"Etiqueta QR generada: {label.human_label}")
    return label


def label_qr_data_uri(label: QRLabel, request) -> str:
    """Renders the label's QR code as a self-contained base64 PNG data
    URI — no external image-serving endpoint, so the printed page works
    even without a live connection to this server (only the scan
    itself needs connectivity). The payload is the opaque scan URL,
    never the underlying object's real ID."""
    scan_path = reverse("qr-scan", args=[label.token])
    scan_url = request.build_absolute_uri(scan_path)
    img = qrcode.make(scan_url)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def record_print(label: QRLabel, user, *, quantity=1, is_batch=False) -> QRLabelPrintEvent:
    if quantity < 1:
        raise LabelError("La cantidad a imprimir debe ser al menos 1.")
    event = QRLabelPrintEvent.objects.create(label=label, printed_by=user, quantity=quantity, is_batch=is_batch, created_by=user)
    audit.log(
        AuditEvent.Action.OTHER, instance=label, actor=user,
        summary=f"Etiqueta QR impresa: {label.human_label} (cantidad {quantity})",
    )
    return event


@transaction.atomic
def invalidate_and_replace_label(label: QRLabel, user, *, reason) -> QRLabel:
    if label.is_invalidated:
        raise LabelError("Esta etiqueta ya fue invalidada.")
    if not reason or not reason.strip():
        raise LabelError("Se requiere un motivo para invalidar la etiqueta.")
    instance = label.content_type.get_object_for_this_type(pk=label.object_id)
    entry = registry_entry(instance)
    new_label = QRLabel.objects.create(
        content_type=label.content_type, object_id=label.object_id, organization=label.organization,
        human_label=entry["human_label"](instance), entity_type_label=entry["label"],
        context_label=entry["context"](instance), version=label.version + 1, created_by=user,
    )
    label.is_invalidated = True
    label.invalidated_at = timezone.now()
    label.invalidated_by = user
    label.invalidated_reason = reason
    label.replaced_by = new_label
    label.save()
    audit.log(
        AuditEvent.Action.OTHER, instance=label, actor=user,
        summary=f"Etiqueta QR invalidada y reemplazada: {label.human_label}", reason=reason,
    )
    return new_label


def resolve_scan(token, user, ip_address=None):
    """Looks up the label by its opaque token, always logs a
    `QRScanEvent` (even for a denied/invalid scan), and returns
    `(label, target_url)` — `target_url` is `None` if the label is
    invalid, invalidated, or the user's organization doesn't match
    (cross-organization access is logged, never silently allowed, and
    never reveals *what* the label was for). The scan alone never
    authorizes anything further — `target_url` is just the entity's
    own existing, already-permission-checked detail page."""
    label = QRLabel.objects.filter(token=token).select_related("content_type").first()
    if label is None:
        return None, None
    if label.is_invalidated:
        QRScanEvent.objects.create(label=label, scanned_by=user, ip_address=ip_address, created_by=user)
        return label, None
    user_organization = getattr(getattr(user, "profile", None), "organization", None)
    if user_organization is None or user_organization != label.organization:
        QRScanEvent.objects.create(
            label=label, scanned_by=user, ip_address=ip_address, was_cross_organization_denied=True, created_by=user,
        )
        return label, None
    QRScanEvent.objects.create(label=label, scanned_by=user, ip_address=ip_address, created_by=user)
    instance = label.content_type.get_object_for_this_type(pk=label.object_id)
    entry = registry_entry(instance)
    return label, entry["url"](instance)
