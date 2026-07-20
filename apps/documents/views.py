from pathlib import Path

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render

from apps.audit import services as audit
from apps.audit.models import AuditEvent
from apps.core.storage import document_storage
from apps.governance import services as governance_services
from apps.procurement.models import ProcurementPackage

from .models import Document, DocumentType, DocumentVersion


def authorized_documents_queryset(user):
    """Apply tenant/package/classification policy before document retrieval."""
    profile_org_id = getattr(getattr(user, "profile", None), "organization_id", None)
    authorization = Q(package__isnull=True, organization_id=profile_org_id)
    package_ids = governance_services.authorized_package_ids(user)
    for package in ProcurementPackage.objects.filter(pk__in=package_ids):
        authorization |= Q(
            package=package,
            classification__in=governance_services.visible_classifications_for(user, package=package),
        )
    return Document.objects.filter(authorization)


class DocumentUploadForm(forms.Form):
    document_type = forms.ModelChoiceField(queryset=DocumentType.objects.none(), label="Tipo de documento")
    title = forms.CharField(max_length=255, label="Título")
    file = forms.FileField(label="Archivo")

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["document_type"].queryset = DocumentType.objects.filter(organization=organization)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")

    def clean_file(self):
        f = self.cleaned_data["file"]
        ext = Path(f.name).suffix.lower()
        if ext not in settings.ALLOWED_UPLOAD_EXTENSIONS:
            raise forms.ValidationError(
                f"Tipo de archivo no permitido ({ext}). Permitidos: {', '.join(settings.ALLOWED_UPLOAD_EXTENSIONS)}"
            )
        max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
        if f.size > max_bytes:
            raise forms.ValidationError(f"El archivo excede el límite de {settings.MAX_UPLOAD_SIZE_MB} MB.")
        return f


@login_required
def document_list(request):
    documents = (
        authorized_documents_queryset(request.user)
        .select_related("document_type")
        .prefetch_related("versions")
        .order_by("-created_at")[:200]
    )
    return render(request, "documents/list.html", {"documents": documents})


@login_required
def document_upload(request):
    organization = request.user.profile.organization
    if request.method == "POST":
        form = DocumentUploadForm(request.POST, request.FILES, organization=organization)
        if form.is_valid():
            uploaded = form.cleaned_data["file"]
            stored = document_storage.save(uploaded, uploaded.name)

            duplicate = DocumentVersion.objects.filter(sha256=stored["sha256"]).first()

            document = Document.objects.create(
                organization=organization,
                document_type=form.cleaned_data["document_type"],
                title=form.cleaned_data["title"],
                uploaded_by=request.user,
                created_by=request.user,
            )
            version = DocumentVersion.objects.create(
                document=document,
                version_number=1,
                stored_name=stored["stored_name"],
                original_filename=stored["original_filename"],
                sha256=stored["sha256"],
                size_bytes=stored["size_bytes"],
                mime_type=getattr(uploaded, "content_type", "") or "",
                uploaded_by=request.user,
                is_duplicate_of=duplicate,
                created_by=request.user,
            )
            audit.log(
                AuditEvent.Action.DOCUMENT_UPLOAD,
                instance=document,
                actor=request.user,
                summary=f"Cargado: {document.title}",
                sha256=stored["sha256"],
            )
            if duplicate:
                messages.warning(
                    request,
                    "Este archivo tiene el mismo contenido (SHA-256) que un documento ya cargado. "
                    "Se conservó de todas formas — revise si es un duplicado antes de usarlo.",
                )
            else:
                messages.success(request, "Documento cargado correctamente.")
            return redirect("documents:detail", pk=document.pk)
    else:
        form = DocumentUploadForm(organization=organization)
    return render(request, "documents/upload.html", {"form": form})


@login_required
def document_detail(request, pk):
    document = get_object_or_404(
        authorized_documents_queryset(request.user).select_related("document_type").prefetch_related("versions"),
        pk=pk,
    )
    return render(request, "documents/detail.html", {"document": document})


@login_required
def document_download(request, pk, version_pk):
    """Authorize package and classification before opening stored bytes."""
    version = get_object_or_404(
        DocumentVersion, pk=version_pk, document_id=pk,
        document_id__in=authorized_documents_queryset(request.user).values("pk"),
    )
    try:
        fh = document_storage.open(version.stored_name)
    except FileNotFoundError:
        raise Http404("Archivo no encontrado en almacenamiento.")
    return FileResponse(fh, as_attachment=True, filename=version.original_filename)
