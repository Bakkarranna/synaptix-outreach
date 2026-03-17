import os

from django.conf import settings
from django.contrib.auth.models import Group
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class BaseModel(models.Model):
    """Shared base for all project models."""

    class Meta:
        abstract = True

    creation_date = models.DateTimeField(
        default=timezone.now,
        verbose_name=_("Creation date"),
    )
    update_date = models.DateTimeField(
        auto_now=True,
        verbose_name=_("Update date"),
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.CASCADE,
        verbose_name=_("Owner"),
        related_name="%(app_label)s_%(class)s_owner_related",
    )
    department = models.ForeignKey(
        "auth.Group",
        blank=True,
        null=True,
        on_delete=models.CASCADE,
        verbose_name=_("Department"),
        related_name="%(app_label)s_%(class)s_department_related",
    )


class Department(Group):
    class Meta:
        verbose_name = _("Department")
        verbose_name_plural = _("Departments")


class TheFile(models.Model):
    class Meta:
        verbose_name = _("File")
        verbose_name_plural = _("Files")

    file = models.FileField(
        blank=True, null=True,
        verbose_name=_("Attached file"),
        upload_to="docs/%Y/%m/%d/%H%M%S/",
        max_length=250,
    )
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey("content_type", "object_id")

    def __str__(self):
        if self.file.name:
            return self.file.name.split(os.sep)[-1]
        return "File"

    def delete(self, *args, **kwargs):
        file_num = TheFile.objects.filter(file=self.file).count()
        if file_num == 1:
            self.file.delete(save=False)
        super().delete(*args, **kwargs)
