from django.contrib.auth import views as auth_views
from django.urls import path

from core.throttle import login_view

from . import views

urlpatterns = [
    path("login/", login_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("reports/", views.index, name="reports_index"),
    path("reports/trial-balance/", views.trial_balance, name="trial_balance"),
    path("reports/ledger/", views.ledger, name="ledger"),
    path("reports/income-statement/", views.income_statement, name="income_statement"),
    path("reports/balance-sheet/", views.balance_sheet, name="balance_sheet"),
    path("reports/cash-flow/", views.cash_flow, name="cash_flow"),
    path("treasury/voucher/<int:pk>/print/", views.voucher_print, name="voucher_print"),
]
