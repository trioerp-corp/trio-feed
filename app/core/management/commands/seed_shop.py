"""Set up a NEW, EMPTY database for a shop / supermarket client (the lite edition).

Usage: python manage.py seed_shop --name "سوبر ماركت النور" [--vat 14] [--no-vat] [--phone ...] [--address ...]
Creates: a compact chart of accounts (docs/shop/chart-of-accounts.csv), one store (cost center + warehouse), the cash
drawer and the bank, journals, retail item categories, the POS branch and its first cashier device, and the roles.
Everything stays editable afterwards from «الإعدادات».
"""
import csv
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import CompanySettings, Currency
from inventory.models import ItemCategory, Warehouse
from ledger.models import Account, Dimension, DimensionValue, Journal
from pos.models import Branch, Device
from treasury.models import CashBox

JOURNALS = [
    ("JV", "قيد يومية", "ق-"), ("RV", "سند قبض", "ق"), ("PV", "سند صرف", "ص"), ("TR", "تحويل داخلي", "ت"),
    ("ST", "حركات مخازن", "م-"), ("CJ", "قيود تسوية", "ك-"), ("OB", "أرصدة افتتاحية", "افت-"), ("SI", "فواتير مبيعات", "ف-"),
    ("PI", "فواتير مشتريات", "م ش-"),
]
CAFE_CATEGORIES = ["مشروبات ساخنة", "مشروبات باردة وعصائر", "مشروبات معلبة ومياه", "أكل وسناكس", "شيشة", "خامات المطبخ والبار", "أخرى"]
CATEGORIES = ["بقالة وتموين", "ألبان وأجبان", "مشروبات", "حلويات وسناكس", "خضار وفاكهة", "لحوم ودواجن ومجمدات",
              "منظفات", "عناية شخصية", "أدوات منزلية", "أخرى"]
ROLES = {
    "مدير المحل": "__all__",
    "أمين مخزن": ["add_stockmove", "view_stockmove", "add_stockmoveline", "view_item", "add_item", "change_item", "view_warehouse",
                  "view_partner", "add_partner"],
    "محاسب": ["view_journalentry", "add_journalentry", "add_journalline", "view_voucher", "add_voucher", "add_voucherline", "view_stockmove",
              "view_account", "view_partner", "add_partner", "change_partner", "view_item", "view_order"],
}


class Command(BaseCommand):
    help = "تجهيز قاعدة بيانات جديدة لمحل / سوبر ماركت"

    def add_arguments(self, p):
        p.add_argument("--name", default="")
        p.add_argument("--config", default="", help="ملف client.json به بيانات العميل (الاسم، المستخدمون، أجهزة الكاشير، المخازن، اللوجو)")
        p.add_argument("--name-en", default="")
        p.add_argument("--vat", default="14")
        p.add_argument("--no-vat", action="store_true", help="المحل غير مسجّل في القيمة المضافة")
        p.add_argument("--phone", default="")
        p.add_argument("--address", default="")
        p.add_argument("--allow-existing", action="store_true", help="للاختبارات بس")

    @transaction.atomic
    def handle(self, name, name_en, vat, no_vat, phone, address, allow_existing=False, config="", **o):
        cfg = {}
        if config:
            import json

            cfg = json.loads(Path(config).read_text(encoding="utf-8"))
            name, name_en = cfg.get("name", name), cfg.get("name_en", name_en)
            no_vat = no_vat or cfg.get("vat") is False
            phone, address = cfg.get("phone", phone), cfg.get("address", address)
        if not name:
            raise CommandError("اكتب اسم المحل (--name) أو ملف العميل (--config)")
        if Account.objects.filter(code="1103010127").exists() and not allow_existing:
            raise CommandError("قاعدة البيانات هذه بها بيانات شركة أخرى — نسخة المحلات تُجهَّز على قاعدة جديدة فارغة")
        s = CompanySettings.get()
        s.name, s.name_en, s.phone, s.address = name, name_en or name, phone, address
        s.pos_enabled, s.pos_prices_include_vat = True, True
        s.default_vat_rate = 0 if no_vat else vat
        s.require_2fa = False
        rep = cfg.get("reports") or {}
        s.report_whatsapp = rep.get("whatsapp", True)
        s.report_telegram = rep.get("telegram", False)
        s.report_email = rep.get("email", False)
        if cfg.get("logo"):
            s.logo_svg = (Path(config).parent / cfg["logo"]).read_text(encoding="utf-8")
        s.save()
        Currency.objects.update_or_create(code="EGP", defaults={"name": "جنيه مصري"})
        by = {}
        with open(Path(settings.BASE_DIR) / "docs" / "shop" / "chart-of-accounts.csv", encoding="utf-8-sig") as f:
            data = list(csv.DictReader(f))
        for r in sorted(data, key=lambda r: (int(r["level"]), r["code"])):
            acc, _ = Account.objects.update_or_create(code=r["code"], defaults=dict(
                name=r["name_ar"], parent=by.get(r["parent_code"]), level=int(r["level"]), is_postable=r["postable"] == "yes",
                account_type=r["account_type"], normal_balance=r["normal_balance"], report=r["report"],
                cash_flow_category=r["cash_flow_category"], currencies="EGP", is_partner_control=r["is_partner_control"] == "yes",
                dim1_policy="optional" if r["postable"] == "yes" and r["report"] == "profit_loss" else "none",
                dim2_policy="none", dim3_policy="none"))
            by[acc.code] = acc
        d1, _ = Dimension.objects.update_or_create(number=1, defaults={"name": "الفروع"})
        Dimension.objects.update_or_create(number=2, defaults={"name": "بعد إضافي"})
        Dimension.objects.update_or_create(number=3, defaults={"name": "بعد إضافي 2"})
        store, _ = DimensionValue.objects.update_or_create(dimension=d1, code="1", defaults=dict(
            name=name, level=1, is_postable=False, is_project=True, project_kind="permanent"))
        main, _ = DimensionValue.objects.update_or_create(dimension=d1, code="11", defaults=dict(
            name=f"{name} — الفرع الرئيسي", level=2, is_postable=True, parent=store))
        for code, jname, prefix in JOURNALS:
            Journal.objects.update_or_create(code=code, defaults=dict(name=jname, prefix=prefix, yearly_reset=True, start_after=0, padding=5))
        drawer, _ = CashBox.objects.update_or_create(account=by["1201030101"], defaults=dict(name="الخزينة (درج الكاشير)", kind="cash", currency="EGP", order=1))
        CashBox.objects.update_or_create(account=by["1201030103"], defaults=dict(name="إنستاباي", kind="wallet", currency="EGP", order=2))
        CashBox.objects.update_or_create(account=by["1201030104"], defaults=dict(name="فودافون كاش", kind="wallet", currency="EGP", order=3))
        CashBox.objects.update_or_create(account=by["1201010101"], defaults=dict(name="البنك", kind="bank", currency="EGP", order=4))
        whs = cfg.get("warehouses") or [{"code": "WH01", "name": "المحل", "role": "shop"}]
        wh = None
        for w in whs:
            obj, _ = Warehouse.objects.update_or_create(code=w["code"], defaults=dict(name=w["name"], project=store))
            if w.get("role", "shop") == "shop" and wh is None:
                wh = obj
        from pos.clinic.services import CLINIC_CATEGORIES

        cats = cfg.get("categories") or (CAFE_CATEGORIES if cfg.get("business") in ("cafe", "gaming")
                                         else CLINIC_CATEGORIES if cfg.get("business") == "clinic" else CATEGORIES)  # the activity's own
        if "أخرى" not in cats:
            cats = list(cats) + ["أخرى"]
        for c in cats:
            ItemCategory.objects.update_or_create(name=c, defaults=dict(stock_account=by["1202010101"], farm_account=by["3101010101"],
                                                                         admin_account=by["3101010101"], needs_equipment=False))
        # packing bags, trays … used in the shop: issuing them from the operating store is an expense, not cost of goods
        ItemCategory.objects.update_or_create(name="مستلزمات تشغيل (شنط وأطباق)" if cfg.get("business") in ("cafe", "gaming") else "مستلزمات تشغيل (أكياس وتغليف)", defaults=dict(
            stock_account=by["1202010101"], farm_account=by["3202010110"], admin_account=by["3202010110"], needs_equipment=False))
        from inventory.models import Item

        Item.objects.update_or_create(code="0", defaults=dict(name="صنف بدون كود", category=ItemCategory.objects.get(name="أخرى"), unit="قطعة",
                                                              sale_price=0, aliases="متنوع"))
        br, _ = Branch.objects.update_or_create(code="MAIN", defaults=dict(name="الفرع الرئيسي", warehouse=wh, cashbox=drawer, cost_center=main,
                                                                           receipt_footer="شكرًا لزيارتكم"))
        devs = []
        for c in cfg.get("cashiers") or [{"device": "01", "first_name": "كاشير 1"}]:
            d = Device.objects.filter(branch=br, code=c["device"]).first() or Device.objects.create(branch=br, code=c["device"], name=c["first_name"])
            devs.append(d)
        if cfg.get("business") == "gaming":
            devs.append(Device.objects.filter(branch=br, code="GM").first() or Device.objects.create(branch=br, code="GM", name="شاشة الصالة"))
        if cfg.get("business") == "clinic":
            from pos.clinic import services as CL

            devs.append(CL.device())
            CL.services_category()
            CL.share_account()
            CL.payable_account()
        dev = devs[0]
        allp = Permission.objects.filter(content_type__app_label__in=["ledger", "treasury", "inventory", "core", "pos", "clinic"]).exclude(
            content_type__model__in=["auditlog"], codename__startswith="delete_")
        for role, codes in ROLES.items():
            g, _ = Group.objects.get_or_create(name=role)
            g.permissions.set(allp if codes == "__all__" else Permission.objects.filter(codename__in=codes))
        Group.objects.get_or_create(name="كاشير")
        if cfg.get("business") == "clinic":  # reception books and takes the money; doctors write the visit
            for g in ("استقبال", "دكتور"):
                Group.objects.get_or_create(name=g)
        self.users(cfg)
        self.stdout.write(self.style.SUCCESS(f"تم تجهيز «{name}»"))
        for d in devs:
            self.stdout.write(f"  مفتاح جهاز «{d.name}»: {d.token}")

    def users(self, cfg):
        """Owner (full rights) and cashiers, all with the temporary password — each must pick his own at the first login."""
        from django.contrib.auth import get_user_model

        from core.models import UserFlag

        if not cfg.get("owner"):
            return
        U, tmp = get_user_model(), cfg.get("temp_password", "0000")
        o = cfg["owner"]
        owner = U.objects.filter(username=o["username"]).first() or U(username=o["username"])
        owner.first_name, owner.is_staff, owner.is_superuser = o.get("first_name", ""), False, False
        owner.set_password(tmp)
        owner.save()
        owner.groups.add(Group.objects.get(name="مدير المحل"))
        UserFlag.objects.update_or_create(user=owner, defaults={"must_change_password": True})
        d = cfg.get("developer")
        if d:
            dev = U.objects.filter(username=d["username"]).first() or U(username=d["username"])
            dev.first_name, dev.is_staff, dev.is_superuser = d.get("first_name", "مطور النظام"), True, True
            dev.set_password(d.get("password", tmp))
            dev.save()
            UserFlag.objects.update_or_create(user=dev, defaults={"must_change_password": True})
            self.stdout.write(f"  حساب مطور النظام: {dev.username} (يفتح الحسابات والإعدادات الكاملة)")
        for c in cfg.get("cashiers", []):
            u = U.objects.filter(username=c["username"]).first() or U(username=c["username"])
            u.first_name, u.is_staff = c.get("first_name", ""), False
            u.set_password(tmp)
            u.save()
            u.groups.add(Group.objects.get(name="كاشير"))
            UserFlag.objects.update_or_create(user=u, defaults={"must_change_password": True})
        self.stdout.write(f"  المستخدمين: {owner.username} + {len(cfg.get('cashiers', []))} كاشير — كلمة السر المؤقتة {tmp} (يجب تغييرها عند أول دخول)")
