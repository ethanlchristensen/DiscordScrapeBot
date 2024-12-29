from django.db import models
from django.utils import timezone

class DiscordMessage(models.Model):
    message_id = models.CharField(max_length=100, unique=True)
    channel_id = models.CharField(max_length=100)
    guild_id = models.CharField(max_length=100)
    author_id = models.CharField(max_length=100)
    content = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)
    
    def __str__(self):
        return f"Message {self.message_id} by {self.author_id}"

class MessageEmbed(models.Model):
    message = models.ForeignKey(DiscordMessage, related_name='embeds', on_delete=models.CASCADE)
    title = models.CharField(max_length=256, null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    url = models.URLField(max_length=500, null=True, blank=True)
    color = models.IntegerField(null=True, blank=True)
    timestamp = models.DateTimeField(null=True, blank=True)

class MessageAttachment(models.Model):
    message = models.ForeignKey(DiscordMessage, related_name='attachments', on_delete=models.CASCADE)
    file_url = models.URLField(max_length=500)
    filename = models.CharField(max_length=256)
    content_type = models.CharField(max_length=100)
    size = models.IntegerField()

class MessageEdit(models.Model):
    message = models.ForeignKey(DiscordMessage, related_name='edits', on_delete=models.CASCADE)
    previous_content = models.TextField()
    edited_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-edited_at']