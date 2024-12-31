from django.db import models


class Message(models.Model):
    id = models.BigIntegerField(primary_key=True)
    content = models.TextField(blank=True)
    channel_id = models.BigIntegerField()
    channel_name = models.CharField(max_length=100)
    author_id = models.BigIntegerField()
    author_name = models.CharField(max_length=100)
    author_discriminator = models.CharField(max_length=4)
    created_at = models.DateTimeField()
    edited_at = models.DateTimeField(null=True, blank=True)
    stickers = models.ManyToManyField("Sticker", related_name="messages", blank=True)

    def __str__(self):
        return f"Message {self.id} - {self.author_name}"


class Attachment(models.Model):
    message = models.ForeignKey(
        Message, related_name="attachments", on_delete=models.CASCADE
    )
    id = models.BigIntegerField(primary_key=True)
    url = models.TextField(blank=True)
    filename = models.TextField()
    size = models.IntegerField()

    def __str__(self):
        return f"Attachment {self.filename} for Message {self.message.id}"


class Embed(models.Model):
    message = models.ForeignKey(
        Message, related_name="embeds", on_delete=models.CASCADE
    )
    type = models.CharField(max_length=50, blank=True, null=True)
    title = models.TextField(blank=True, null=True)
    color = models.IntegerField(null=True, blank=True)
    description = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"Embed {self.id} - {self.title}"


class EmbedFooter(models.Model):
    embed = models.OneToOneField(Embed, related_name="footer", on_delete=models.CASCADE)
    text = models.TextField(blank=True)
    icon_url = models.TextField(blank=True)
    proxy_icon_url = models.TextField(blank=True)


class EmbedThumbnail(models.Model):
    embed = models.OneToOneField(
        Embed, related_name="thumbnail", on_delete=models.CASCADE
    )
    url = models.TextField(blank=True)
    proxy_url = models.TextField(blank=True)
    width = models.IntegerField()
    height = models.IntegerField()
    flags = models.IntegerField(default=0)


class EmbedField(models.Model):
    embed = models.ForeignKey(Embed, related_name="fields", on_delete=models.CASCADE)
    name = models.TextField(blank=True)
    value = models.TextField(blank=True)
    inline = models.BooleanField(default=False)


class Sticker(models.Model):
    id = models.BigIntegerField(primary_key=True)
    name = models.CharField(max_length=255)
    url = models.TextField()

    def __str__(self):
        return f"Sticker {self.name}"
    
class MessageContentHistory(models.Model):
    message = models.ForeignKey(Message, related_name="content_history", on_delete=models.CASCADE)
    content = models.TextField(blank=True)
    edited_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"History for Message {self.message.id} at {self.edited_at}"
    
class MessageEmbedHistory(models.Model):
    message = models.ForeignKey(Message, related_name="embed_history", on_delete=models.CASCADE)
    embed_data = models.JSONField()
    changed_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Embed History for Message {self.message.id} at {self.changed_at}"