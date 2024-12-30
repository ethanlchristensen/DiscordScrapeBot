from django.contrib import admin

from .models import Message, Sticker, Embed, EmbedField, EmbedFooter, EmbedThumbnail, Attachment
# Register your models here.
admin.site.register(Message)
admin.site.register(Sticker)
admin.site.register(Embed)
admin.site.register(EmbedField)
admin.site.register(EmbedFooter)
admin.site.register(EmbedThumbnail)
admin.site.register(Attachment)