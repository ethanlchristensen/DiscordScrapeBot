from rest_framework import serializers
from .models import (
    Message,
    MessageContentHistory,
    MessageEmbedHistory,
    Attachment,
    Embed,
    EmbedFooter,
    EmbedThumbnail,
    EmbedField,
    Sticker,
)
from django.db import transaction
from django.utils import timezone
from django.core.serializers.json import DjangoJSONEncoder
import json


class EmbedFooterSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmbedFooter
        fields = ["text", "icon_url", "proxy_icon_url"]


class EmbedThumbnailSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmbedThumbnail
        fields = ["url", "proxy_url", "width", "height", "flags"]


class EmbedFieldSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmbedField
        fields = ["name", "value", "inline"]


class EmbedSerializer(serializers.ModelSerializer):
    footer = EmbedFooterSerializer(required=False)
    thumbnail = EmbedThumbnailSerializer(required=False)
    fields = EmbedFieldSerializer(many=True, required=False)

    class Meta:
        model = Embed
        fields = ["type", "title", "color", "description", "footer", "thumbnail", "fields"]

    def create(self, validated_data):
        footer_data = validated_data.pop("footer", None)
        thumbnail_data = validated_data.pop("thumbnail", None)
        fields_data = validated_data.pop("fields", [])

        embed = Embed.objects.create(**validated_data)

        if footer_data:
            EmbedFooter.objects.create(embed=embed, **footer_data)
        if thumbnail_data:
            EmbedThumbnail.objects.create(embed=embed, **thumbnail_data)
        for field_data in fields_data:
            EmbedField.objects.create(embed=embed, **field_data)

        return embed


class AttachmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Attachment
        fields = ["id", "url", "filename", "size"]


class StickerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Sticker
        fields = ["id", "name", "url"]

    def to_internal_value(self, data):
        sticker_id = data.get("id")
        if sticker_id:
            try:
                sticker = Sticker.objects.get(id=sticker_id)
                return sticker
            except Sticker.DoesNotExist:
                pass
        return super().to_internal_value(data)


class MessageContentHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = MessageContentHistory
        fields = ["content", "edited_at"]

class MessageEmbedHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = MessageEmbedHistory
        fields = ["embed_data", "changed_at"]


class MessageSerializer(serializers.ModelSerializer):
    attachments = AttachmentSerializer(many=True, required=False)
    embeds = EmbedSerializer(many=True, required=False)
    stickers = StickerSerializer(many=True, required=False)
    content_history = MessageContentHistorySerializer(many=True, read_only=True)
    embed_history = MessageEmbedHistorySerializer(many=True, read_only=True)

    class Meta:
        model = Message
        fields = [
            "id",
            "content",
            "channel_id",
            "channel_name",
            "author_id",
            "author_name",
            "author_discriminator",
            "created_at",
            "edited_at",
            "is_deleted",
            "deleted_at",
            "attachments",
            "embeds",
            "stickers",
            "content_history",
            "embed_history"
        ]

    def create(self, validated_data):
        attachments_data = validated_data.pop("attachments", [])
        embeds_data = validated_data.pop("embeds", [])
        stickers_data = validated_data.pop("stickers", [])

        message = Message.objects.create(**validated_data)

        for attachment_data in attachments_data:
            Attachment.objects.create(message=message, **attachment_data)

        for embed_data in embeds_data:
            embed_serializer = EmbedSerializer(data=embed_data)
            if embed_serializer.is_valid():
                embed_serializer.save(message=message)

        for sticker_data in stickers_data:
            if isinstance(sticker_data, Sticker):
                message.stickers.add(sticker_data)
            elif isinstance(sticker_data, dict):
                sticker_id = sticker_data.get("id")
                sticker, created = Sticker.objects.update_or_create(
                    id=sticker_id, defaults=sticker_data
                )
                message.stickers.add(sticker)
        return message

    @transaction.atomic
    def update(self, instance, validated_data):
        # Track content changes...
        if (
            "content" in validated_data
            and instance.content != validated_data["content"]
        ):
            MessageContentHistory.objects.create(
                message=instance, content=instance.content, edited_at=timezone.now()
            )

        # Handle attachments...
        attachments_data = validated_data.pop("attachments", [])
        instance.attachments.all().delete()
        for attachment_data in attachments_data:
            Attachment.objects.create(message=instance, **attachment_data)

        # Handle stickers...
        stickers_data = validated_data.pop("stickers", [])
        instance.stickers.clear()
        for sticker_data in stickers_data:
            if isinstance(sticker_data, Sticker):
                instance.stickers.add(sticker_data)
            elif isinstance(sticker_data, dict):
                sticker_id = sticker_data.get("id")
                sticker, created = Sticker.objects.update_or_create(
                    id=sticker_id, defaults=sticker_data
                )
                instance.stickers.add(sticker)

        # Handle embeds
        new_embeds_data = validated_data.pop("embeds", [])
        
        # Extract current embeds' relevant fields
        current_embeds = list(
            instance.embeds.values('type', 'title', 'color', 'description')
        )

        new_embeds = [
            {key:embed[key] for key in ["type", "title", "color", 'description']}
            for embed in new_embeds_data
        ]

        # Check if relevant embed fields have changed
        if current_embeds != new_embeds and new_embeds:
            # Log history if they are different
            current_embeds_json = json.dumps(current_embeds, cls=DjangoJSONEncoder)
            MessageEmbedHistory.objects.create(
                message=instance,
                embed_data=current_embeds_json,
                changed_at=timezone.now()
            )

        # Clear and add new embeds
        instance.embeds.all().delete()
        for embed_data in new_embeds_data:
            embed_serializer = EmbedSerializer(data=embed_data)
            if embed_serializer.is_valid(raise_exception=True):
                embed_serializer.save(message=instance)

        is_deleted = validated_data.get("is_deleted", instance.is_deleted)
        if is_deleted and not instance.is_deleted:
            instance.deleted_at = timezone.now()
        elif not is_deleted:
            instance.deleted_at = None
        instance.is_deleted = is_deleted

        # Update other fields
        instance.content = validated_data.get("content", instance.content)
        instance.channel_id = validated_data.get("channel_id", instance.channel_id)
        instance.channel_name = validated_data.get("channel_name", instance.channel_name)
        instance.author_id = validated_data.get("author_id", instance.author_id)
        instance.author_name = validated_data.get("author_name", instance.author_name)
        instance.author_discriminator = validated_data.get(
            "author_discriminator", instance.author_discriminator
        )
        instance.created_at = validated_data.get("created_at", instance.created_at)
        instance.edited_at = validated_data.get("edited_at", instance.edited_at)

        instance.save()
        return instance
