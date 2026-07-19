from django.conf import settings
from django.db import models

from apps.core.models import BaseModel, ConfidenceLevel
from apps.governance.models import Classification


class DocumentType(BaseModel):
    """Configurable taxonomy (spec section 11). Default types are seeded
    by a management command, not hard-coded into business logic."""

    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE, related_name="document_types")
    name = models.CharField(max_length=150)
    code = models.SlugField(max_length=60)
    is_official_customs_document = models.BooleanField(
        default=False,
        help_text="True for BL, customs declarations, liquidations, CONFOTUR — documents that "
        "must be preserved exactly as issued and never treated as internal-only.",
    )
    description = models.TextField(blank=True)

    class Meta:
        unique_together = [("organization", "code")]
        ordering = ["name"]

    def __str__(self):
        return self.name


class DocumentTypeAlias(BaseModel):
    """PO, PI, CI, PL, BL, B/L, CIAL, PIAU, Chinese titles, supplier-specific
    abbreviations. Aliases assist classification but never silently define
    the type (spec section 11) — confirmation always requires a human."""

    document_type = models.ForeignKey(DocumentType, on_delete=models.CASCADE, related_name="aliases")
    alias_text = models.CharField(max_length=150)
    language = models.CharField(max_length=10, blank=True, help_text="e.g. es, en, zh")

    def __str__(self):
        return f"{self.alias_text} -> {self.document_type}"


class SupplierDocumentProfile(BaseModel):
    """Reusable column-mapping profile for a given supplier's spreadsheet
    layout, so repeat imports do not require re-mapping columns by hand."""

    supplier = models.ForeignKey("procurement.Supplier", on_delete=models.CASCADE, related_name="document_profiles")
    document_type = models.ForeignKey(DocumentType, on_delete=models.CASCADE, related_name="+")
    name = models.CharField(max_length=150)
    column_mapping = models.JSONField(default=dict, blank=True, help_text="source column -> canonical field")
    notes = models.TextField(blank=True)

    def __str__(self):
        return f"{self.supplier} — {self.name}"


class RequiredDocumentRule(BaseModel):
    """Which document types must exist before a gate can pass (spec
    section 9.3–9.5)."""

    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE, related_name="+")
    document_type = models.ForeignKey(DocumentType, on_delete=models.CASCADE, related_name="+")
    gate_name = models.CharField(max_length=100, help_text='E.g. "operational_verification".')
    is_mandatory = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.gate_name}: {self.document_type}"


class Document(BaseModel):
    """The immutable original upload (core principle 4.4 — original
    uploaded documents are immutable; replacing one creates a new
    DocumentVersion, never overwrites)."""

    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE, related_name="documents")
    document_type = models.ForeignKey(DocumentType, on_delete=models.PROTECT, related_name="documents")
    human_confirmed_type = models.BooleanField(
        default=False, help_text="False if the type shown is only an automatic guess."
    )
    title = models.CharField(max_length=255)
    detected_language = models.CharField(max_length=10, blank=True)
    confirmed_language = models.CharField(max_length=10, blank=True)
    project = models.ForeignKey(
        "projects.Project", on_delete=models.SET_NULL, null=True, blank=True, related_name="documents"
    )
    supplier = models.ForeignKey(
        "procurement.Supplier", on_delete=models.SET_NULL, null=True, blank=True, related_name="documents"
    )
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")

    # Controlled Transparency / Confidentiality foundation. Defaults to
    # OPERATIONAL_SHARED so every pre-existing document keeps behaving
    # exactly as before (visible within its organization) — only new
    # confidentiality-aware uploads (factory quotes, internal commercial
    # sheets, ...) set a stricter classification explicitly.
    classification = models.CharField(
        max_length=40, choices=Classification.choices, default=Classification.OPERATIONAL_SHARED, blank=True,
    )
    package = models.ForeignKey(
        "procurement.ProcurementPackage", on_delete=models.SET_NULL, null=True, blank=True, related_name="documents_in_package",
    )

    def __str__(self):
        return self.title

    @property
    def current_version(self):
        return self.versions.order_by("-version_number").first()


class DocumentVersion(BaseModel):
    """One immutable stored file + its SHA-256 (core principle 4.4, spec
    section 32). Replacing a document creates a new version; nothing here
    is ever edited in place after creation."""

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="versions")
    version_number = models.PositiveIntegerField()
    stored_name = models.CharField(max_length=255, help_text="Opaque on-disk name from the storage adapter.")
    original_filename = models.CharField(max_length=255)
    sha256 = models.CharField(max_length=64, db_index=True)
    size_bytes = models.BigIntegerField()
    mime_type = models.CharField(max_length=150, blank=True)
    page_or_sheet_count = models.PositiveIntegerField(null=True, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    uploaded_at = models.DateTimeField(auto_now_add=True)
    is_duplicate_of = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="duplicates",
        help_text="Set automatically when SHA-256 matches an existing version (spec section 10, item 3).",
    )
    replaces_version = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="replaced_by",
    )

    class Meta:
        unique_together = [("document", "version_number")]
        ordering = ["document", "-version_number"]

    def __str__(self):
        return f"{self.document.title} v{self.version_number}"


class DocumentClassificationResult(BaseModel):
    """An automatic guess at document type/language — never equivalent to
    human confirmation (core principle 4.3)."""

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="classification_results")
    suggested_type = models.ForeignKey(DocumentType, on_delete=models.SET_NULL, null=True, related_name="+")
    suggested_language = models.CharField(max_length=10, blank=True)
    confidence = models.CharField(max_length=20, choices=ConfidenceLevel.choices, default=ConfidenceLevel.LOW)
    method = models.CharField(max_length=100, blank=True, help_text="e.g. filename heuristic, OCR, manual rule")


class HumanConfirmedDocumentType(BaseModel):
    document = models.OneToOneField(Document, on_delete=models.CASCADE, related_name="human_confirmation")
    confirmed_type = models.ForeignKey(DocumentType, on_delete=models.PROTECT, related_name="+")
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    confirmed_at = models.DateTimeField(auto_now_add=True)


class DocumentFieldSource(BaseModel):
    """The full provenance chain required by core principle 4.1: original
    source value, extracted value, normalized value, proposed/confirmed
    translation, canonical value, confidence, and who/when confirmed it.
    Attached to any downstream record via a generic pointer (e.g. a
    ManifestLine, PurchaseOrderLine, ReceiptLine)."""

    content_type = models.ForeignKey("contenttypes.ContentType", on_delete=models.CASCADE)
    object_id = models.UUIDField()
    field_name = models.CharField(max_length=100)

    document_version = models.ForeignKey(
        DocumentVersion, on_delete=models.PROTECT, related_name="field_sources", null=True, blank=True
    )
    source_reference = models.CharField(
        max_length=255, blank=True, help_text="Sheet/page/row/cell/package reference within the source document."
    )

    source_value = models.TextField(blank=True, help_text="Exact original-language value. Never overwritten.")
    extracted_value = models.TextField(blank=True)
    normalized_value = models.TextField(blank=True)
    proposed_translation = models.TextField(blank=True)
    confirmed_translation = models.TextField(blank=True)
    canonical_value = models.TextField(blank=True)

    confidence = models.CharField(max_length=20, choices=ConfidenceLevel.choices, default=ConfidenceLevel.UNKNOWN)
    warning = models.CharField(max_length=255, blank=True)

    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["content_type", "object_id", "field_name"])]

    def __str__(self):
        return f"{self.field_name}: {self.source_value!r} -> {self.canonical_value!r}"
