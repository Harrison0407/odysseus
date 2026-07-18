import uuid

from django.conf import settings
from django.db import models


class UUIDModel(models.Model):
    """Base for every domain model that may ever be referenced by a share
    link, QR label, or external API. Never expose sequential integer PKs
    externally (spec section 12)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class CreatedByModel(models.Model):
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        abstract = True


class BaseModel(UUIDModel, TimeStampedModel, CreatedByModel):
    """Standard base for domain entities: UUID PK + audit timestamps +
    creator. Concrete immutability (no hard delete, versioning) is added
    per-model where the spec requires it (documents, release packets,
    landed cost versions, posted inventory movements, etc.)."""

    class Meta:
        abstract = True


class ConfidenceLevel(models.TextChoices):
    CONFIRMED = "confirmed", "Confirmado por persona"
    HIGH = "high", "Confianza alta (automático)"
    MEDIUM = "medium", "Confianza media (automático)"
    LOW = "low", "Confianza baja (automático)"
    UNKNOWN = "unknown", "Desconocido"


class DestinationScope(models.TextChoices):
    """Spec section 13A.7 — project-wide stock vs destination-specific
    material. A project-wide item must never be forced into a building
    merely to satisfy a required field."""

    ORGANIZATION = "organization", "Toda la organización"
    PROJECT = "project", "Todo el proyecto"
    ZONE = "zone", "Etapa o zona"
    BUILDING = "building", "Edificio"
    FLOOR = "floor", "Piso"
    UNIT = "unit", "Apartamento / unidad"
    COMMON_AREA = "common_area", "Área común"
    WAREHOUSE_STOCK = "warehouse_stock", "Stock de almacén"
    REPLACEMENT_RESERVE = "replacement_reserve", "Reserva de reemplazo"
    UNKNOWN_PENDING = "unknown_pending", "Desconocido / pendiente de asignación"


class Severity(models.TextChoices):
    INFO = "info", "Informativo"
    WARNING = "warning", "Advertencia"
    CRITICAL = "critical", "Crítico"


class UnknownableDecimalMixin:
    """Convention marker: fields that may legitimately be unknown pair a
    nullable DecimalField with a companion `*_basis` CharField using
    UNKNOWN_VALUE_CHOICES, instead of silently defaulting to zero."""


UNKNOWN_VALUE_CHOICES = [
    ("known", "Conocido"),
    ("unknown", "Desconocido"),
    ("not_provided", "No suministrado"),
    ("not_applicable", "No aplica"),
    ("approximate", "Aproximado"),
]
