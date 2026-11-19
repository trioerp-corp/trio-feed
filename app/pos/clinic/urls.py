from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="clinic_home"),
    path("appointments/", views.appointments, name="clinic_appointments"),
    path("patients/", views.patients, name="clinic_patients"),
    path("patients/find/", views.patient_find, name="clinic_patient_find"),
    path("patients/<int:pk>/", views.patient, name="clinic_patient"),
    path("visits/<int:pk>/", views.visit, name="clinic_visit"),
    path("visits/<int:pk>/print/", views.print_rx, name="clinic_print"),
    path("visits/<int:pk>/receipt/", views.receipt, name="clinic_receipt"),
    path("setup/", views.setup, name="clinic_setup"),
    path("doctors/", views.doctors, name="clinic_doctors"),
    path("files/<int:pk>/", views.patient_file, name="clinic_file"),
    path("insurance/", views.insurance, name="clinic_insurance"),
    path("insurance/<int:pk>/", views.insurance_detail, name="clinic_insurance_detail"),
    path("reception/", views.reception, name="clinic_reception"),
    path("doctor/", views.doctor_screen, name="clinic_doctor_screen"),
    path("owner/", views.owner_board, name="clinic_owner"),
]
