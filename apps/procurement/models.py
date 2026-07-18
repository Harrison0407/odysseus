from django.conf import settings
from django.db import models

from apps.core.models import BaseModel, DestinationScope


class Supplier(BaseModel):
    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE, related_name="suppliers")
    name = models.CharField(max_length=255)
    country = models.CharField(max_length=100, blank=True)
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
