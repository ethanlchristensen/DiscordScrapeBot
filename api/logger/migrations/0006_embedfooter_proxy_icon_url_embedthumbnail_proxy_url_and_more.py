# Generated by Django 5.1.4 on 2024-12-30 07:36

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('logger', '0005_remove_embedfooter_proxy_icon_url_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='embedfooter',
            name='proxy_icon_url',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='embedthumbnail',
            name='proxy_url',
            field=models.TextField(blank=True),
        ),
        migrations.AlterField(
            model_name='attachment',
            name='url',
            field=models.TextField(blank=True),
        ),
        migrations.AlterField(
            model_name='embedthumbnail',
            name='url',
            field=models.TextField(blank=True),
        ),
    ]
