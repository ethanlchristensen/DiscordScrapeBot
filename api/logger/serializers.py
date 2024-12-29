from .models import *
from rest_framework import serializers

class MessageEmbedSerializer(serializers.ModelSerializer):
    class Meta:
        model = MessageEmbed
        fields = ['title', 'description', 'url', 'color', 'timestamp']

class MessageAttachmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = MessageAttachment
        fields = ['file_url', 'filename', 'content_type', 'size']

class MessageEditSerializer(serializers.ModelSerializer):
    class Meta:
        model = MessageEdit
        fields = ['previous_content', 'edited_at']

class DiscordMessageSerializer(serializers.ModelSerializer):
    embeds = MessageEmbedSerializer(many=True, required=False)
    attachments = MessageAttachmentSerializer(many=True, required=False)
    edits = MessageEditSerializer(many=True, read_only=True)

    class Meta:
        model = DiscordMessage
        fields = ['message_id', 'channel_id', 'guild_id', 'author_id', 
                 'content', 'created_at', 'embeds', 'attachments', 'edits']

    def create(self, validated_data):
        embeds_data = validated_data.pop('embeds', [])
        attachments_data = validated_data.pop('attachments', [])
        
        message = DiscordMessage.objects.create(**validated_data)
        
        for embed_data in embeds_data:
            MessageEmbed.objects.create(message=message, **embed_data)
        
        for attachment_data in attachments_data:
            MessageAttachment.objects.create(message=message, **attachment_data)
        
        return message