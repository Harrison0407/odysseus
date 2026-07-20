"""HTTP surface for ProcurementPackage and its commercial layers
(Controlled Transparency / Confidentiality release). Every view
authorizes through apps.governance.services — never through a bare
`request.user.profile.organization` equality check — since a package's
participants (buyer, seller of record, China procurement operator,
factory) legitimately span more than one organization. An unauthorized
package is a 404, never a 403 that would confirm its existence.
"""

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.audit import services as audit_services
from apps.audit.models import EvidenceBundle
from apps.documents.views import DocumentUploadForm
from apps.governance import services as governance_services
from apps.governance.models import Classification, DisclosureGrant, Party

from . import services
from .models import ClientQuote, FactoryRFQ, InternalCommercialSheet, ProcurementPackage, Quotation, Supplier, VerificationAssertion


def user_can_view_package(request, package) -> bool:
    return governance_services.user_can_access_package(request.user, package)


def _get_package_or_404(request, pk):
    return get_object_or_404(
        ProcurementPackage, pk=pk, id__in=governance_services.authorized_package_ids(request.user)
    )


@login_required
def package_list(request):
    packages = ProcurementPackage.objects.filter(
        id__in=governance_services.authorized_package_ids(request.user)
    ).order_by("-created_at")
    return render(request, "procurement/package_list.html", {"packages": packages})


@login_required
def package_detail(request, pk):
    package = _get_package_or_404(request, pk)

    can_view_factory = governance_services.has_capability(request.user, "VIEW_FACTORY_QUOTE", package=package)
    can_view_factory_identity = governance_services.has_capability(request.user, "VIEW_FACTORY_IDENTITY", package=package)
    can_view_cost = governance_services.has_capability(request.user, "VIEW_INTERNAL_COST_COMPONENTS", package=package)
    can_view_client_quote = governance_services.can_view_classification(request.user, Classification.CLIENT_SHARED, package=package)
    can_manage_client_quote = (
        governance_services.has_capability(request.user, "CREATE_COMMERCIAL_DOCUMENT", package=package)
        or governance_services.has_capability(request.user, "APPROVE_CLIENT_QUOTE", package=package)
    )
    can_authorize_disclosure = governance_services.has_capability(request.user, "AUTHORIZE_DISCLOSURE", package=package)
    can_approve_client_quote = governance_services.has_capability(request.user, "APPROVE_CLIENT_QUOTE", package=package)
    can_freeze = governance_services.has_capability(request.user, "APPROVE_GATE", package=package)

    context = {
        "package": package,
        "can_view_factory": can_view_factory,
        "can_view_factory_identity": can_view_factory_identity,
        "can_view_cost": can_view_cost,
        "can_view_client_quote": can_view_client_quote,
        "can_authorize_disclosure": can_authorize_disclosure,
        "can_approve_client_quote": can_approve_client_quote,
        "can_freeze": can_freeze,
        "factory_quotes": Quotation.objects.filter(package=package).select_related("supplier", "factory_party") if can_view_factory else Quotation.objects.none(),
        "internal_sheets": InternalCommercialSheet.objects.filter(package=package) if can_view_cost else InternalCommercialSheet.objects.none(),
        "client_quotes": (
            ClientQuote.objects.filter(package=package).select_related("visible_seller_party")
            if can_view_client_quote and can_manage_client_quote else
            ClientQuote.objects.filter(package=package, status__in=[ClientQuote.Status.APPROVED, ClientQuote.Status.SENT]).select_related("visible_seller_party")
            if can_view_client_quote else ClientQuote.objects.none()
        ),
        "verification_assertions": VerificationAssertion.objects.filter(
            package=package, is_revoked=False,
        ).filter(Q(valid_until__isnull=True) | Q(valid_until__gte=timezone.now())) if can_view_client_quote or can_view_factory else VerificationAssertion.objects.none(),
        "role_assignments": governance_services.active_role_assignments(request.user, package=package).select_related("party"),
        "change_requests": [
            governance_services.change_request_projection(request.user, cr)
            for cr in package.change_requests.all().only(
                "id", "package", "field_name", "status", "created_at", "requested_by", "decided_by",
            )[:20]
        ],
        "disclosure_grants": package.disclosure_grants.all()[:20] if can_authorize_disclosure else DisclosureGrant.objects.none(),
        "disclosed_projection": governance_services.disclosure_projection_for_user(package, request.user),
    }
    return render(request, "procurement/package_detail.html", context)


class FactoryQuoteForm(forms.Form):
    supplier = forms.ModelChoiceField(queryset=Supplier.objects.none())
    factory_party = forms.ModelChoiceField(queryset=Party.objects.none(), required=False)
    reference = forms.CharField(max_length=100)
    total_amount = forms.DecimalField(required=False, max_digits=14, decimal_places=2)
    currency = forms.CharField(max_length=3, initial="USD")

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["supplier"].queryset = Supplier.objects.filter(organization=organization)
        self.fields["factory_party"].queryset = Party.objects.filter(hosting_organization=organization)


@login_required
def factory_quote_create(request, pk):
    package = _get_package_or_404(request, pk)
    if request.method == "POST":
        form = FactoryQuoteForm(request.POST, organization=package.organization)
        if form.is_valid():
            try:
                quotation = services.submit_factory_quote(
                    package, form.cleaned_data["factory_party"], form.cleaned_data["supplier"], request.user,
                    reference=form.cleaned_data["reference"], currency=form.cleaned_data["currency"],
                    total_amount=form.cleaned_data["total_amount"],
                )
                messages.success(request, "Cotización de fábrica registrada.")
                return redirect("procurement:package-detail", pk=package.pk)
            except services.PackageError as exc:
                messages.error(request, str(exc))
        else:
            messages.error(request, "Revise los datos de la cotización.")
    else:
        form = FactoryQuoteForm(organization=package.organization)
    return render(request, "procurement/factory_quote_form.html", {"form": form, "package": package})


class InternalSheetForm(forms.Form):
    source_quotation = forms.ModelChoiceField(queryset=Quotation.objects.none(), required=False)
    factory_price = forms.DecimalField(max_digits=14, decimal_places=4)
    currency = forms.CharField(max_length=3, initial="USD")
    inland_transport = forms.DecimalField(max_digits=14, decimal_places=2, required=False, initial=0)
    inspection_qc = forms.DecimalField(max_digits=14, decimal_places=2, required=False, initial=0)
    consolidation = forms.DecimalField(max_digits=14, decimal_places=2, required=False, initial=0)
    freight = forms.DecimalField(max_digits=14, decimal_places=2, required=False, initial=0)
    insurance = forms.DecimalField(max_digits=14, decimal_places=2, required=False, initial=0)
    duties_taxes = forms.DecimalField(max_digits=14, decimal_places=2, required=False, initial=0)
    administration = forms.DecimalField(max_digits=14, decimal_places=2, required=False, initial=0)
    contingency = forms.DecimalField(max_digits=14, decimal_places=2, required=False, initial=0)
    markup_method = forms.CharField(max_length=50, required=False)
    markup_value = forms.DecimalField(max_digits=14, decimal_places=4, required=False, initial=0)
    recommended_sell_price = forms.DecimalField(max_digits=14, decimal_places=4, required=False)

    def __init__(self, *args, package=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["source_quotation"].queryset = Quotation.objects.filter(package=package)


@login_required
def commercial_sheet_create(request, pk):
    package = _get_package_or_404(request, pk)
    if request.method == "POST":
        form = InternalSheetForm(request.POST, package=package)
        if form.is_valid():
            data = {k: (v if v is not None else 0) for k, v in form.cleaned_data.items()}
            try:
                sheet = services.create_internal_commercial_sheet(package, request.user, **data)
                messages.success(request, "Hoja comercial interna creada.")
                return redirect("procurement:package-detail", pk=package.pk)
            except services.PackageError as exc:
                messages.error(request, str(exc))
        else:
            messages.error(request, "Revise los datos de la hoja comercial.")
    else:
        form = InternalSheetForm(package=package)
    return render(request, "procurement/commercial_sheet_form.html", {"form": form, "package": package})


class ClientQuoteForm(forms.Form):
    visible_seller_party = forms.ModelChoiceField(queryset=Party.objects.none())
    source_internal_sheet = forms.ModelChoiceField(queryset=InternalCommercialSheet.objects.none(), required=False)
    product_description = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}))
    quantity = forms.DecimalField(max_digits=14, decimal_places=3)
    sell_price = forms.DecimalField(max_digits=14, decimal_places=4)
    currency = forms.CharField(max_length=3, initial="USD")
    client_facing_terms = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)
    delivery_terms = forms.CharField(max_length=150, required=False)

    def __init__(self, *args, package=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["visible_seller_party"].queryset = Party.objects.filter(hosting_organization=package.organization)
        self.fields["source_internal_sheet"].queryset = InternalCommercialSheet.objects.filter(package=package)


@login_required
def client_quote_create(request, pk):
    package = _get_package_or_404(request, pk)
    if request.method == "POST":
        form = ClientQuoteForm(request.POST, package=package)
        if form.is_valid():
            try:
                quote = services.create_client_quote(package, request.user, **form.cleaned_data)
                messages.success(request, "Cotización de cliente preparada.")
                return redirect("procurement:package-detail", pk=package.pk)
            except services.PackageError as exc:
                messages.error(request, str(exc))
        else:
            messages.error(request, "Revise los datos de la cotización.")
    else:
        form = ClientQuoteForm(package=package)
    return render(request, "procurement/client_quote_form.html", {"form": form, "package": package})


@login_required
def client_quote_approve(request, pk):
    quote = get_object_or_404(
        ClientQuote, pk=pk, package_id__in=governance_services.authorized_package_ids(request.user)
    )
    if request.method == "POST":
        try:
            services.approve_client_quote(quote, request.user)
            messages.success(request, "Cotización de cliente aprobada.")
        except services.PackageError as exc:
            messages.error(request, str(exc))
    return redirect("procurement:package-detail", pk=quote.package_id)


@login_required
def package_freeze(request, pk):
    package = _get_package_or_404(request, pk)
    if request.method == "POST":
        try:
            services.freeze_package(
                package, request.user, incoterm=request.POST.get("incoterm", ""),
                currency=request.POST.get("currency", "USD"), payment_terms=request.POST.get("payment_terms", ""),
                evidence_policy=request.POST.get("evidence_policy", ""),
            )
            messages.success(request, "Paquete congelado.")
        except services.PackageError as exc:
            messages.error(request, str(exc))
    return redirect("procurement:package-detail", pk=pk)


@login_required
def change_request_create(request, pk):
    package = _get_package_or_404(request, pk)
    if request.method == "POST":
        try:
            governance_services.request_change(
                package, request.POST.get("field_name", ""), request.POST.get("proposed_new_value", ""),
                request.POST.get("reason", ""), request.user,
                affected_relationships=request.POST.get("affected_relationships", ""),
            )
            messages.success(request, "Solicitud de cambio creada; el paquete queda en espera.")
        except governance_services.AuthorizationDenied as exc:
            messages.error(request, str(exc))
    return redirect("procurement:package-detail", pk=pk)


@login_required
def change_request_decide(request, pk, decision):
    from apps.governance.models import ChangeRequest

    if decision not in {"approve", "reject"}:
        raise Http404
    change_request = get_object_or_404(
        ChangeRequest, pk=pk, package_id__in=governance_services.authorized_package_ids(request.user)
    )
    if request.method == "POST":
        try:
            if decision == "approve":
                governance_services.approve_change_request(change_request, request.user, comment=request.POST.get("comment", ""))
                messages.success(request, "Solicitud de cambio aprobada.")
            else:
                governance_services.reject_change_request(change_request, request.user, comment=request.POST.get("comment", ""))
                messages.success(request, "Solicitud de cambio rechazada.")
        except governance_services.AuthorizationDenied as exc:
            messages.error(request, str(exc))
    return redirect("procurement:package-detail", pk=change_request.package_id)


@login_required
def disclosure_grant_create(request, pk):
    package = _get_package_or_404(request, pk)
    if request.method == "POST":
        field_scope = [f.strip() for f in request.POST.get("field_scope", "").split(",") if f.strip()]
        try:
            source_party = Party.objects.get(pk=request.POST.get("source_party"))
            recipient_organization = request.user.profile.organization.__class__.objects.get(pk=request.POST.get("recipient_organization"))
            governance_services.create_disclosure_grant(
                source_party, package, recipient_organization, field_scope, request.POST.get("reason", ""),
                request.user,
            )
            messages.success(request, "Divulgación autorizada.")
        except (governance_services.AuthorizationDenied, Party.DoesNotExist) as exc:
            messages.error(request, str(exc))
    return redirect("procurement:package-detail", pk=pk)


@login_required
def disclosure_grant_revoke(request, pk):
    grant = get_object_or_404(
        DisclosureGrant, pk=pk, package_id__in=governance_services.authorized_package_ids(request.user)
    )
    if not governance_services.has_capability(request.user, "AUTHORIZE_DISCLOSURE", package=grant.package):
        governance_services.log_denied_attempt(
            request.user, "REVOKE_DISCLOSURE", package=grant.package, resource=grant,
            required_capability="AUTHORIZE_DISCLOSURE",
        )
        raise Http404
    if request.method == "POST":
        try:
            governance_services.revoke_disclosure_grant(grant, request.user)
            messages.success(request, "Divulgación revocada.")
        except governance_services.AuthorizationDenied as exc:
            messages.error(request, str(exc))
    return redirect("procurement:package-detail", pk=grant.package_id)


@login_required
def evidence_bundle_create(request, pk):
    package = _get_package_or_404(request, pk)
    if request.method == "POST":
        try:
            audit_services.create_evidence_bundle(
                package, request.POST.get("bundle_type", EvidenceBundle.BundleType.PRODUCTION_VERIFICATION), request.user,
                minimum_count=int(request.POST.get("minimum_count", 1) or 1),
            )
        except audit_services.EvidenceBundleError:
            raise Http404
        messages.success(request, "Paquete de evidencia creado.")
        return redirect("procurement:package-detail", pk=package.pk)
    return redirect("procurement:package-detail", pk=pk)


def _bundle_target(bundle):
    try:
        return bundle.content_type.get_object_for_this_type(pk=bundle.object_id)
    except Exception:
        return None


def _bundle_redirect(bundle):
    target = _bundle_target(bundle)
    if isinstance(target, ProcurementPackage):
        return redirect("procurement:package-detail", pk=target.pk)
    return redirect("/")


@login_required
def evidence_item_add(request, pk):
    package_content_type = ContentType.objects.get_for_model(ProcurementPackage)
    bundle = get_object_or_404(
        EvidenceBundle, pk=pk, content_type=package_content_type,
        object_id__in=governance_services.authorized_package_ids(request.user),
    )
    target = _bundle_target(bundle)
    try:
        audit_services.authorize_evidence_action(bundle, request.user, "CREATE_EVIDENCE")
    except audit_services.EvidenceBundleError:
        raise Http404
    # A package-scoped bundle's evidence is uploaded under the package's
    # own hosting organization, not necessarily the requester's home
    # organization — e.g. Edison's own login is under DT Beach, but he
    # acts on behalf of the China Trading Co package here.
    upload_organization = getattr(target, "organization", None) or request.user.profile.organization
    if request.method == "POST":
        form = DocumentUploadForm(request.POST, request.FILES, organization=upload_organization)
        if form.is_valid():
            from apps.core.storage import document_storage
            from apps.documents.models import Document, DocumentVersion

            uploaded_file = form.cleaned_data["file"]
            stored = document_storage.save(uploaded_file, uploaded_file.name)
            document = Document.objects.create(
                organization=upload_organization, document_type=form.cleaned_data["document_type"],
                title=form.cleaned_data["title"], created_by=request.user,
            )
            DocumentVersion.objects.create(
                document=document, version_number=1, stored_name=stored["stored_name"],
                original_filename=stored["original_filename"], sha256=stored["sha256"], size_bytes=stored["size_bytes"],
                mime_type=getattr(uploaded_file, "content_type", "") or "", uploaded_by=request.user, created_by=request.user,
            )
            audit_services.add_evidence_item(
                bundle, request.user, document=document, evidence_type=request.POST.get("evidence_type", ""),
            )
            messages.success(request, "Evidencia agregada.")
        else:
            messages.error(request, "Revise el archivo de evidencia.")
    return _bundle_redirect(bundle)


@login_required
def evidence_item_verify(request, pk):
    from apps.audit.models import EvidenceItem

    package_content_type = ContentType.objects.get_for_model(ProcurementPackage)
    item = get_object_or_404(
        EvidenceItem, pk=pk, bundle__content_type=package_content_type,
        bundle__object_id__in=governance_services.authorized_package_ids(request.user),
    )
    if request.method == "POST":
        package_pk = request.POST.get("package")
        try:
            audit_services.verify_evidence_item(
                item, request.user, verification_basis=request.POST.get("verification_basis", ""),
                expected_package_id=package_pk,
            )
            messages.success(request, "Evidencia verificada.")
        except audit_services.EvidenceBundleError as exc:
            messages.error(request, str(exc))
    return redirect(request.POST.get("next") or request.META.get("HTTP_REFERER", "/"))


@login_required
def verification_assertion_create(request, pk):
    package = _get_package_or_404(request, pk)
    if request.method == "POST":
        from apps.audit.models import EvidenceBundle

        bundle_pk = request.POST.get("source_evidence_bundle")
        bundle = get_object_or_404(
            EvidenceBundle,
            pk=bundle_pk,
            content_type=ContentType.objects.get_for_model(ProcurementPackage),
            object_id=package.pk,
        ) if bundle_pk else None
        source_party_pk = request.POST.get("source_party")
        source_party = Party.objects.filter(pk=source_party_pk).first() if source_party_pk else None
        try:
            services.create_verification_assertion(
                package, request.POST.get("assertion_code", ""), request.POST.get("client_visible_wording", ""),
                request.user, source_evidence_bundle=bundle, source_party=source_party,
            )
            messages.success(request, "Aserción de verificación creada.")
        except services.PackageError as exc:
            messages.error(request, str(exc))
    return redirect("procurement:package-detail", pk=pk)
