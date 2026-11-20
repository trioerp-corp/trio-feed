from django.urls import path

from . import views

urlpatterns = [
    path("", views.screen, name="play"),
    path("api/state/", views.state, name="play_state"),
    path("api/balance/", views.balance, name="play_balance"),
    path("api/<str:name>/", views.action, name="play_action"),
    path("receipt/<int:pk>/", views.receipt, name="play_receipt"),
    path("shift/<int:pk>/", views.shift_print, name="play_shift"),
    path("report/", views.report, name="play_report"),
    path("devices/", views.devices, name="play_devices"),
    path("bookings/", views.bookings, name="play_bookings"),
    path("customers/", views.customers, name="play_customers"),
    path("receipt/pkg/<int:pk>/", views.package_receipt, name="play_package_receipt"),
]
