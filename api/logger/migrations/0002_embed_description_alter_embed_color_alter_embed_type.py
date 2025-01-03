# Generated by Django 5.1.4 on 2024-12-31 07:00

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("logger", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="embed",
            name="description",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="embed",
            name="color",
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="embed",
            name="type",
            field=models.CharField(blank=True, max_length=50, null=True),
        ),
    ]
