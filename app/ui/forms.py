from decimal import Decimal

from django import forms
from django.forms import inlineformset_factory

from inventory.models import Item, StockMove, StockMoveLine
from ledger.models import Account, DimensionValue, JournalEntry, JournalLine, Partner
from treasury.models import CashBox, Voucher, VoucherLine

LOOKUP_MODELS = {
    "account": (Account, lambda o: f"{o.code} {o.name}"),
    "cc": (DimensionValue, lambda o: f"{o.code} {o.name}"),
    "equipment": (DimensionValue, lambda o: o.name),
    "partner": (Partner, lambda o: o.name),
    "item": (Item, lambda o: f"{o.name} ({o.code})"),
    "accrual": (None, lambda o: f"{o.number} — {o.description} — باقي {o.remaining:,.2f}"),
}


class LookupWidget(forms.Widget):
    template_name = "ui/widgets/lookup.html"

    def __init__(self, kind, placeholder="ابحث…", attrs=None):
        super().__init__(attrs)
        self.kind, self.placeholder = kind, placeholder

    def get_context(self, name, value, attrs):
        ctx = super().get_context(name, value, attrs)
        label = ""
        if value not in (None, ""):
            model, fmt = LOOKUP_MODELS[self.kind]
            if model is None:
                from accruals.models import Accrual as model
            obj = model.objects.filter(pk=value).first()
            label = fmt(obj) if obj else ""
        ctx["widget"].update(kind=self.kind, label=label, placeholder=self.placeholder)
        return ctx


def lookup_field(model_field_formfield, kind, placeholder="ابحث…"):
    model_field_formfield.widget = LookupWidget(kind, placeholder)
    return model_field_formfield


class DateInput(forms.DateInput):
    input_type = "date"

    def format_value(self, value):
        return value.isoformat() if hasattr(value, "isoformat") else value


class VoucherForm(forms.ModelForm):
    class Meta:
        model = Voucher
        fields = ["kind", "date", "cashbox", "to_cashbox", "amount", "rate", "partner", "payee", "description", "approved_by_text"]
        widgets = {"date": DateInput(), "kind": forms.HiddenInput()}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["cashbox"].queryset = CashBox.objects.filter(active=True)
        self.fields["to_cashbox"].queryset = CashBox.objects.filter(active=True)
        self.fields["to_cashbox"].required = False
        self.fields["amount"].required = False
        lookup_field(self.fields["partner"], "partner", "عميل / مورد / موظف (اختياري)")

    def clean(self):
        c = super().clean()
        box = c.get("cashbox")
        if box:
            self.instance.currency = box.currency
            if box.currency == "EGP":
                c["rate"] = Decimal("1")
        if c.get("kind") == "transfer" and not c.get("to_cashbox"):
            self.add_error("to_cashbox", "حدد الخزينة المحوَّل إليها")
        return c


class VoucherLineForm(forms.ModelForm):
    class Meta:
        model = VoucherLine
        fields = ["accrual", "account", "partner", "amount", "dim1", "dim2", "dim3", "description"]

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        lookup_field(self.fields["accrual"], "accrual", "اختر الالتزام (اختياري)")
        self.fields["accrual"].required = False
        self.fields["account"].required = False
        lookup_field(self.fields["account"], "account", "الحساب / التوجيه")
        lookup_field(self.fields["partner"], "partner", "الطرف")
        lookup_field(self.fields["dim1"], "cc", "القطاع / المركز")
        lookup_field(self.fields["dim2"], "equipment", "المعدة")
        self.fields["dim3"].queryset = DimensionValue.objects.filter(dimension__number=3, active=True)
        self.fields["amount"].widget.attrs.update({"data-amount": "1", "inputmode": "decimal"})


def _voucher_line_clean(self):
    c = forms.ModelForm.clean(self)
    acc = c.get("accrual")
    if acc:
        c["account"] = acc.liability_account
        self.instance.account = acc.liability_account
        c["partner"] = c.get("partner") or acc.partner
        c["dim1"] = c["dim2"] = None
        if not c.get("description"):
            c["description"] = f"سداد {acc.description}"[:300]
    elif not c.get("account") and c.get("amount"):
        self.add_error("account", "حدد الحساب")
    return c


VoucherLineForm.clean = _voucher_line_clean

VoucherLineFormSet = inlineformset_factory(Voucher, VoucherLine, form=VoucherLineForm, extra=1, can_delete=True)


class MoveForm(forms.ModelForm):
    class Meta:
        model = StockMove
        fields = ["kind", "date", "warehouse", "to_warehouse", "partner", "receiver", "description"]
        widgets = {"date": DateInput()}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["kind"].choices = [c for c in StockMove.KINDS if c[0] != "harvest"]
        lookup_field(self.fields["partner"], "partner", "المورد (للوارد)")


class MoveLineForm(forms.ModelForm):
    class Meta:
        model = StockMoveLine
        fields = ["item", "qty", "unit_cost", "dim1", "dim2", "dim3", "account"]

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        lookup_field(self.fields["item"], "item", "الصنف")
        lookup_field(self.fields["dim1"], "cc", "القطاع")
        lookup_field(self.fields["dim2"], "equipment", "المعدة")
        lookup_field(self.fields["account"], "account", "حساب (اختياري)")
        self.fields["dim3"].queryset = DimensionValue.objects.filter(dimension__number=3, active=True)


MoveLineFormSet = inlineformset_factory(StockMove, StockMoveLine, form=MoveLineForm, extra=2, can_delete=True)


class EntryForm(forms.ModelForm):
    class Meta:
        model = JournalEntry
        fields = ["journal", "date", "description"]
        widgets = {"date": DateInput()}


class EntryLineForm(forms.ModelForm):
    class Meta:
        model = JournalLine
        fields = ["account", "partner", "debit", "credit", "dim1", "dim2", "dim3", "description"]

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        lookup_field(self.fields["account"], "account", "الحساب")
        lookup_field(self.fields["partner"], "partner", "الطرف")
        lookup_field(self.fields["dim1"], "cc", "المركز")
        lookup_field(self.fields["dim2"], "equipment", "المعدة")
        self.fields["dim3"].queryset = DimensionValue.objects.filter(dimension__number=3, active=True)


EntryLineFormSet = inlineformset_factory(JournalEntry, JournalLine, form=EntryLineForm, extra=2, can_delete=True)
