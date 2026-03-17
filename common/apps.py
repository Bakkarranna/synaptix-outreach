from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class CommonConfig(AppConfig):
    name = 'common'
    verbose_name = _('Common')
    label = 'common'
    default_auto_field = 'django.db.models.AutoField'
