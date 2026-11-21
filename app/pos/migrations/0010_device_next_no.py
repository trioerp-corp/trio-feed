from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pos", "0009_delivery"),
    ]

    operations = [
        migrations.AddField(
            model_name="device",
            name="next_no",
            field=models.PositiveIntegerField(default=0, verbose_name="آخر رقم فاتورة محجوز"),
        ),
    ]
