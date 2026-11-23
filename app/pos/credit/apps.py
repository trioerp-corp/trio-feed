from django.apps import AppConfig


class CreditConfig(AppConfig):
    """Credit customers (statement, due dates, instalment plans, reminders) and delivery platforms (1.25).
    Inside «pos» so every updater carries it."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "pos.credit"
    label = "credit"
    verbose_name = "الآجل والأقساط ومنصات التوصيل"
