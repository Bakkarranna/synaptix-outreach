from django.db import models
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from common.models import Base1
from crm.models.base_contact import BaseCounterparty


class Company(BaseCounterparty, Base1):
    class Meta:
        verbose_name = _("Company")
        verbose_name_plural = _("Companies")

    full_name = models.CharField(
        max_length=200,
        null=False,
        blank=False,
        verbose_name=_("Company name")
    )
    alternative_names = models.CharField(
        max_length=100,
        default='',
        blank=True,
        verbose_name=_("Alternative names"),
        help_text=_("Separate them with commas.")
    )
    website = models.CharField(
        max_length=200,
        blank=True,
        default='',
        verbose_name=_("Website")
    )
    active = models.BooleanField(
        default=True,
        verbose_name=_("Active"),
    )
    phone = models.CharField(
        max_length=100,
        blank=True,
        default='',
        verbose_name=_("Phone")
    )
    city_name = models.CharField(
        max_length=100,
        blank=True,
        default='',
        verbose_name=_("City name")
    )
    registration_number = models.CharField(
        max_length=30,
        default='',
        blank=True,
        verbose_name=_("Registration number"),
        help_text=_("Registration number of Company")
    )

    def get_absolute_url(self):
        return reverse('admin:crm_company_change', args=(self.id,))

    def __str__(self):
        return self.full_name
