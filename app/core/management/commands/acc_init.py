"""Trio: mark a NEW, EMPTY database as «waiting for the developer setup» (the launcher runs it at the first start)."""
from django.core.management.base import BaseCommand

from core.models import CompanySettings
from ledger.models import Account


class Command(BaseCommand):
    help = "تجهيز نسخة Trio جديدة لواجهة تثبيت المطور"

    def handle(self, **o):
        s = CompanySettings.get()
        if Account.objects.exists():
            self.stdout.write("هذه النسخة مثبتة بالفعل — لا تغيير")
            return
        s.name, s.name_en, s.setup_done, s.require_2fa, s.pos_enabled = "Trio", "Trio", False, False, True
        s.save()
        self.stdout.write(self.style.SUCCESS("جاهز — افتح البرنامج وادخل واجهة المطور باسم ACC"))
