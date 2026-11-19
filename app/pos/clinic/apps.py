from django.apps import AppConfig


class ClinicConfig(AppConfig):
    """Lives inside «pos» so the updater of copies before 1.11 (which only copies known top folders) carries it."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "pos.clinic"
    label = "clinic"
    verbose_name = "العيادة"
