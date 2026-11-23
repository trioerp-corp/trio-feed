from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
    verbose_name = "الإعدادات العامة"

    def ready(self):
        from django.apps import apps
        from . import audit

        audited = [
            "core.CompanySettings", "core.ExchangeRate", "ledger.Account", "ledger.Dimension", "ledger.DimensionValue",
            "ledger.Partner", "ledger.Journal", "ledger.Period", "treasury.CashBox", "treasury.TreasuryMapping",
            "treasury.Voucher", "inventory.Warehouse", "inventory.ItemCategory", "inventory.Item", "inventory.StockMove",
            "auth.User", "auth.Group",
        ]
        audit.register(*[apps.get_model(m) for m in audited])
        import sys

        if any("waitress" in a for a in sys.argv[:2]):  # the shop's web server (not manage.py commands or tests)
            from . import mobile, owner_phone

            mobile.start()
            owner_phone.start()  # orders from the owner's phone
            from pos import qr_orders

            qr_orders.start()  # customers' orders from the QR on their table (1.23)
            from pos.clinic import online as clinic_online

            clinic_online.start()  # 1.25 the clinic's online booking requests
