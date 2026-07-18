from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render

from . import services
from .models import QRLabel


def _instance_or_404(app_label, model, pk, organization):
    content_type = get_object_or_404(ContentType, app_label=app_label, model=model)
    model_class = content_type.model_class()
    if model_class is None:
        raise Http404
    instance = get_object_or_404(model_class, pk=pk)
    try:
        entry = services.registry_entry(instance)
    except services.LabelError:
        raise Http404
    if entry["organization"](instance) != organization:
        raise Http404
    return content_type, instance


@login_required
def label_print(request, app_label, model, pk):
    organization = request.user.profile.organization
    content_type, instance = _instance_or_404(app_label, model, pk, organization)
    label = services.get_or_create_active_label(instance, request.user)

    if request.method == "POST":
        quantity = int(request.POST.get("cantidad", 1) or 1)
        try:
            services.record_print(label, request.user, quantity=quantity)
            messages.success(request, f"Impresión registrada ({quantity}).")
        except services.LabelError as exc:
            messages.error(request, str(exc))
        return redirect("labels:print", app_label=app_label, model=model, pk=pk)

    qr_data_uri = services.label_qr_data_uri(label, request)
    history = label.print_events.select_related("printed_by").all()
    scans = label.scan_events.select_related("scanned_by").all()
    return render(request, "labels/print.html", {
        "label": label, "instance": instance, "qr_data_uri": qr_data_uri,
        "history": history, "scans": scans, "app_label": app_label, "model": model,
    })


@login_required
def label_batch_print(request):
    if request.method != "POST":
        return redirect("/")
    organization = request.user.profile.organization
    app_label = request.POST.get("app_label", "")
    model = request.POST.get("model", "")
    object_ids = request.POST.getlist("object_id")
    quantity = int(request.POST.get("cantidad", 1) or 1)
    content_type = get_object_or_404(ContentType, app_label=app_label, model=model)
    model_class = content_type.model_class()

    labels_and_qr = []
    for object_id in object_ids:
        instance = model_class.objects.filter(pk=object_id).first()
        if instance is None:
            continue
        entry = services.registry_entry(instance)
        if entry["organization"](instance) != organization:
            continue  # silently excluded from the batch, never a cross-organization leak
        label = services.get_or_create_active_label(instance, request.user)
        services.record_print(label, request.user, quantity=quantity, is_batch=True)
        labels_and_qr.append((label, services.label_qr_data_uri(label, request)))

    return render(request, "labels/batch_print.html", {"labels_and_qr": labels_and_qr, "quantity": quantity})


@login_required
def label_invalidate(request, label_pk):
    label = get_object_or_404(QRLabel, pk=label_pk, organization=request.user.profile.organization)
    if request.method == "POST":
        try:
            services.invalidate_and_replace_label(label, request.user, reason=request.POST.get("reason", ""))
            messages.success(request, "Etiqueta invalidada — se generó una nueva.")
        except services.LabelError as exc:
            messages.error(request, str(exc))
    return redirect("labels:print", app_label=label.content_type.app_label, model=label.content_type.model, pk=label.object_id)


@login_required
def qr_scan_landing(request, token):
    """The controlled scan landing page: `login_required` means an
    unauthenticated scan is redirected to log in first (with `next`
    pointing back here) before anything about the label is resolved —
    scanning alone never reveals or authorizes anything. Once
    authenticated, `resolve_scan` re-checks organization membership and
    logs the scan either way, then this view simply forwards to the
    entity's own existing, already-permission-checked detail page."""
    ip = request.META.get("REMOTE_ADDR")
    label, target_url = services.resolve_scan(token, request.user, ip_address=ip)
    if target_url is None:
        return render(request, "labels/scan_invalid.html", status=404)
    return redirect(target_url)
