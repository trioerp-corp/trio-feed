from django.urls import path

from . import views

urlpatterns = [
    path("", views.accrual_list, name="accrual_list"),
    path("new/", views.accrual_new, name="accrual_new"),
    path("<int:pk>/", views.accrual_detail, name="accrual_detail"),
]
