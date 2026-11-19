from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from core import audit
from core.models import TwoFactor


class Command(BaseCommand):
    help = "إلغاء رمز تأكيد الهاتف لمستخدم (إن فُقد الهاتف). سيُفعّله المستخدم من جديد عند أول دخول."

    def add_arguments(self, p):
        p.add_argument("username")

    def handle(self, username, **o):
        try:
            u = get_user_model().objects.get(username=username)
        except get_user_model().DoesNotExist:
            raise CommandError(f"لا يوجد مستخدم باسم {username}")
        n = TwoFactor.objects.filter(user=u).delete()[0]
        audit.log("2fa_reset", model="TwoFactor", object_id=str(u.pk), summary=f"إلغاء رمز التأكيد لـ {u} من سطر الأوامر")
        self.stdout.write("تم الإلغاء — سيُفعّله من جديد عند أول دخول" if n else "هذا المستخدم لم يكن قد فعّل الرمز")
