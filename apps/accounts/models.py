from django.conf import settings
from django.db import models

from apps.core.models import BaseModel


class Organization(BaseModel):
    """Spec section 12 — Organization and access. DT Beach is seeded as the
    single pilot organization, but nothing is hard-coded to that name so the
    system remains usable if migrated into MarketMatch as a multi-tenant
    module later."""

    name = models.CharField(max_length=200, unique=True)
    legal_name = models.CharField(max_length=255, blank=True)
    tax_id = models.CharField("RNC / Tax ID", max_length=50, blank=True)
    address = models.TextField(blank=True)
    default_currency = models.CharField(max_length=3, default="USD")
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Department(BaseModel):
    """Configurable department (spec section 8): Compras, Almacén, Obra,
    Finanzas, Dirección, Aduanas/Logística, etc. Not hard-coded — an
    organization defines its own departments via seed data or admin."""

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="departments")
    name = models.CharField(max_length=150)
    code = models.SlugField(max_length=50)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = [("organization", "code")]
        ordering = ["name"]

    def __str__(self):
        return self.name


class Role(BaseModel):
    """Configurable role (spec section 7): Compras, Finanzas, Almacén,
    Obra, Dirección, Auditor Externo, Solo Lectura, etc. A user may hold
    several roles; roles are configuration, never hard-coded business
    logic keyed on a person's name."""

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="roles")
    name = models.CharField(max_length=150)
    code = models.SlugField(max_length=50)
    description = models.TextField(blank=True)
    is_management = models.BooleanField(
        default=False, help_text="Read-only executive oversight role (e.g. María Luisa)."
    )
    requires_django_admin = models.BooleanField(
        default=False,
        help_text="Should always be False for business roles — spec requires a normal business "
        "interface, never Django Admin, for day-to-day operational users.",
    )

    class Meta:
        unique_together = [("organization", "code")]
        ordering = ["name"]

    def __str__(self):
        return self.name


class UserProfile(BaseModel):
    """Extends the built-in Django User with organization membership and
    department default, without swapping AUTH_USER_MODEL."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="members")
    primary_department = models.ForeignKey(
        Department, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    preferred_language = models.CharField(max_length=5, default="es", choices=[("es", "Español"), ("en", "English")])
    phone = models.CharField(max_length=50, blank=True)
    is_absent = models.BooleanField(
        default=False, help_text="Marks the user as temporarily unavailable so delegations activate."
    )

    def __str__(self):
        return self.user.get_full_name() or self.user.username


class UserRole(BaseModel):
    """Assigns a configurable Role to a user, optionally scoped to one
    department. A user commonly holds several (spec section 7)."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="user_roles")
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="assignments")
    department = models.ForeignKey(
        Department, on_delete=models.SET_NULL, null=True, blank=True, related_name="user_roles"
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = [("user", "role", "department")]

    def __str__(self):
        return f"{self.user} — {self.role}"


class UserProjectAccess(BaseModel):
    """Project-scoped authorization (spec section 32 — object-level
    authorization). A user may see/act on a project only if granted access
    here, independent of their organization-wide role."""

    class AccessLevel(models.TextChoices):
        READ_ONLY = "read_only", "Solo lectura"
        OPERATE = "operate", "Operar"
        APPROVE = "approve", "Aprobar"
        ADMIN = "admin", "Administrar"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="project_access")
    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="user_access")
    access_level = models.CharField(max_length=20, choices=AccessLevel.choices, default=AccessLevel.OPERATE)

    class Meta:
        unique_together = [("user", "project")]
        verbose_name_plural = "User project access"

    def __str__(self):
        return f"{self.user} @ {self.project} ({self.access_level})"


class ResponsibilityAssignment(BaseModel):
    """Answers "who owns this record now / what department controls it"
    (spec section 8) for any record in the system via a generic pointer.
    Kept lightweight here; Handoff (apps.workflow) carries the actual
    submit/accept transition history."""

    content_type = models.ForeignKey("contenttypes.ContentType", on_delete=models.CASCADE)
    object_id = models.UUIDField()

    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="responsibilities")
    primary_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    backup_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    escalation_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    due_at = models.DateTimeField(null=True, blank=True)
    is_open = models.BooleanField(default=True)

    class Meta:
        indexes = [models.Index(fields=["content_type", "object_id"])]

    def __str__(self):
        return f"{self.department} responsible for {self.content_type} {self.object_id}"


class Delegation(BaseModel):
    """Temporary delegation / absence coverage (spec section 7, 8)."""

    delegator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="delegations_made")
    delegate = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="delegations_received")
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    reason = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.delegator} -> {self.delegate} ({self.starts_at:%Y-%m-%d}..{self.ends_at:%Y-%m-%d})"
