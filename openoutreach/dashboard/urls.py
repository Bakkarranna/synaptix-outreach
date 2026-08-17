from django.urls import path

from openoutreach.dashboard import views

urlpatterns = [
    path("", views.overview, name="dashboard-overview"),
    path("leads/", views.leads, name="dashboard-leads"),
    path("leads/export/", views.leads_export, name="dashboard-leads-export"),
    path("leads/import/", views.leads_import, name="dashboard-leads-import"),
    path("deals/", views.deals, name="dashboard-deals"),
    path("config/", views.config_view, name="dashboard-config"),
    path("team/", views.team, name="dashboard-team"),
    path("logs/", views.logs, name="dashboard-logs"),
    path("outbox/", views.outbox, name="dashboard-outbox"),
    path("reviews/", views.reviews, name="dashboard-reviews"),
    path("research/", views.research, name="dashboard-research"),
    path("lookups/", views.lookups, name="dashboard-lookups"),
    path("sends/", views.sends, name="dashboard-sends"),
    path("sends/export/", views.sends_csv, name="dashboard-sends-csv"),
    path("action/<str:name>/", views.action, name="dashboard-action"),
    path("save/", views.save_config, name="dashboard-save"),
    path("team/add/", views.team_add, name="dashboard-team-add"),
    path("team/<int:pk>/toggle-active/", views.team_toggle_active, name="dashboard-team-toggle"),
    path("team/<int:pk>/password/", views.team_password, name="dashboard-team-password"),
    path("team/<int:pk>/role/", views.team_role, name="dashboard-team-role"),
    path("api/status/", views.api_status, name="dashboard-api-status"),
    path("api/deal/<int:pk>/", views.api_deal, name="dashboard-api-deal"),
]
