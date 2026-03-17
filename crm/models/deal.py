from django.db import models
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from common.models import BaseModel
from linkedin.enums import ProfileState


class ClosingReason(models.TextChoices):
    COMPLETED = "Completed"
    FAILED = "Failed"
    DISQUALIFIED = "Disqualified"


class Deal(BaseModel):
    class Meta:
        verbose_name = _("Deal")
        verbose_name_plural = _("Deals")

    name = models.CharField(max_length=250)
    lead = models.ForeignKey(
        "Lead", blank=True, null=True, on_delete=models.CASCADE,
    )
    state = models.CharField(
        max_length=20,
        choices=ProfileState.choices,
        default=ProfileState.QUALIFIED,
    )
    closing_reason = models.CharField(
        max_length=20,
        choices=ClosingReason.choices,
        blank=True,
        default="",
    )
    reason = models.TextField(blank=True, default="")
    connect_attempts = models.IntegerField(default=0)
    backoff_hours = models.IntegerField(default=0)

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("admin:crm_deal_change", args=(self.id,))
