from django.db import models

from apps.core.models import BaseModel


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


class Building(BaseModel):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="buildings")
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
    level = models.IntegerField(null=True, blank=True)

    class Meta:
        ordering = ["level", "name"]

    def __str__(self):
        return f"{self.building} / {self.name}"


class Unit(BaseModel):
    """Apartment / unit — the finest-grained destination used by kitchen
    and closet kits."""

    floor = models.ForeignKey(Floor, on_delete=models.CASCADE, related_name="units", null=True, blank=True)
    building = models.ForeignKey(Building, on_delete=models.CASCADE, related_name="units")
    name = models.CharField(max_length=100, help_text='E.g. "APT-A", "G", "H".')
    unit_type = models.CharField(max_length=100, blank=True)

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
