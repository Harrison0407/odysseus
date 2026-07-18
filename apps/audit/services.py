from django.contrib.contenttypes.models import ContentType

from .middleware import get_current_user
from .models import AuditEvent


def log(action, instance=None, actor=None, summary="", **metadata):
    """Convenience helper: apps.audit.services.log(AuditEvent.Action.RECEIPT_POSTING, receipt, summary="...")."""
    kwargs = {
        "action": action,
        "actor": actor or get_current_user(),
        "summary": summary,
        "metadata": metadata,
    }
    if instance is not None:
        kwargs["content_type"] = ContentType.objects.get_for_model(instance)
        kwargs["object_id"] = instance.pk
    return AuditEvent.objects.create(**kwargs)
