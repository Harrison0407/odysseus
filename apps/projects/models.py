from django.db import models

from apps.core.models import BaseModel

# ---------------------------------------------------------------------------
# Physical property master (buildings/floors/units) — one-shot release,
# "physical building/floor/apartment hierarchy."
#
# Hierarchy: Project -> BuildingFamily (building family/type, e.g. "ARENA
# T1", "SOLE PH") -> Building (one physical building) -> Floor -> Unit
# (apartment/room). BuildingFamily sits ABOVE the pre-existing Building
# model rather than replacing it — Building already represented exactly
# "one physical building" (e.g. "Arena #11"), so no parallel hierarchy was
# introduced; family is simply a new, optional grouping layer.
# ---------------------------------------------------------------------------


class Project(BaseModel):
    """E.g. Palmera, Arena, Sole-26. Note from source-package-analysis.md:
    real supplier PIs in the live fixture disagree with local POs about
    whether "Sole-26" is its own project or a building inside "Arena" —
    the model allows a Building to declare its own project independent of
    any assumption baked into code; reconciliation is a data decision, not
    a schema one."""

    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE, related_name="projects")
    name = models.CharField(max_length=150)
    code = models.SlugField(max_length=50)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = [("organization", "code")]
        ordering = ["name"]

    def __str__(self):
        return self.name


class BuildingFamily(BaseModel):
    """A building family/type within a project — e.g. "ARENA T1" (a tower
    grouping 8 physical buildings), "SOLE PH" (the penthouse-inclusive
    variant), "PALMERA" (a single hotel-style building). `is_active`
    lets a family exist in the catalog without being offered anywhere
    yet (MARE A, ARENA T2, ARENA T3 per this release's explicit
    instruction) — new families are added by configuration/data, never
    by redesigning this model."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="building_families")
    display_name = models.CharField(max_length=100, help_text='Canonical display name, e.g. "ARENA T1", "SOLE PH".')
    code = models.SlugField(max_length=50, help_text='Used in the permanent unit code, e.g. "arena-t1".')
    source_label = models.CharField(
        max_length=100, blank=True,
        help_text="Verbatim label as it appears in the source document (provenance), e.g. \"SOLE A\", \"SOLE B\".",
    )
    is_active = models.BooleanField(default=True)
    uses_building_segment = models.BooleanField(
        default=True,
        help_text="Whether the permanent unit code includes a building segment. False for hotel-style "
        "buildings with already-unique sequential room numbers (e.g. PALMERA).",
    )
    notes = models.TextField(blank=True)

    class Meta:
        unique_together = [("project", "code")]
        ordering = ["display_name"]
        verbose_name_plural = "building families"

    def __str__(self):
        return self.display_name


class Building(BaseModel):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="buildings")
    family = models.ForeignKey(
        BuildingFamily, on_delete=models.SET_NULL, null=True, blank=True, related_name="buildings",
        help_text="Nullable for backward compatibility with pre-existing Building rows created before "
        "this grouping layer existed.",
    )
    building_number = models.CharField(
        max_length=20, blank=True, help_text='The physical building\'s own number on the site plan, e.g. "11", "12".',
    )
    name = models.CharField(max_length=150)
    code = models.SlugField(max_length=50)
    notes = models.TextField(blank=True)

    class Meta:
        unique_together = [("project", "code")]
        ordering = ["name"]

    def __str__(self):
        return f"{self.project.name} / {self.name}"


class Floor(BaseModel):
    building = models.ForeignKey(Building, on_delete=models.CASCADE, related_name="floors")
    name = models.CharField(max_length=100)
    level = models.IntegerField(null=True, blank=True, help_text="Numeric sort key; a combined floor label (e.g. \"4-5\") uses its first level here.")
    label = models.CharField(max_length=20, blank=True, help_text='Verbatim floor label when it is not a single integer, e.g. "4-5" for a combined penthouse floor.')

    class Meta:
        ordering = ["level", "name"]

    def __str__(self):
        return f"{self.building} / {self.name}"


class Unit(BaseModel):
    """Apartment / unit — the finest-grained destination used by kitchen
    and closet kits, and now the permanent physical-unit identifier used
    end-to-end from construction through occupancy/maintenance (this
    release's "physical property master")."""

    floor = models.ForeignKey(Floor, on_delete=models.CASCADE, related_name="units", null=True, blank=True)
    building = models.ForeignKey(Building, on_delete=models.CASCADE, related_name="units")
    name = models.CharField(max_length=100, help_text='E.g. "APT-A", "G", "H".')
    unit_type = models.CharField(max_length=100, blank=True)

    permanent_code = models.CharField(
        max_length=100, unique=True, null=True, blank=True,
        help_text="Stable identifier generated once at creation (apps.projects.services.build_permanent_code) "
        "and never regenerated automatically — it must survive every later reassignment.",
    )
    apartment_number = models.CharField(max_length=30, blank=True, help_text='Source "Apt. Number" value, e.g. "A1", "417".')
    apartment_letter = models.CharField(max_length=10, blank=True)
    internal_area_sqm = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    terrace_area_sqm = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    total_area_sqm = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    bedrooms_label = models.CharField(
        max_length=30, blank=True,
        help_text='Source "Bedrooms" value verbatim — kept as text since some rows say "Estudio"/"Studio", not a number.',
    )
    bathrooms = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    has_service_room = models.BooleanField(null=True, blank=True, help_text="Null when not stated by the source.")
    is_penthouse = models.BooleanField(default=False)
    source_row_reference = models.CharField(
        max_length=255, blank=True, help_text="Provenance: source document + row identifier this unit was imported from.",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.building} / {self.name}"


class Area(BaseModel):
    """Common area or non-apartment destination within a building."""

    building = models.ForeignKey(Building, on_delete=models.CASCADE, related_name="areas")
    name = models.CharField(max_length=150)

    def __str__(self):
        return f"{self.building} / {self.name}"


class ProjectMilestone(BaseModel):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="milestones")
    building = models.ForeignKey(Building, on_delete=models.SET_NULL, null=True, blank=True, related_name="milestones")
    name = models.CharField(max_length=200)
    target_date = models.DateField(null=True, blank=True)
    actual_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["target_date"]

    def __str__(self):
        return f"{self.project} — {self.name}"
