from django.db import migrations, models


def fill(apps, schema_editor):
    """Orders from before 1.26: «مع الطيار» = out with the rider, collected = delivered, cancelled = void."""
    D = apps.get_model("pos", "DeliveryOrder")
    for d in D.objects.all().iterator():
        d.stage = {"out": "out", "paid": "done", "void": "void"}.get(d.state, "out")
        d.steps = [dict(s="new", at=d.created_at.isoformat(), by=""), *([dict(s=d.stage, at=(d.collected_at or d.created_at).isoformat(), by="")]
                                                                    if d.stage != "new" else [])]
        d.save(update_fields=["stage", "steps"])


class Migration(migrations.Migration):

    dependencies = [
        ("pos", "0011_order_paid_insta"),
    ]

    operations = [
        migrations.AddField(model_name="deliveryorder", name="stage", field=models.CharField(
            choices=[("new", "جديد"), ("prep", "قيد التحضير"), ("out", "خرج مع عامل التوصيل"), ("done", "تم التسليم"), ("void", "ملغي")],
            db_index=True, default="new", max_length=5, verbose_name="مرحلة الطلب")),
        migrations.AddField(model_name="deliveryorder", name="steps", field=models.JSONField(blank=True, default=list, verbose_name="المراحل")),
        migrations.AddField(model_name="deliveryorder", name="platform", field=models.CharField(blank=True, max_length=12, verbose_name="المنصة")),
        migrations.RunPython(fill, migrations.RunPython.noop),
    ]
