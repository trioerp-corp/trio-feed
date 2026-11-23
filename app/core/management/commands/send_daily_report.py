import datetime as dt

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import CompanySettings
from core.reports_send import send_daily


class Command(BaseCommand):
    help = "إرسال ملخص اليوم (تليجرام / البريد)"

    def add_arguments(self, p):
        p.add_argument("--auto", action="store_true", help="يُرسل مرة واحدة يوميًا بعد ساعة الإرسال")
        p.add_argument("--date", default="")

    def handle(self, auto=False, date="", **o):
        s = CompanySettings.get()
        from pos.branches import branches_on

        from core import alerts

        now = timezone.localtime()
        if auto and now.hour < s.report_hour:
            return
        day = dt.date.fromisoformat(date) if date else None
        try:  # 1.25: the WhatsApp digest to the owner (once a day), whatever the other channels are
            res = alerts.digest(day, force=not auto)
            if res:
                self.stdout.write(f"واتساب: {res}")
        except Exception as e:  # noqa: BLE001
            self.stdout.write(f"واتساب: {e}")
        if not (s.report_telegram or s.report_email or branches_on()):
            return
        for ch, ok, msg in send_daily(dt.date.fromisoformat(date) if date else None, force=not auto):
            self.stdout.write(f"{ch}: {msg}")
