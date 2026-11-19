from django.urls import path

from . import views

urlpatterns = [
    path("staff/", views.staff_list, name="staff_list"),
    path("staff/<int:pk>/", views.staff_edit, name="staff_edit"),
    path("staff/payroll/", views.payroll, name="staff_payroll"),
    path("tax/", views.tax_home, name="tax_home"),
]
