from django.db import models
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from common.models import BaseModel


class Lead(BaseModel):
    class Meta:
        verbose_name = _("Lead")
        verbose_name_plural = _("Leads")

    first_name = models.CharField(max_length=100, blank=True, default="")
    last_name = models.CharField(max_length=100, blank=True, default="")
    title = models.CharField(
        max_length=100, blank=True, default="",
        verbose_name=_("Title / Position"),
    )
    email = models.CharField(max_length=200, blank=True, default="")
    phone = models.CharField(max_length=100, blank=True, default="")
    city_name = models.CharField(max_length=50, blank=True, default="")
    company_name = models.CharField(max_length=200, blank=True, default="")
    website = models.URLField(max_length=200, blank=True, default="", unique=True)
    description = models.TextField(blank=True, default="")
    disqualified = models.BooleanField(default=False)
    company = models.ForeignKey(
        "Company", blank=True, null=True, on_delete=models.CASCADE,
    )

    def __str__(self):
        name = f"{self.first_name} {self.last_name}".strip()
        if self.disqualified:
            name = f"({_('Disqualified')}) {name}"
        if self.company_name:
            return f"{name}, {self.company_name}"
        return name or self.website

    @property
    def full_name(self):
        name = f"{self.first_name} {self.last_name}".strip()
        if self.disqualified:
            name = f"({_('Disqualified')}) {name}"
        return name

    def get_absolute_url(self):
        return reverse("admin:crm_lead_change", args=(self.id,))
