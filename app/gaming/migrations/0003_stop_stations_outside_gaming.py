"""1.23: copies whose business was changed from «بلايستيشن» to another kept their devices on — stop them (history stays)."""
from django.db import migrations


def stop(apps, schema_editor):
    S = apps.get_model("core", "CompanySettings")
    s = S.objects.order_by("pk").first()
    if s and s.business != "gaming":
        apps.get_model("gaming", "Station").objects.filter(active=True).update(active=False)


class Migration(migrations.Migration):
    dependencies = [("gaming", "0002_msa_labels"), ("core", "0016_trial_extra")]
    operations = [migrations.RunPython(stop, migrations.RunPython.noop)]
