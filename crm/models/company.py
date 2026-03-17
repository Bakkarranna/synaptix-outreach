from django.db import models
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from common.models import BaseModel


class Company(BaseModel):
    class Meta:
        verbose_name = _("Company")
        verbose_name_plural = _("Companies")

    full_name = models.CharField(max_length=200, verbose_name=_("Company name"))

    def get_absolute_url(self):
        return reverse("admin:crm_company_change", args=(self.id,))

    def __str__(self):
        return self.full_name
