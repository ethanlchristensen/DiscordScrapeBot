from rest_framework import serializers
from .models import (
    Message,
    Attachment,
    Embed,
    EmbedFooter,
    EmbedThumbnail,
    EmbedField,
    Sticker,
)


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
        fields = ["type", "title", "color", "footer", "thumbnail", "fields"]

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


class MessageSerializer(serializers.ModelSerializer):
    attachments = AttachmentSerializer(many=True, required=False)
    embeds = EmbedSerializer(many=True, required=False)
    stickers = StickerSerializer(many=True, required=False)

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
            "attachments",
            "embeds",
            "stickers",
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
                    id=sticker_id,
                    defaults=sticker_data
                )
                message.stickers.add(sticker)
        return message