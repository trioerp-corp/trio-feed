"""Check Trio's remote control file (the launcher runs it every hour)."""
from django.core.management.base import BaseCommand

from core import control


class Command(BaseCommand):
    help = "مراجعة ملف التحكم عن بعد (إيقاف/تشغيل)"

    def handle(self, **o):
        from core import feed

        self.stdout.write(feed.check())  # Trio's command channel (from the licence) — no password needed
        self.stdout.write(control.check())
        from core import updates

        self.stdout.write(updates.check())
