from django.apps import AppConfig


class StaffConfig(AppConfig):
    """Employees, payroll, payroll tax and social insurance, tax return drafts (1.21). Inside «pos» so every updater carries it."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "pos.staff"
    label = "staff"
    verbose_name = "الموظفين والضرايب"
