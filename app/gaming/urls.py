from django.urls import path

from . import views

urlpatterns = [
    path("", views.screen, name="play"),
    path("api/state/", views.state, name="play_state"),
    path("api/<str:name>/", views.action, name="play_action"),
    path("receipt/<int:pk>/", views.receipt, name="play_receipt"),
    path("shift/<int:pk>/", views.shift_print, name="play_shift"),
    path("report/", views.report, name="play_report"),
    path("devices/", views.devices, name="play_devices"),
]
