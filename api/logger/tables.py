import django_tables2 as tables
from .models import Message

class MessageTable(tables.Table):
    class Meta:
        model = Message
        template_name = "django_tables2/bootstrap.html"
        fields = (
            "id",
            "content",
            "channel_name",
            "author_name",
            "created_at",
            "edited_at",
        )