from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render

from apps.documents.models import Document
from apps.documents.views import DocumentUploadForm
from apps.workflow import services as wfsvc

from . import services
from .models import Drawing


@login_required
def drawing_list(request):
    organization = request.user.profile.organization
    drawings = Drawing.objects.select_related("project", "building", "floor", "unit").filter(
        project__organization=organization, is_current=True
    )
    building_id = request.GET.get("building")
    if building_id:
        drawings = drawings.filter(building_id=building_id)
    drawings = drawings.order_by("-created_at")[:200]
    return render(request, "drawings/list.html", {"drawings": drawings})


@login_required
def drawing_detail(request, pk):
    organization = request.user.profile.organization
    drawing = get_object_or_404(
        Drawing.objects.select_related("project", "building", "floor", "unit", "source_document", "supersedes"),
        pk=pk, project__organization=organization,
    )
    if not wfsvc.user_can_access_project(request.user, drawing.project):
        raise Http404
    history = []
    node = drawing
    while node is not None:
        history.append(node)
        node = node.supersedes
    upload_form = DocumentUploadForm(organization=organization)
    return render(request, "drawings/detail.html", {"drawing": drawing, "history": history, "upload_form": upload_form})


@login_required
def drawing_supersede(request, pk):
    organization = request.user.profile.organization
    drawing = get_object_or_404(Drawing, pk=pk, project__organization=organization)
    if not wfsvc.user_can_access_project(request.user, drawing.project):
        raise Http404
    if request.method == "POST":
        upload_form = DocumentUploadForm(request.POST, request.FILES, organization=organization)
        if upload_form.is_valid():
            from apps.core.storage import document_storage
            from apps.documents.models import DocumentVersion

            stored = document_storage.save(upload_form.cleaned_data["file"], upload_form.cleaned_data["file"].name)
            duplicate = DocumentVersion.objects.filter(sha256=stored["sha256"]).first()
            new_document = Document.objects.create(
                organization=organization, document_type=upload_form.cleaned_data["document_type"],
                title=upload_form.cleaned_data["title"], uploaded_by=request.user, created_by=request.user,
            )
            DocumentVersion.objects.create(
                document=new_document, version_number=1, stored_name=stored["stored_name"],
                original_filename=stored["original_filename"], sha256=stored["sha256"], size_bytes=stored["size_bytes"],
                mime_type=getattr(upload_form.cleaned_data["file"], "content_type", "") or "", is_duplicate_of=duplicate,
                uploaded_by=request.user, created_by=request.user,
            )
            try:
                new_drawing = services.supersede_drawing(
                    drawing, request.user, new_source_document=new_document, notes=request.POST.get("notes", ""),
                )
                messages.success(request, f"Plano reemplazado — nueva revisión {new_drawing.revision}.")
                return redirect("drawings:detail", pk=new_drawing.pk)
            except services.DrawingError as exc:
                messages.error(request, str(exc))
        else:
            messages.error(request, "Revise el archivo de la nueva revisión.")
    return redirect("drawings:detail", pk=pk)


@login_required
def drawing_approve(request, pk):
    organization = request.user.profile.organization
    drawing = get_object_or_404(Drawing, pk=pk, project__organization=organization)
    if request.method == "POST":
        try:
            services.approve_drawing(drawing, request.user)
            messages.success(request, "Plano aprobado.")
        except services.DrawingError as exc:
            messages.error(request, str(exc))
    return redirect("drawings:detail", pk=pk)
