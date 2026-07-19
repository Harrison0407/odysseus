from django.conf import settings
from django.db import models

from apps.core.models import BaseModel, DestinationScope
from apps.governance.models import Classification, VisibilityMode


class Supplier(BaseModel):
    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE, related_name="suppliers")
    name = models.CharField(max_length=255)
    country = models.CharField(max_length=100, blank=True)
    address = models.TextField(blank=True, help_text="Registered/production-site address — confidentiality-sensitive for hidden factories.")
    default_currency = models.CharField(max_length=3, default="USD")
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class SupplierContact(BaseModel):
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name="contacts")
    name = models.CharField(max_length=150)
    role = models.CharField(max_length=150, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)

    def __str__(self):
        return f"{self.name} ({self.supplier})"


class Quotation(BaseModel):
    """A supplier PI/quotation such as W5082, W5076, W5097, W5084 in the
    live fixture. Note from source-package-analysis.md: the PI header for
    W5084 says PROJECT NAME "ARENA" while the matched local PO says
    building "SOLE 26" — both are preserved as given; `project` here
    records the PI's own stated project, not a reconciled value."""

    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE, related_name="quotations")
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="quotations")
    reference = models.CharField(max_length=100, help_text='E.g. "W5082", "PI-60044678".')
    project_label_on_document = models.CharField(
        max_length=150, blank=True, help_text="Project name exactly as printed on the PI, unreconciled."
    )
    project = models.ForeignKey(
        "projects.Project", on_delete=models.SET_NULL, null=True, blank=True, related_name="quotations"
    )
    currency = models.CharField(max_length=3, default="USD")
    total_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    issued_date = models.DateField(null=True, blank=True)
    valid_until = models.DateField(null=True, blank=True)
    trade_terms = models.CharField(max_length=50, blank=True, help_text="EXW, FOB, C&I, CIF, etc.")
    source_document = models.ForeignKey(
        "documents.Document", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    # Controlled Transparency / Confidentiality foundation — a Quotation
    # already models exactly what a "Factory Quote" is (a supplier's PI/
    # quotation); rather than a duplicate object, it is reused directly
    # and scoped to a package with an explicit classification. Legacy
    # rows (no package) default to the system's pre-existing implicit
    # "visible within organization" behavior.
    package = models.ForeignKey(
        "ProcurementPackage", on_delete=models.SET_NULL, null=True, blank=True, related_name="factory_quotes",
    )
    factory_party = models.ForeignKey(
        "governance.Party", on_delete=models.SET_NULL, null=True, blank=True, related_name="factory_quotes",
    )
    classification = models.CharField(
        max_length=40, choices=Classification.choices, default=Classification.OPERATIONAL_SHARED, blank=True,
    )

    class Meta:
        ordering = ["-issued_date"]

    def __str__(self):
        return f"{self.reference} ({self.supplier})"


class QuotationLine(BaseModel):
    quotation = models.ForeignKey(Quotation, on_delete=models.CASCADE, related_name="lines")
    line_no = models.PositiveIntegerField(default=1)
    description = models.TextField()
    item = models.ForeignKey(
        "items.Item", on_delete=models.SET_NULL, null=True, blank=True, related_name="quotation_lines"
    )
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    unit_of_measure = models.ForeignKey("items.UnitOfMeasure", on_delete=models.PROTECT, related_name="+")
    unit_price = models.DecimalField(max_digits=14, decimal_places=4)
    line_total = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        ordering = ["line_no"]

    def __str__(self):
        return f"{self.quotation}: {self.description[:40]}"


class PurchaseOrder(BaseModel):
    """A local Markeris PO such as DT-BEACH786, DT-BEACH793, DT-BEACH804,
    DT-BEACH1111, or PO 1260 in the fixture. Every local PO in the fixture
    already carries a "RECEIVED IN FULL" stamp applied before physical
    arrival — this field never feeds `Receipt`; only apps.receiving.Receipt
    records physical receipt (core principle 4.3, 4.5)."""

    class ApprovalStatus(models.TextChoices):
        DRAFT = "draft", "Borrador"
        PENDING_APPROVAL = "pending_approval", "Pendiente de aprobación"
        APPROVED = "approved", "Aprobada"
        REJECTED = "rejected", "Rechazada"

    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE, related_name="purchase_orders")
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="purchase_orders")
    quotation = models.ForeignKey(
        Quotation, on_delete=models.SET_NULL, null=True, blank=True, related_name="purchase_orders"
    )
    po_number = models.CharField(max_length=100, help_text='E.g. "DT-BEACH786".')
    project = models.ForeignKey(
        "projects.Project", on_delete=models.SET_NULL, null=True, blank=True, related_name="purchase_orders"
    )
    building = models.ForeignKey(
        "projects.Building", on_delete=models.SET_NULL, null=True, blank=True, related_name="purchase_orders"
    )
    usage_note_on_document = models.CharField(
        max_length=255, blank=True, help_text='Verbatim "USO: ..." text from the local PO, e.g. "USO: GENERAL DEL PROYECTO".'
    )
    requested_by = models.CharField(max_length=150, blank=True)
    approval_status = models.CharField(max_length=30, choices=ApprovalStatus.choices, default=ApprovalStatus.DRAFT)
    order_date = models.DateField(null=True, blank=True)
    currency = models.CharField(max_length=3, default="USD")
    total_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    intended_destination_scope = models.CharField(
        max_length=30, choices=DestinationScope.choices, blank=True,
        default=DestinationScope.UNKNOWN_PENDING,
    )
    source_document = models.ForeignKey(
        "documents.Document", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    document_marked_received_in_full = models.BooleanField(
        default=False,
        help_text="True if the source PO document itself bears a 'RECEIVED IN FULL' stamp. This "
        "is metadata about the document, never treated as physical receiving evidence.",
    )

    class POKind(models.TextChoices):
        STANDARD = "standard", "Estándar"
        CLIENT = "client", "Orden de compra del cliente"
        UPSTREAM_FACTORY = "upstream_factory", "Orden de compra a fábrica (aguas arriba)"

    package = models.ForeignKey(
        "ProcurementPackage", on_delete=models.SET_NULL, null=True, blank=True, related_name="purchase_orders_in_package",
    )
    po_kind = models.CharField(max_length=20, choices=POKind.choices, default=POKind.STANDARD)
    classification = models.CharField(
        max_length=40, choices=Classification.choices, default=Classification.OPERATIONAL_SHARED, blank=True,
    )

    class Meta:
        ordering = ["-order_date"]

    def __str__(self):
        return self.po_number


class PurchaseOrderRevision(BaseModel):
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="revisions")
    revision_number = models.PositiveIntegerField()
    reason = models.CharField(max_length=255, blank=True)
    document = models.ForeignKey(
        "documents.Document", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        unique_together = [("purchase_order", "revision_number")]
        ordering = ["revision_number"]


class PurchaseOrderLine(BaseModel):
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="lines")
    quotation_line = models.ForeignKey(
        QuotationLine, on_delete=models.SET_NULL, null=True, blank=True, related_name="purchase_order_lines"
    )
    line_no = models.PositiveIntegerField(default=1)
    description = models.TextField()
    item = models.ForeignKey(
        "items.Item", on_delete=models.SET_NULL, null=True, blank=True, related_name="purchase_order_lines"
    )
    quantity_ordered = models.DecimalField(max_digits=14, decimal_places=3)
    required_quantity = models.DecimalField(
        max_digits=14, decimal_places=3, null=True, blank=True,
        help_text="The actually-needed quantity, when known, for shortage calculation — never "
        "assumed equal to quantity_ordered when not explicitly recorded.",
    )
    unit_of_measure = models.ForeignKey("items.UnitOfMeasure", on_delete=models.PROTECT, related_name="+")
    unit_price = models.DecimalField(max_digits=14, decimal_places=4)
    line_total = models.DecimalField(max_digits=14, decimal_places=2)
    destination_scope = models.CharField(max_length=30, blank=True)
    building = models.ForeignKey(
        "projects.Building", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["line_no"]

    def __str__(self):
        return f"{self.purchase_order}: {self.description[:40]}"


class OrderLineAllocation(BaseModel):
    """A split destination allocation for a `PurchaseOrderLine` (spec:
    "order destination allocation" — a line can be split across
    building family/physical building/floor/unit/room/common area).
    Reassignment never deletes the original row: a reassignment closes
    this one (`is_active=False`) and creates a new row pointing back at
    it via `reassigned_from`, so the original planned destination stays
    visible in history (core principle 4.4)."""

    purchase_order_line = models.ForeignKey(PurchaseOrderLine, on_delete=models.CASCADE, related_name="allocations")
    destination_scope = models.CharField(max_length=30, choices=DestinationScope.choices)
    building_family = models.ForeignKey("projects.BuildingFamily", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    building = models.ForeignKey("projects.Building", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    floor = models.ForeignKey("projects.Floor", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    unit = models.ForeignKey("projects.Unit", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    area = models.ForeignKey("projects.Area", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    drawing = models.ForeignKey(
        "drawings.Drawing", on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        help_text="The exact drawing revision used for this allocation — never silently replaced by a later revision.",
    )
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    reassigned_from = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="reassigned_to_set",
    )
    reassignment_reason = models.TextField(blank=True)
    reassigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    reassigned_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.purchase_order_line}: {self.quantity} -> {self.get_destination_scope_display()}"


class PurchasedSpare(BaseModel):
    """Authorized confirmation that an order line's excess quantity is a
    deliberate purchased spare, not silently-unallocated stock (spec:
    "explicitly confirmed purchased-spare quantity"). This is the
    *authorization* record — once physically received, the resulting
    `InventoryLot` (via `apps.receiving.services.post_receipt_line`)
    links back here (`InventoryLot.purchased_spare`) and all
    quantity-received/available/reserved/consumed figures are read from
    the existing ledger/reservation architecture, never duplicated as
    directly-editable fields here (core principle 4.5 — never a second
    inventory source of truth)."""

    purchase_order_line = models.ForeignKey(PurchaseOrderLine, on_delete=models.PROTECT, related_name="purchased_spares")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    compatible_typology = models.CharField(max_length=255, blank=True, help_text="Compatibility / intended typology.")
    reason = models.TextField()
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    confirmed_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Repuesto comprado: {self.quantity} — {self.purchase_order_line}"


class PaymentTerm(BaseModel):
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="payment_terms")
    description = models.CharField(max_length=255, help_text='E.g. "30% deposit before production".')

    def __str__(self):
        return self.description


class PaymentMilestone(BaseModel):
    class Status(models.TextChoices):
        NOT_REQUIRED_YET = "not_required_yet", "Aún no requerido"
        PENDING_APPROVAL = "pending_approval", "Pendiente de aprobación"
        APPROVED = "approved", "Aprobado para pago"
        PARTIALLY_PAID = "partially_paid", "Parcialmente pagado"
        PAID = "paid", "Pagado"
        ON_HOLD = "on_hold", "En espera"
        REFUNDED = "refunded", "Reembolsado"
        UNKNOWN_HISTORICAL = "unknown_historical", "Desconocido (histórico)"

    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="payment_milestones")
    name = models.CharField(max_length=150, help_text='E.g. "Depósito 30%", "Saldo antes de embarque".')
    percentage = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    amount_due = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.NOT_REQUIRED_YET)
    due_date = models.DateField(null=True, blank=True)

    def __str__(self):
        return f"{self.purchase_order} — {self.name}"


class PaymentRecord(BaseModel):
    milestone = models.ForeignKey(PaymentMilestone, on_delete=models.CASCADE, related_name="payments")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3, default="USD")
    payment_date = models.DateField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    reference = models.CharField(max_length=150, blank=True, help_text="Bank reference / transaction id.")

    def __str__(self):
        return f"{self.milestone} — {self.amount} {self.currency}"


class PaymentEvidence(BaseModel):
    payment = models.ForeignKey(PaymentRecord, on_delete=models.CASCADE, related_name="evidence")
    document = models.ForeignKey("documents.Document", on_delete=models.PROTECT, related_name="+")

    def __str__(self):
        return f"Evidence for {self.payment}"


class OpenPurchaseOrderRelease(BaseModel):
    """Spec section 13A.4 / 23: a PO/PI may remain open across several
    shipments/deliveries. Each release records a partial fulfillment
    against the (still open) parent order."""

    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="releases")
    purchase_order_line = models.ForeignKey(
        PurchaseOrderLine, on_delete=models.CASCADE, related_name="releases", null=True, blank=True
    )
    quantity_released = models.DecimalField(max_digits=14, decimal_places=3)
    released_at = models.DateTimeField(null=True, blank=True)
    shipment = models.ForeignKey(
        "shipments.Shipment", on_delete=models.SET_NULL, null=True, blank=True, related_name="po_releases"
    )
    notes = models.TextField(blank=True)

    def __str__(self):
        return f"{self.purchase_order} release {self.quantity_released}"


# ---------------------------------------------------------------------------
# DT Beach Controlled Transparency, Commercial Confidentiality & Authorization
# Foundation — commercial layers (spec section 6) and visibility modes
# (spec section 4). Six distinct commercial records, never one record
# whose columns are merely hidden in the UI: Factory RFQ (below), Factory
# Quote (reuses Quotation/QuotationLine directly — already models exactly
# that), Internal Commercial Sheet, Client Quote, Client Purchase Order
# and Upstream Factory Purchase Order (both reuse PurchaseOrder, `po_kind`
# distinguishes them).
# ---------------------------------------------------------------------------


class ProcurementPackage(BaseModel):
    """A confidentiality/visibility scope wrapping one commercial
    relationship — e.g. one buyer, one seller of record, one China
    procurement operator, one (possibly hidden) production factory.
    `organization` is the hosting/administering tenant (typically the
    China procurement operator's own organization); other parties
    participate through package-scoped `governance.RoleAssignment`
    rows, never by owning this row themselves."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Borrador"
        ACTIVE = "active", "Activo"
        FROZEN = "frozen", "Congelado"
        CLOSED = "closed", "Cerrado"

    organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.CASCADE, related_name="procurement_packages",
        help_text="The hosting/administering organization for this package.",
    )
    project = models.ForeignKey(
        "projects.Project", on_delete=models.SET_NULL, null=True, blank=True, related_name="procurement_packages",
    )
    code = models.SlugField(max_length=80)
    name = models.CharField(max_length=255)

    visibility_mode = models.CharField(
        max_length=40, choices=VisibilityMode.choices, default=VisibilityMode.CONTROLLED_CONFIDENTIALITY,
        help_text="For China-managed DT Beach procurement, CONTROLLED_CONFIDENTIALITY is the configured default.",
    )
    visibility_mode_version = models.PositiveIntegerField(default=1)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    is_frozen = models.BooleanField(default=False)
    frozen_at = models.DateTimeField(null=True, blank=True)
    frozen_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    frozen_snapshot = models.JSONField(
        default=dict, blank=True,
        help_text="Critical terms frozen at freeze time: role party ids, incoterm, currency, payment terms, "
        "specification/drawing revision, evidence policy — altering any of these afterward requires a "
        "governance.ChangeRequest, never a direct edit.",
    )
    is_on_hold = models.BooleanField(default=False, help_text="True while a Change Request against a frozen term is pending.")

    notes = models.TextField(blank=True)

    class Meta:
        unique_together = [("organization", "code")]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.get_visibility_mode_display()})"


class FactoryRFQ(BaseModel):
    """A request sent to a factory before it has quoted — distinct from
    the resulting Factory Quote (`Quotation`), never the same record
    with empty price columns."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Borrador"
        SENT = "sent", "Enviado"
        QUOTED = "quoted", "Cotizado"
        CANCELLED = "cancelled", "Cancelado"

    package = models.ForeignKey(ProcurementPackage, on_delete=models.CASCADE, related_name="factory_rfqs")
    factory_party = models.ForeignKey(
        "governance.Party", on_delete=models.SET_NULL, null=True, blank=True, related_name="factory_rfqs",
    )
    requested_spec = models.TextField()
    quantity = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    target_price = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    currency = models.CharField(max_length=3, default="USD")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    classification = models.CharField(max_length=40, choices=Classification.choices, default=Classification.SOURCE_PRIVATE)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"RFQ — {self.package}"


class InternalCommercialSheet(BaseModel):
    """Factory price, full landed-cost breakdown, markup method/value and
    recommended sell price — never visible to the client, never merged
    into the Client Quote as hidden columns."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Borrador"
        PENDING_APPROVAL = "pending_approval", "Pendiente de aprobación"
        APPROVED = "approved", "Aprobado"

    package = models.ForeignKey(ProcurementPackage, on_delete=models.CASCADE, related_name="internal_commercial_sheets")
    source_quotation = models.ForeignKey(
        Quotation, on_delete=models.SET_NULL, null=True, blank=True, related_name="commercial_sheets",
    )

    factory_price = models.DecimalField(max_digits=14, decimal_places=4)
    currency = models.CharField(max_length=3, default="USD")
    exchange_rate_note = models.CharField(max_length=255, blank=True)
    inland_transport = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    inspection_qc = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    consolidation = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    freight = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    insurance = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    duties_taxes = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    administration = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    contingency = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    markup_method = models.CharField(max_length=50, blank=True, help_text='E.g. "percentage_on_landed_cost", "fixed_fee".')
    markup_value = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    recommended_sell_price = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)

    status = models.CharField(max_length=30, choices=Status.choices, default=Status.DRAFT)
    prepared_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    classification = models.CharField(max_length=40, choices=Classification.choices, default=Classification.TRADING_COMPANY_CONFIDENTIAL)

    class Meta:
        ordering = ["-created_at"]

    @property
    def landed_cost(self):
        return (
            (self.factory_price or 0) + self.inland_transport + self.inspection_qc + self.consolidation
            + self.freight + self.insurance + self.duties_taxes + self.administration + self.contingency
        )

    @property
    def margin_amount(self):
        if self.recommended_sell_price is None:
            return None
        return self.recommended_sell_price - self.landed_cost

    def __str__(self):
        return f"Hoja comercial interna — {self.package}"


class ClientQuote(BaseModel):
    """Only approved client-facing information — visible seller,
    product/spec, quantity, sell price, terms, approved documents/
    verification statements. Never exposes hidden factory identity,
    source cost, internal calculations, markup, margin, or upstream
    document ids — those simply are not fields on this model."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Borrador"
        APPROVED = "approved", "Aprobado"
        SENT = "sent", "Enviado al cliente"

    package = models.ForeignKey(ProcurementPackage, on_delete=models.CASCADE, related_name="client_quotes")
    source_internal_sheet = models.ForeignKey(
        InternalCommercialSheet, on_delete=models.SET_NULL, null=True, blank=True, related_name="client_quotes",
        help_text="Internal traceability only — must never be exposed through any client-facing view/serializer/export.",
    )

    visible_seller_party = models.ForeignKey(
        "governance.Party", on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        help_text="The client-visible seller of record, e.g. the trading company.",
    )
    product_description = models.TextField()
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    sell_price = models.DecimalField(max_digits=14, decimal_places=4)
    currency = models.CharField(max_length=3, default="USD")
    client_facing_terms = models.TextField(blank=True)
    delivery_terms = models.CharField(max_length=150, blank=True)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    prepared_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    classification = models.CharField(max_length=40, choices=Classification.choices, default=Classification.CLIENT_SHARED)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Cotización al cliente — {self.package}"


class VerificationAssertion(BaseModel):
    """A reusable client-safe verification assertion (spec section 8) —
    e.g. "Production verified at an authorized site." Preserves the
    hidden source Party/evidence bundle internally; the buyer only ever
    sees `client_visible_wording`."""

    package = models.ForeignKey(ProcurementPackage, on_delete=models.CASCADE, related_name="verification_assertions")
    assertion_code = models.SlugField(max_length=80)
    client_visible_wording = models.TextField()

    source_evidence_bundle = models.ForeignKey(
        "audit.EvidenceBundle", on_delete=models.SET_NULL, null=True, blank=True, related_name="verification_assertions",
    )
    source_party = models.ForeignKey(
        "governance.Party", on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        help_text="Hidden internally — never rendered in client_visible_wording.",
    )

    verifier = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    verified_at = models.DateTimeField(null=True, blank=True)
    valid_until = models.DateTimeField(null=True, blank=True)

    classification = models.CharField(max_length=40, choices=Classification.choices, default=Classification.CLIENT_SHARED)
    version = models.PositiveIntegerField(default=1)
    supersedes = models.OneToOneField(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="superseded_by",
    )
    is_revoked = models.BooleanField(default=False)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.assertion_code} — {self.package}"
