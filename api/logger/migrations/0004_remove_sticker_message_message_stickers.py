# Generated by Django 5.1.4 on 2024-12-30 06:20

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('logger', '0003_alter_embedfield_name'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='sticker',
            name='message',
        ),
        migrations.AddField(
            model_name='message',
            name='stickers',
            field=models.ManyToManyField(blank=True, related_name='messages', to='logger.sticker'),
        ),
    ]
