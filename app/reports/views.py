import datetime as dt
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from inventory.models import Item
from ledger.models import ZERO, Account, DimensionValue, JournalEntry, Partner
from treasury import services as T
from treasury.models import CashBox, Voucher

from . import services as R


def _d(v, default):
    try:
        return dt.date.fromisoformat(v) if v else default
    except ValueError:
        return default


def _range(request):
    today = timezone.localdate()
    return _d(request.GET.get("from"), today.replace(month=1, day=1)), _d(request.GET.get("to"), today)


@login_required
def index(request):
    return render(request, "reports/index.html", {})


@login_required
def trial_balance(request):
    d1, d2 = _range(request)
    level = int(request.GET.get("level", 4))
    rows, totals = R.trial_balance(d1, d2, max_level=level)
    return render(request, "reports/trial_balance.html", dict(rows=rows, totals=totals, d1=d1, d2=d2, level=level, levels=range(1, 7)))


@login_required
def ledger(request):
    d1, d2 = _range(request)
    acc = Account.objects.filter(code=request.GET.get("account", "").split(" ")[0]).first()
    partner = Partner.objects.filter(pk=request.GET.get("partner") or 0).first()
    cc = DimensionValue.objects.filter(dimension__number=1, code=request.GET.get("cc", "").split(" ")[0]).first()
    ctx = dict(d1=d1, d2=d2, acc=acc, partner=partner, cc=cc)
    if partner and not acc:
        ctx["opening"], ctx["rows"], ctx["closing"] = R.partner_statement(partner, d1, d2)
    elif acc:
        ctx["opening"], ctx["rows"], ctx["closing"] = R.account_ledger(acc, d1, d2, partner=partner, dim1=cc)
    ctx["partners"] = Partner.objects.filter(active=True).order_by("name")[:3000]
    return render(request, "reports/ledger.html", ctx)


@login_required
def income_statement(request):
    d1, d2 = _range(request)
    return render(request, "reports/income_statement.html", dict(r=R.income_statement(d1, d2), d1=d1, d2=d2))


@login_required
def balance_sheet(request):
    _, d2 = _range(request)
    return render(request, "reports/balance_sheet.html", dict(r=R.balance_sheet(d2), d2=d2))


@login_required
def cash_flow(request):
    d1, d2 = _range(request)
    return render(request, "reports/cash_flow.html", dict(r=R.cash_flow(d1, d2), d1=d1, d2=d2))


@login_required
def voucher_print(request, pk):
    v = get_object_or_404(Voucher.objects.select_related("cashbox", "partner", "entry"), pk=pk)
    return render(request, "reports/voucher_print.html", dict(v=v, lines=v.lines.select_related("account", "dim1", "dim2")))
