from django.conf import settings
from django.db import models

from apps.core.models import BaseModel


class Tool(BaseModel):
    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE, related_name="tools")
    code = models.CharField(max_length=50)
    description = models.CharField(max_length=255)
    brand = models.CharField(max_length=100, blank=True)
    serial_number = models.CharField(max_length=100, blank=True)
    photo_document = models.ForeignKey(
        "documents.Document", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    condition = models.CharField(max_length=100, blank=True)
    current_location = models.ForeignKey(
        "inventory.WarehouseLocation", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        unique_together = [("organization", "code")]

    def __str__(self):
        return f"{self.code} — {self.description}"


class ToolAssignment(BaseModel):
    tool = models.ForeignKey(Tool, on_delete=models.CASCADE, related_name="assignments")
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    is_active = models.BooleanField(default=True)


class ToolCheckout(BaseModel):
    assignment = models.ForeignKey(ToolAssignment, on_delete=models.CASCADE, related_name="checkouts")
    checkout_date = models.DateField()
    expected_return_date = models.DateField(null=True, blank=True)

    def __str__(self):
        return f"Checkout {self.assignment.tool} -> {self.assignment.assigned_to}"


class ToolReturn(BaseModel):
    checkout = models.OneToOneField(ToolCheckout, on_delete=models.CASCADE, related_name="tool_return")
    actual_return_date = models.DateField(null=True, blank=True)
    damage_noted = models.BooleanField(default=False)
    damage_description = models.TextField(blank=True)


class ToolRepair(BaseModel):
    tool = models.ForeignKey(Tool, on_delete=models.CASCADE, related_name="repairs")
    description = models.TextField()
    repaired_at = models.DateField(null=True, blank=True)
    cost = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    resulted_in_write_off = models.BooleanField(default=False)
