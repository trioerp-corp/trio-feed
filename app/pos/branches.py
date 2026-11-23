"""Branches and staff (the «شامل» plan, module «branches»).

One program runs at the main branch; every branch works on it over a secure link (no router ports): its cashier screens keep
selling when the internet drops and sync when it is back, its manager opens the same program from his browser. So there is one
set of books and nothing to merge: the owner sees every branch, a branch manager sees his own.

Staff: every user has a branch (empty = all branches), a job title written freely, and capabilities ticked one by one
(role templates fill them for you).
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ValidationError
from django.db import transaction

Z = Decimal(0)

CAPS = [
    ("sell", "البيع والكاشير والورديات"),
    ("items", "الأصناف والأسعار"),
    ("stock", "الجرد والتالف والمخزن"),
    ("purchases", "المشتريات والموردين"),
    ("partners", "العملاء الآجل"),
    ("money", "المصروفات والخزينة"),
    ("reports", "التقارير والأرباح"),
    ("close", "تقفيل اليوم"),
    ("approve", "اعتماد الإلغاء والتعديل"),
    ("settings", "الإعدادات والمستخدمين"),
]
CAP_KEYS = [k for k, _ in CAPS]
TEMPLATES = {
    "owner": ("صاحب النشاط / المدير العام", CAP_KEYS),
    "branch_manager": ("مدير فرع", [k for k in CAP_KEYS if k != "settings"]),
    "cashier": ("كاشير", ["sell"]),
    "storekeeper": ("أمين مخزن", ["items", "stock", "purchases"]),
    "accountant": ("محاسب", ["money", "reports", "partners", "purchases", "close"]),
    "reception": ("استقبال (العيادة)", ["sell"]),  # books, takes the money, attaches results — clinic group «استقبال»
    "doctor": ("دكتور (العيادة)", []),  # his own «شاشة الدكتور»: his queue, the visit, the patient's file — clinic group «دكتور»
    "waiter": ("ويتر / كابتن", []),  # tables only: opens tables, adds items, kitchen, pre-bill — never takes money (group «ويتر»)
    "custom": ("مسمى خاص (اختار الصلاحيات)", []),
}
CAP_PERMS = {
    "sell": ["pos.view_order", "pos.add_order", "pos.view_shift", "pos.add_shift", "pos.add_cashmove", "inventory.view_item"],
    "items": ["inventory.view_item", "inventory.add_item", "inventory.change_item", "inventory.view_itemcategory", "inventory.add_itemunit",
              "inventory.change_itemunit", "inventory.view_itemunit"],
    "stock": ["inventory.view_item", "inventory.view_stockmove", "inventory.add_stockmove", "inventory.change_stockmove", "inventory.add_stockmoveline",
              "inventory.view_warehouse"],
    "purchases": ["inventory.view_item", "inventory.view_stockmove", "inventory.add_stockmove", "inventory.add_stockmoveline", "ledger.view_partner",
                  "ledger.add_partner"],
    "partners": ["ledger.view_partner", "ledger.add_partner", "ledger.change_partner", "treasury.view_voucher", "treasury.add_voucher"],
    "money": ["treasury.view_voucher", "treasury.add_voucher", "treasury.change_voucher", "treasury.add_voucherline", "treasury.view_cashbox",
              "pos.add_cashmove", "pos.view_cashmove", "ledger.view_partner"],
    "reports": ["treasury.view_voucher", "pos.view_order", "inventory.view_item"],
    "close": ["treasury.add_dayclose", "treasury.view_dayclose", "treasury.view_voucher"],
    "approve": ["pos.change_changerequest", "pos.view_changerequest"],
    "settings": ["auth.add_user", "auth.change_user"],
}
OWNER_GROUP, CASHIER_GROUP = "مدير المحل", "كاشير"
CLINIC_ONLY = ("reception", "doctor")
TABLES_ONLY = ("waiter",)
WAITER_GROUP = "ويتر"


def templates_for(business):
    """The role templates offered for this business (the clinic's reception / doctor only in a clinic, the waiter only where
    there are tables)."""
    from .tables import TABLE_BIZ

    out = {k: v for k, v in TEMPLATES.items()
           if (business == "clinic" or k not in CLINIC_ONLY) and ((business in TABLE_BIZ and business != "gaming") or k not in TABLES_ONLY)}
    if business == "gaming":  # a hall has no waiters (no tables): its worker runs the devices, the buffet and the drawer
        out["cashier"] = ("عامل الصالة", TEMPLATES["cashier"][1])
    return out


def staff_of(user):
    try:
        return user.staff
    except Exception:  # noqa: BLE001 — no profile yet (old copies): behave as before
        return None


def caps_of(user):
    if not user or not user.is_authenticated:
        return set()
    if user.is_superuser or user.groups.filter(name=OWNER_GROUP).exists():
        return set(CAP_KEYS)
    st = staff_of(user)
    return set(st.caps) if st else set()


def can(user, cap):
    return cap in caps_of(user)


def home_branch(user):
    """The branch a user is tied to — None means every branch (owner, developer, or a single-branch shop)."""
    st = staff_of(user)
    return st.branch if st and st.branch_id else None


def is_owner_level(user):
    return bool(user and user.is_authenticated and (user.is_superuser or user.groups.filter(name=OWNER_GROUP).exists()) and not home_branch(user))


def view_branch(request):
    """Branch the screens show: a branch manager is locked to his; the owner picks one (or all) from the branch switcher."""
    from .models import Branch

    b = home_branch(request.user)
    if b:
        return b
    bid = request.session.get("branch_view")
    return Branch.objects.filter(pk=bid, active=True).first() if bid else None


def branches_on():
    from core.modules import enabled

    from .models import Branch

    return "branches" in enabled() and Branch.objects.filter(active=True).count() > 1


def apply_staff(user, role, caps=None, branch=None, title="", email="", phone=""):
    """Write the profile and turn the capabilities into Django permissions (+ the old groups the rest of the program checks)."""
    from .models import Staff

    role = role if role in TEMPLATES else "custom"
    caps = [c for c in (caps if caps is not None else TEMPLATES[role][1]) if c in CAP_KEYS]
    if role != "custom" and not caps:
        caps = TEMPLATES[role][1]
    st, _ = Staff.objects.get_or_create(user=user)
    st.role, st.caps, st.branch, st.title = role, caps, branch, (title or TEMPLATES[role][0])[:60]
    if email is not None:
        st.email = (email or "").strip()[:150]
    if phone is not None:
        st.phone = (phone or "").strip()[:40]
    st.save()
    perms = set()
    for c in caps:
        perms.update(CAP_PERMS[c])
    objs = []
    for p in perms:
        app, code = p.split(".")
        objs += list(Permission.objects.filter(content_type__app_label=app, codename=code))
    user.user_permissions.set(objs)
    owner_g, _ = Group.objects.get_or_create(name=OWNER_GROUP)
    cash_g, _ = Group.objects.get_or_create(name=CASHIER_GROUP)
    if role == "owner" and branch is None:
        user.groups.add(owner_g)
    else:
        user.groups.remove(owner_g)
    if "sell" in caps:
        user.groups.add(cash_g)
    else:
        user.groups.remove(cash_g)
    for r, gname in (("reception", "استقبال"), ("doctor", "دكتور"), ("waiter", WAITER_GROUP)):  # the clinic pages check these groups
        g, _ = Group.objects.get_or_create(name=gname)
        (user.groups.add if role == r else user.groups.remove)(g)
    return st


# ------------------------------------------------------------------ opening a branch
def _next(prefix_qs, field, start, width=2):
    used = set(prefix_qs.values_list(field, flat=True))
    n = start
    while f"{n:0{width}d}" in used or str(n) in used:
        n += 1
    return n


@transaction.atomic
def create_branch(name, address="", phone="", devices=1, code=""):
    """A branch = its own store (warehouse), its own cash drawer, a cost centre for its profit, and its cashier devices."""
    from inventory.models import Warehouse
    from ledger.models import Account, DimensionValue
    from treasury.models import CashBox

    from .models import Branch, Device

    name = (name or "").strip()
    if len(name) < 2:
        raise ValidationError("اكتب اسم الفرع")
    if Branch.objects.filter(name=name).exists():
        raise ValidationError(f"في فرع اسمه «{name}» بالفعل")
    main = Branch.objects.order_by("id").first()
    n = _next(Branch.objects.all(), "code", Branch.objects.count() + 1)
    code = (code or f"B{n:02d}")[:6]
    # cost centre under the business (dimension 1)
    root = main.cost_center.parent if main and main.cost_center.parent_id else DimensionValue.objects.filter(dimension__number=1, level=1).first()
    cc_code = str(_next(DimensionValue.objects.filter(dimension__number=1, parent=root), "code", int(f"{root.code}1") + 1, 1))
    cc = DimensionValue.objects.create(dimension=root.dimension, code=cc_code, name=f"{root.name} — {name}"[:150], level=root.level + 1, is_postable=True,
                                       parent=root)
    # warehouse
    wn = _next(Warehouse.objects.all(), "code", 1)
    wcode = f"WH{wn:02d}"
    while Warehouse.objects.filter(code=wcode).exists():
        wn += 1
        wcode = f"WH{wn:02d}"
    wh = Warehouse.objects.create(code=wcode, name=f"مخزن {name}"[:100], project=cc)
    # cash drawer account under the cash parent
    parent = Account.objects.get(code="12010301")
    an = 11
    while Account.objects.filter(code=f"12010301{an:02d}").exists():
        an += 1
    acc = Account.objects.create(code=f"12010301{an:02d}", name=f"خزينة {name}"[:150], parent=parent, level=parent.level + 1, is_postable=True,
                                 account_type="asset", normal_balance="debit", report="balance_sheet", cash_flow_category="cash", currencies="EGP")
    box = CashBox.objects.create(account=acc, name=f"خزينة {name}"[:100], kind="cash", currency="EGP", order=10 + an)
    br = Branch.objects.create(code=code, name=name[:100], warehouse=wh, cashbox=box, cost_center=cc, address=address[:200], phone=phone[:40],
                               receipt_footer=main.receipt_footer if main else "شكرًا لزيارتكم")
    for k in range(max(1, min(int(devices or 1), 20))):
        Device.objects.create(branch=br, code=f"{k + 1:02d}", name=f"كاشير {k + 1}")  # the branch shows next to it everywhere
    return br


def create_user(username, name, password, role, branch=None, title="", caps=None, email="", phone=""):
    U = get_user_model()
    if U.objects.filter(username=username).exists():
        raise ValidationError(f"اسم الدخول «{username}» مستخدم قبل كده")
    if not password or len(password) < 4:
        raise ValidationError(f"كلمة سر «{username}» لازم 4 حروف/أرقام على الأقل")
    u = U(username=username, first_name=(name or username)[:150])
    u.set_password(password)
    u.save()
    apply_staff(u, role, caps, branch, title, email, phone)
    return u


def update_branch(b, name, address="", phone="", footer=""):
    """1.25: the owner corrects a branch's details (its receipt shows its own address and phone)."""
    from .models import Branch

    name = (name or "").strip()
    if len(name) < 2:
        raise ValidationError("اكتب اسم الفرع")
    if Branch.objects.filter(name=name).exclude(pk=b.pk).exists():
        raise ValidationError(f"يوجد فرع اسمه «{name}» بالفعل")
    b.name, b.address, b.phone = name[:100], (address or "").strip()[:200], (phone or "").strip()[:40]
    b.receipt_footer = (footer or "").strip()[:200]
    b.save(update_fields=["name", "address", "phone", "receipt_footer"])
    return b


def close_branch(b):
    """1.25: stop a branch — only when nothing is left in it (no open shift, no goods, no cash, no goods on the road to it)."""
    from inventory.models import StockQuant, Transfer
    from treasury import services as T

    from .models import Branch, Shift

    if b.pk == Branch.objects.order_by("id").values_list("id", flat=True).first():
        raise ValidationError("الفرع الرئيسي لا يُوقف")
    if Shift.objects.filter(device__branch=b, closed_at__isnull=True).exists():
        raise ValidationError("في الفرع وردية مفتوحة — أغلقها أولًا")
    if StockQuant.objects.filter(warehouse=b.warehouse).exclude(qty=0).exists():
        raise ValidationError("في مخزن الفرع بضاعة — حوّلها إلى فرع آخر أولًا («التحويلات بين الفروع»)")
    if T.cashbox_balance(b.cashbox):
        raise ValidationError("في خزينة الفرع نقدية — حوّلها أو وَرِّدها أولًا")
    from django.db.models import Q

    if Transfer.objects.filter(Q(from_warehouse=b.warehouse) | Q(to_warehouse=b.warehouse), state__in=["requested", "sent", "partial"]).exists():
        raise ValidationError("للفرع تحويلات لم تكتمل — أنهِها أولًا")
    b.active = False
    b.save(update_fields=["active"])
    b.devices.update(active=False)
    for st in b.staff.select_related("user"):
        st.user.is_active = False
        st.user.save(update_fields=["is_active"])
    return b


def in_transit_value():
    from .transfers import in_transit_value as v

    return v()
