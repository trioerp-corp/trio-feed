from django.urls import path

from . import views

urlpatterns = [
    path("api/cashier/", views.api_cashier, name="pos_api_cashier"),
    path("", views.app, name="pos_app"),
    path("sw.js", views.sw, name="pos_sw"),
    path("api/catalog/", views.api_catalog),
    path("api/sync/", views.api_sync),
    path("api/kitchen/", views.api_kitchen),
    path("api/delivery/", views.api_delivery),
    path("api/return/ok/", views.api_return_ok),
    path("api/tables/", views.api_tables),
    path("api/tables/claim/", views.api_tables, {"op": "claim"}),
    path("api/tables/<int:tid>/", views.api_tables),
    path("api/tables/<int:tid>/<str:op>/", views.api_tables),
    path("api/tab/<int:tab>/<str:op>/", views.api_tables),
]
