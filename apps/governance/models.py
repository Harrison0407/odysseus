from django.conf import settings
from django.db import models

from apps.core.models import BaseModel

# ---------------------------------------------------------------------------
# DT Beach Controlled Transparency, Commercial Confidentiality & Authorization
# Foundation (one-shot release addendum).
#
# Party is deliberately separate from Role: a Party (an organization, an
# individual, or an operational site) may exercise different Roles in
# different scopes (package, project, organization context) without ever
# being permanently hard-coded as "the factory" or "the forwarder". Role is
# in turn separate from Capability: holding a role does not by itself grant
# every action — sensitive actions (approve, authorize, export, disclose)
# require an explicit CapabilityGrant, never a role-implied default.
# ---------------------------------------------------------------------------


class Classification(models.TextChoices):
    """Central confidentiality classification (spec section 5). A derived
    resource may inherit the same classification or become MORE
    restrictive automatically; it must never become less restrictive
    without an explicit authorized disclosure/redaction decision — that
    rule is enforced in code (see DisclosureGrant / DerivedArtifact),
    not by this enum alone."""

    CHINA_INTERNAL = "china_internal", "Interno China"
    SOURCE_PRIVATE = "source_private", "Privado de origen (fábrica)"
    TRADING_COMPANY_CONFIDENTIAL = "trading_company_confidential", "Confidencial de la comercializadora"
    SUPPLIER_SHARED = "supplier_shared", "Compartido con proveedor"
    CLIENT_PROJECT = "client_project", "Proyecto de cliente"
    CLIENT_SHARED = "client_shared", "Compartido con cliente"
    OPERATIONAL_SHARED = "operational_shared", "Compartido operativo"
    RESTRICTED_FINANCE = "restricted_finance", "Financiero restringido"
    LEGAL_REQUIRED = "legal_required", "Requerido legalmente"


# Rank used only to detect "less restrictive" drift on derived artifacts —
# never used to grant access by itself. Lower index = more freely shared.
CLASSIFICATION_RANK = [
    Classification.CLIENT_SHARED,
    Classification.OPERATIONAL_SHARED,
    Classification.SUPPLIER_SHARED,
    Classification.CLIENT_PROJECT,
    Classification.LEGAL_REQUIRED,
    Classification.TRADING_COMPANY_CONFIDENTIAL,
    Classification.RESTRICTED_FINANCE,
    Classification.CHINA_INTERNAL,
    Classification.SOURCE_PRIVATE,
]


def is_less_restrictive(candidate: str, source: str) -> bool:
    try:
        return CLASSIFICATION_RANK.index(candidate) < CLASSIFICATION_RANK.index(source)
    except ValueError:
        return False


class VisibilityMode(models.TextChoices):
    CONTROLLED_TRANSPARENCY = "controlled_transparency", "Transparencia controlada"
    CONTROLLED_CONFIDENTIALITY = "controlled_confidentiality", "Confidencialidad controlada"


# Package-scoped role codes named by the release (spec section 1). Stored
# as free text (not a DB enum) so a future role never requires a schema
# migration — this list is only the known/seedable set for admin UIs.
KNOWN_ROLE_CODES = [
    ("buyer", "Comprador"),
    ("buyer_approver", "Aprobador del comprador"),
    ("seller_of_record", "Vendedor de registro"),
    ("exporter_of_record", "Exportador de registro"),
    ("china_procurement_operator", "Operador de compras en China"),
    ("production_factory", "Fábrica de producción"),
    ("production_site", "Sitio de producción"),
    ("logistics_operator", "Operador logístico"),
    ("consolidation_warehouse", "Almacén de consolidación"),
    ("quality_operator", "Operador de calidad"),
    ("inspector", "Inspector"),
    ("laboratory", "Laboratorio"),
    ("technical_authority", "Autoridad técnica"),
    ("non_financial_approver", "Aprobador no financiero"),
    ("data_entry_clerk", "Auxiliar de captura de datos"),
    ("commission_agent", "Agente de comisión"),
    ("installer", "Instalador"),
    ("importer_of_record", "Importador de registro"),
    ("consignee", "Consignatario"),
    ("notify_party", "Parte a notificar"),
    ("customs_broker", "Agente aduanal"),
    ("external_service_provider", "Proveedor de servicio externo"),
    ("finance_partner", "Socio financiero"),
]

# Baseline VIEW-type capabilities implied simply by holding an active role
# assignment — deliberately limited to low-risk read access. Every
# APPROVE_*/AUTHORIZE_*/EXPORT_*/VIEW_PRIVILEGED_AUDIT-type action is
# intentionally absent here: those always require an explicit
# CapabilityGrant (spec section 2 — "role membership alone must not grant
# all actions"), never a role-implied default.
ROLE_DEFAULT_CAPABILITIES = {
    "china_procurement_operator": {
        "VIEW_FACTORY_IDENTITY", "VIEW_FACTORY_ADDRESS", "VIEW_FACTORY_CONTACT", "VIEW_FACTORY_QUOTE",
        "VIEW_ORIGIN_COST", "VIEW_INTERNAL_COST_COMPONENTS", "VIEW_MARKUP", "VIEW_MARGIN",
        "CREATE_COMMERCIAL_DOCUMENT", "CREATE_EVIDENCE", "VIEW_MONETIZATION",
    },
    "production_factory": {"CREATE_EVIDENCE"},
    "production_site": {"CREATE_EVIDENCE"},
    "quality_operator": {"CREATE_EVIDENCE", "VERIFY_EVIDENCE"},
    "inspector": {"CREATE_EVIDENCE", "VERIFY_EVIDENCE"},
    "laboratory": {"CREATE_EVIDENCE", "VERIFY_EVIDENCE"},
    "buyer": {"VIEW_CLIENT_QUOTE"},
    "buyer_approver": {"VIEW_CLIENT_QUOTE"},
    "seller_of_record": {"VIEW_CLIENT_QUOTE"},
    "technical_authority": {"VIEW_CLIENT_QUOTE"},
    "installer": {"CREATE_EVIDENCE"},
}

ALL_CAPABILITY_CODES = [
    "VIEW_FACTORY_IDENTITY", "VIEW_FACTORY_ADDRESS", "VIEW_FACTORY_CONTACT", "VIEW_FACTORY_QUOTE",
    "VIEW_ORIGIN_COST", "VIEW_INTERNAL_COST_COMPONENTS", "VIEW_MARKUP", "VIEW_MARGIN", "VIEW_CLIENT_QUOTE",
    "VIEW_MONETIZATION", "VIEW_RESTRICTED_FINANCE", "CREATE_EVIDENCE", "VERIFY_EVIDENCE",
    "CREATE_COMMERCIAL_DOCUMENT", "APPROVE_CLIENT_QUOTE", "APPROVE_TECHNICAL_SPEC", "APPROVE_PAYMENT_ACTION",
    "APPROVE_GATE", "AUTHORIZE_EXCEPTION", "APPROVE_ROLE_CHANGE", "APPROVE_VISIBILITY_CHANGE",
    "AUTHORIZE_DISCLOSURE", "EXPORT_COMMERCIAL_DATA", "VIEW_PRIVILEGED_AUDIT", "RESPOND_TO_CLAIM",
    "VERIFY_CORRECTIVE_WORK", "MANAGE_RISK_FLAGS",
    # Milestone 1 Increment 1 (apps.procurement_gates) -- never implied by
    # any role default (ROLE_DEFAULT_CAPABILITIES), per Charter Section 13.
    "PUBLISH_GATE_POLICY", "CREATE_PROCUREMENT_GATE_ATTEMPT",
    "ASSIGN_GATE_POLICY", "EXEMPT_PACKAGE_FROM_PROCUREMENT_GATES",
]


class Party(BaseModel):
    """A Party represents an organization, an individual, or an
    operational site — never permanently hard-coded as "Factory",
    "Trader", "Forwarder", etc. The same Party may hold different
    RoleAssignments in different packages/projects. A Party may
    optionally wrap an existing `accounts.Organization` (when the party
    is itself a tenant, e.g. the buyer) or an existing
    `procurement.Supplier` row (when the party is a vendor/factory
    already tracked there) — never a duplicate, parallel identity
    record when a perfectly good one already exists."""

    class PartyType(models.TextChoices):
        ORGANIZATION = "organization", "Organización"
        INDIVIDUAL = "individual", "Individuo"
        SITE = "site", "Sitio operativo"

    party_type = models.CharField(max_length=20, choices=PartyType.choices, default=PartyType.ORGANIZATION)
    display_name = models.CharField(max_length=255, help_text="Internal working name — never assumed client-safe by itself.")
    legal_name = models.CharField(max_length=255, blank=True)

    organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.SET_NULL, null=True, blank=True, related_name="parties",
        help_text="Set when this Party is itself a tenant organization (e.g. the buyer).",
    )
    supplier = models.ForeignKey(
        "procurement.Supplier", on_delete=models.SET_NULL, null=True, blank=True, related_name="parties",
        help_text="Set when this Party wraps an existing vendor/factory Supplier record.",
    )

    hosting_organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.CASCADE, related_name="hosted_parties",
        help_text="The tenant this Party record lives under for isolation purposes — the organization "
        "administering the relationship, not necessarily the Party's own legal owner.",
    )
    is_hidden_by_default = models.BooleanField(
        default=False,
        help_text="True for upstream sources (factories, contacts) that must never be client-visible "
        "without an explicit Disclosure Grant, regardless of visibility mode.",
    )
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["display_name"]
        verbose_name_plural = "parties"

    def __str__(self):
        return self.display_name


class PartyMembership(BaseModel):
    """Which logged-in users act on behalf of a Party. A Party (e.g. a
    trading company) may have several member users; a user may act for
    more than one Party over time."""

    party = models.ForeignKey(Party, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="party_memberships")
    is_primary_contact = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = [("party", "user")]

    def __str__(self):
        return f"{self.user} @ {self.party}"


class RoleAssignment(BaseModel):
    """A Party exercising a role_code within a scope (package/project/
    organization context/shipment), with effective dates and immutable
    version history — reassigning a role never edits a prior row, it
    supersedes it (same versioned-immutable-row pattern used throughout
    this system for Drawing/OrderLineAllocation/EvidenceClassification/
    UnitPlanTemplate)."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Activo"
        SUSPENDED = "suspended", "Suspendido"
        ENDED = "ended", "Finalizado"

    party = models.ForeignKey(Party, on_delete=models.CASCADE, related_name="role_assignments")
    role_code = models.SlugField(max_length=60, help_text="Free-text role code — new roles never require a schema change.")

    organization_context = models.ForeignKey(
        "accounts.Organization", on_delete=models.CASCADE, related_name="role_assignments",
        help_text="The hosting/administering organization this assignment is scoped under.",
    )
    project = models.ForeignKey("projects.Project", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    package = models.ForeignKey(
        "procurement.ProcurementPackage", on_delete=models.CASCADE, null=True, blank=True, related_name="role_assignments",
    )
    shipment = models.ForeignKey("shipments.Shipment", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    effective_from = models.DateField()
    effective_until = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    client_visible = models.BooleanField(
        default=False, help_text="Whether this Party's participation in this role is itself visible to client-side users.",
    )

    version = models.PositiveIntegerField(default=1)
    supersedes = models.OneToOneField(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="superseded_by",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.party} as {self.role_code} @ {self.package or self.project or self.organization_context}"

    def is_currently_active(self, *, on_date=None) -> bool:
        from django.utils import timezone

        on_date = on_date or timezone.now().date()
        if self.status != self.Status.ACTIVE:
            return False
        if self.effective_from and self.effective_from > on_date:
            return False
        if self.effective_until and self.effective_until < on_date:
            return False
        return True


class CapabilityGrant(BaseModel):
    """An explicit, auditable, revocable capability grant. Sensitive
    actions (approve, authorize, export, view privileged audit) are
    NEVER implied merely by role membership — they only ever come from
    a row here, scoped to a role assignment (and therefore to whatever
    package/project that assignment covers) or directly to a user."""

    role_assignment = models.ForeignKey(
        RoleAssignment, on_delete=models.CASCADE, null=True, blank=True, related_name="capability_grants",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name="capability_grants",
        help_text="Set when granted directly to a user rather than via a role assignment.",
    )
    capability_code = models.CharField(max_length=60)

    package = models.ForeignKey(
        "procurement.ProcurementPackage", on_delete=models.CASCADE, null=True, blank=True, related_name="capability_grants",
    )
    organization = models.ForeignKey("accounts.Organization", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    granted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    effective_from = models.DateField(null=True, blank=True)
    effective_until = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.capability_code} -> {self.user or self.role_assignment}"

    def is_currently_active(self, *, on_date=None) -> bool:
        from django.utils import timezone

        on_date = on_date or timezone.now().date()
        if not self.is_active:
            return False
        if self.effective_from and self.effective_from > on_date:
            return False
        if self.effective_until and self.effective_until < on_date:
            return False
        return True


class DisclosureGrant(BaseModel):
    """Partial, explicit, revocable disclosure of specific fields from a
    hidden source Party to a recipient organization/package — never a
    blanket "reveal everything" action. Revocation prevents future
    access but never deletes historical audit events."""

    source_party = models.ForeignKey(Party, on_delete=models.CASCADE, related_name="disclosure_grants")
    package = models.ForeignKey("procurement.ProcurementPackage", on_delete=models.CASCADE, related_name="disclosure_grants")

    recipient_organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE, related_name="+")
    recipient_party = models.ForeignKey(Party, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    field_scope = models.JSONField(
        default=list, help_text='List of permitted field codes, e.g. ["manufacturer_name"].',
    )
    classification_before = models.CharField(max_length=40, choices=Classification.choices)
    permitted_projection = models.JSONField(default=dict, blank=True, help_text="Frozen client-safe projection actually released.")

    reason = models.TextField()
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    effective_from = models.DateTimeField()
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Disclosure: {self.source_party} -> {self.recipient_organization} ({self.field_scope})"

    def is_currently_active(self, *, at=None) -> bool:
        from django.utils import timezone

        at = at or timezone.now()
        if self.revoked_at is not None:
            return False
        if self.effective_from > at:
            return False
        if self.expires_at is not None and self.expires_at < at:
            return False
        return True


class ChangeRequest(BaseModel):
    """A versioned request to alter a critical, frozen package term
    (seller of record, exporter of record, China procurement operator,
    production factory/site, visibility mode, incoterm, currency,
    payment terms, approved specification/drawing, evidence policy).
    Creating this never itself applies the change — approval does."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pendiente"
        APPROVED = "approved", "Aprobado"
        REJECTED = "rejected", "Rechazado"
        WITHDRAWN = "withdrawn", "Retirado"

    package = models.ForeignKey("procurement.ProcurementPackage", on_delete=models.CASCADE, related_name="change_requests")
    field_name = models.CharField(max_length=100, help_text='E.g. "exporter_of_record", "visibility_mode".')
    frozen_current_value = models.TextField(blank=True)
    proposed_new_value = models.TextField(blank=True)
    reason = models.TextField()
    affected_relationships = models.TextField(blank=True)
    risk_impact = models.TextField(blank=True)

    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_comment = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Change request: {self.field_name} on {self.package} ({self.status})"


class RiskFlag(BaseModel):
    """Foundation-only risk signal (spec section 17). Records risk and
    may trigger review/hold — never declares legality, approves an
    opaque transaction, or replaces legal/compliance review."""

    class Level(models.TextChoices):
        STANDARD = "standard", "Estándar"
        CONTROLLED_OPAQUE = "controlled_opaque", "Opaco controlado"
        HIGH_RISK = "high_risk", "Alto riesgo"

    package = models.ForeignKey("procurement.ProcurementPackage", on_delete=models.CASCADE, related_name="risk_flags")
    level = models.CharField(max_length=20, choices=Level.choices, default=Level.STANDARD)
    indicator_codes = models.JSONField(default=list, blank=True)
    notes = models.TextField(blank=True)
    raised_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_level_display()} — {self.package}"


class DerivedArtifact(BaseModel):
    """A translation, summary, analysis, redaction, client-safe document,
    or report derived from a source object. Preserves source
    version/hash/classification and the exact authorized projection
    that was actually sent to the transformation step — never the full
    confidential source. Never silently merged/substituted for the
    original; becomes `is_stale` when the source changes; superseding
    creates a new row, never edits history."""

    class ArtifactType(models.TextChoices):
        TRANSLATION = "translation", "Traducción"
        SUMMARY = "summary", "Resumen"
        ANALYSIS = "analysis", "Análisis"
        REDACTION = "redaction", "Redacción/versión sanitizada"
        CLIENT_SAFE_DOCUMENT = "client_safe_document", "Documento seguro para cliente"
        REPORT = "report", "Reporte"

    class ReviewStatus(models.TextChoices):
        DRAFT = "draft", "Borrador"
        REVIEWED = "reviewed", "Revisado"
        APPROVED = "approved", "Aprobado"

    content_type = models.ForeignKey("contenttypes.ContentType", on_delete=models.CASCADE, related_name="+")
    object_id = models.UUIDField()

    source_version = models.CharField(max_length=100, blank=True)
    source_hash = models.CharField(max_length=64, blank=True)
    source_classification = models.CharField(max_length=40, choices=Classification.choices)
    authorized_source_projection = models.JSONField(
        default=dict, help_text="The exact field set actually sent to the transformation step — never the full source.",
    )

    artifact_type = models.CharField(max_length=30, choices=ArtifactType.choices)
    language = models.CharField(max_length=10, blank=True)
    author_or_model = models.CharField(max_length=150, help_text='E.g. a username, or "mock-translate-v1".')
    generated_at = models.DateTimeField(auto_now_add=True)
    policy_version = models.CharField(max_length=20, default="1")

    classification = models.CharField(max_length=40, choices=Classification.choices)
    body_text = models.TextField(blank=True)

    review_status = models.CharField(max_length=20, choices=ReviewStatus.choices, default=ReviewStatus.DRAFT)
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    is_stale = models.BooleanField(default=False)

    supersedes = models.OneToOneField(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="superseded_by",
    )

    class Meta:
        ordering = ["-generated_at"]
        indexes = [models.Index(fields=["content_type", "object_id"])]

    def __str__(self):
        return f"{self.get_artifact_type_display()} ({self.language}) of {self.content_type} {self.object_id}"
