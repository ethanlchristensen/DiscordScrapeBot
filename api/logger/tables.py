import pytz
import django_tables2 as tables
from django.utils.html import format_html
from .models import Message

class MessageTable(tables.Table):
    # Define custom columns
    attachments = tables.Column(empty_values=(), verbose_name='Attachments')
    embeds = tables.Column(empty_values=(), verbose_name='Embeds')
    content_history = tables.Column(empty_values=(), verbose_name='Content History')
    is_deleted = tables.Column(verbose_name='Deleted')

    def render_content(self, value, record):
        limit = 100
        if len(value) > limit:
            preview = value[:limit] + "..."
            return format_html(
                '<a href="javascript:void(0)" onclick="toggleContentDetails(\'{}\'); return false;">{}</a>',
                str(record.id),
                preview
            )
        return value

    def render_is_deleted(self, value):
        return "Yes" if value else "No"

    def render_created_at(self, value):
        if value:
            return value.astimezone(pytz.timezone('America/Chicago')).strftime('%Y-%m-%d %H:%M')
        return ""

    def render_edited_at(self, value):
        if value:
            return value.astimezone(pytz.timezone('America/Chicago')).strftime('%Y-%m-%d %H:%M')
        return ""
    
    def render_attachments(self, value, record):
        attachments = record.attachments.all()
        if not attachments.exists():
            return "No attachments"
        return format_html(
            '<a href="javascript:void(0)" onclick="toggleDetails(\'{}\'); return false;">{} attachments</a>',
            str(record.id),  # Convert ID to string
            attachments.count()
        )

    def render_embeds(self, value, record):
        embeds = record.embeds.all()
        if not embeds.exists():
            return "No embeds"
        
        embed_details = []
        for embed in embeds:
            details = [f"Title: {embed.title or 'No title'}"]
            
            if hasattr(embed, 'footer') and embed.footer is not None:
                details.append(f"Footer: {embed.footer.text}")
            
            if embed.description:
                details.append(f"Description: {embed.description}")

            # Add field details
            fields_details = []
            for field in embed.fields.all():
                fields_details.append(f"Field Name: {field.name}, Value: {field.value}")
            fields_summary = "<br/>".join(fields_details)
            
            if fields_details:
                details.append(f"Fields: {fields_summary}")

            embed_details.append(" | ".join(details))
        
        return format_html(
            '<a href="javascript:void(0)" onclick="toggleDetails(\'{}\'); return false;">{} embeds</a>',
            str(record.id),  # Convert ID to string
            embeds.count()
        )

    def render_content_history(self, value, record):
        history = record.content_history.all()
        if not history.exists():
            return "No edits"
        history_details = [
            f"{edit.edited_at.strftime('%Y-%m-%d %H:%M')}: {edit.content}"
            for edit in history
        ]
        return format_html(
            '<a href="javascript:void(0)" onclick="toggleDetails(\'{}\'); return false;">{} edits</a>',
            str(record.id),  # Convert ID to string
            history.count()
        )

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
            "attachments",
            "embeds", 
            "content_history",
            "is_deleted",
        )
        attrs = {
            'class': 'table table-striped table-bordered',
            'data-toggle': 'table'
        }
        row_attrs = {
            'data-record-id': lambda record: str(record.id),
            'data-attachments': lambda record: ", ".join(f"{att.filename} ({att.size} bytes)" for att in record.attachments.all()),
            'data-embeds': lambda record: "<br/><br/>".join(
                "<br/>".join([
                    f"<h5>Title:</h5>{embed.title or 'No title'}<br/>",
                    f"<h5>Description:</h5>{embed.description or 'No description'}<br/>",
                    f"<h5>Footer:</h5>{embed.footer.text}<br/>" if hasattr(embed, 'footer') and embed.footer else "",
                    f"<h5>Fields:</h5>{embed.fields.count()}<br/>" if embed.fields.exists() else ""
                ]).strip(" |") 
                for embed in record.embeds.all()
            ),
            'data-content-history': lambda record: " || ".join(
                f"{edit.edited_at.strftime('%Y-%m-%d %H:%M')}: {edit.content}" 
                for edit in record.content_history.all()
            ),
            'data-content-full': lambda record: record.content
        }