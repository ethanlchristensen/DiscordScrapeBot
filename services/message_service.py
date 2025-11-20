import logging
from datetime import datetime, timezone

import discord

from services.database_service import DatabaseService

logger = logging.getLogger(__name__)


class MessageService:
    """Service for handling message logging and attachment processing"""

    def __init__(self, db_service: DatabaseService, consent_service=None):
        self.db = db_service
        self.consent_service = consent_service

    def generate_message_payload(
        self, message: discord.Message, is_catchup: bool = False, consent_level: int = 3
    ) -> dict:
        """Generate complete message payload for database"""
        return {
            "message_id": message.id,
            "channel_id": message.channel.id,
            "channel_name": message.channel.name
            if hasattr(message.channel, "name")
            else None,
            "guild_id": message.guild.id if message.guild else None,
            "guild_name": message.guild.name if message.guild else None,
            "author_id": message.author.id,
            "author_name": message.author.name,
            "author_discriminator": message.author.discriminator,
            "author_bot": message.author.bot,
            "content": message.content
            if consent_level >= 2
            else "[REDACTED - No consent]",
            "consent_level": consent_level,
            "timestamp": message.created_at,
            "edited_timestamp": message.edited_at,
            "tts": message.tts,
            "mention_everyone": message.mention_everyone,
            "mentions": [
                {
                    "id": user.id,
                    "name": user.name,
                    "discriminator": user.discriminator,
                    "bot": user.bot,
                }
                for user in message.mentions
            ],
            "mention_roles": [role.id for role in message.role_mentions],
            "mention_channels": [channel.id for channel in message.channel_mentions]
            if hasattr(message, "channel_mentions")
            else [],
            "embeds": [embed.to_dict() for embed in message.embeds],
            "reactions": [
                {
                    "emoji": str(reaction.emoji),
                    "count": reaction.count,
                    "me": reaction.me,
                }
                for reaction in message.reactions
            ],
            "pinned": message.pinned,
            "type": str(message.type),
            "flags": message.flags.value,
            "reference": {
                "message_id": message.reference.message_id,
                "channel_id": message.reference.channel_id,
                "guild_id": message.reference.guild_id,
            }
            if message.reference
            else None,
            "deleted": False,
            "logged_at": datetime.now(timezone.utc),
            "is_catchup": is_catchup,
        }

    async def download_attachment(
        self, attachment: discord.Attachment, message_id: int
    ) -> dict:
        """Download and return attachment data"""
        try:
            # Download the file
            file_data = await attachment.read()
            file_size = len(file_data)

            attachment_info = {
                "id": attachment.id,
                "filename": attachment.filename,
                "size": attachment.size,
                "url": attachment.url,
                "content_type": attachment.content_type,
                "width": attachment.width,
                "height": attachment.height,
                "downloaded_at": datetime.now(timezone.utc),
            }

            # If file is large, store in GridFS
            if file_size > DatabaseService.MAX_ATTACHMENT_INLINE_SIZE:
                gridfs_id = await self.db.store_file_in_gridfs(
                    file_data,
                    attachment.filename,
                    message_id,
                    attachment.id,
                    attachment.content_type,
                )
                attachment_info["storage"] = "gridfs"
                attachment_info["gridfs_id"] = gridfs_id
                logger.info(
                    f"Stored large attachment {attachment.id} ({file_size / 1024 / 1024:.2f}MB) in GridFS"
                )
            else:
                # Store small files inline
                attachment_info["storage"] = "inline"
                attachment_info["data"] = file_data

            return attachment_info

        except Exception as e:
            logger.error(f"Failed to download attachment {attachment.id}: {e}")
            return {
                "id": attachment.id,
                "filename": attachment.filename,
                "size": attachment.size,
                "url": attachment.url,
                "content_type": attachment.content_type,
                "error": str(e),
                "downloaded_at": datetime.now(timezone.utc),
            }

    async def log_message(self, message: discord.Message, is_catchup: bool = False):
        """Log a message to the database with consent checking

        Consent model:
        - Bot messages: Always logged at FULL level
        - User messages: Logged based on their consent record (auto-granted on first message/bot startup)
        - Users can revoke consent via /revoke_consent
        """
        try:
            # Always log bot messages at full level
            if message.author.bot:
                consent_level = 3
            # Check user consent
            elif self.consent_service and message.guild:
                # Get effective consent level
                effective_level = (
                    await self.consent_service.get_effective_consent_level(
                        message.guild.id, message.author.id
                    )
                )

                if effective_level == 0:  # ConsentLevel.NONE - explicitly revoked
                    # User has opted out - don't log
                    return

                consent_level = int(effective_level)
            else:
                # No consent service or no guild - default to FULL level
                consent_level = 3

            payload = self.generate_message_payload(message, is_catchup, consent_level)

            # Download attachments if present and consent level allows
            if message.attachments and consent_level >= 3:
                attachments_data = []
                for attachment in message.attachments:
                    if attachment.content_type.startswith("image/"): # only download image attachments
                        attachment_data = await self.download_attachment(
                            attachment, message.id
                        )
                        attachments_data.append(attachment_data)
                payload["attachments"] = attachments_data
                logger.info(
                    f"Downloaded {len(attachments_data)} attachments for message {message.id}"
                )
            elif message.attachments and consent_level < 3:
                # Log metadata about attachments but don't download
                payload["attachments"] = [
                    {
                        "id": att.id,
                        "filename": att.filename,
                        "size": att.size,
                        "content_type": att.content_type,
                        "redacted": True,
                        "reason": "Insufficient consent level",
                    }
                    for att in message.attachments
                ]
            else:
                payload["attachments"] = []

            # Upsert to database
            await self.db.upsert_message(payload)

            if not is_catchup:
                logger.info(
                    f"Logged message {message.id} from {message.author.name} in {message.channel.name} "
                    f"(consent level: {consent_level})"
                )

        except Exception as e:
            logger.error(f"Error logging message {message.id}: {e}")
            raise

    async def handle_message_edit(
        self, before: discord.Message, after: discord.Message
    ):
        """Handle message edit event"""
        try:
            # Update the message with new content
            payload = self.generate_message_payload(after)
            payload["edit_history"] = {
                "old_content": before.content,
                "new_content": after.content,
                "edited_at": datetime.now(timezone.utc),
            }

            await self.db.update_message(
                after.id,
                {
                    "$set": payload,
                    "$push": {
                        "edits": {
                            "old_content": before.content,
                            "new_content": after.content,
                            "timestamp": datetime.now(timezone.utc),
                        }
                    },
                },
            )

            logger.info(f"Message {after.id} edited by {after.author.name}")

        except Exception as e:
            logger.error(f"Error logging message edit {after.id}: {e}")

    async def mark_message_deleted(self, message_id: int):
        """Mark a message as deleted"""
        try:
            result = await self.db.update_message(
                message_id,
                {
                    "$set": {
                        "deleted": True,
                        "deleted_at": datetime.now(timezone.utc),
                    }
                },
            )

            if result.modified_count > 0:
                logger.info(f"Message {message_id} marked as deleted")

        except Exception as e:
            logger.error(f"Error marking message {message_id} as deleted: {e}")

    async def mark_messages_bulk_deleted(self, message_ids: list[int]):
        """Mark multiple messages as deleted"""
        try:
            await self.db.update_many_messages(
                {"message_id": {"$in": message_ids}},
                {
                    "$set": {
                        "deleted": True,
                        "deleted_at": datetime.now(timezone.utc),
                        "bulk_delete": True,
                    }
                },
            )

            logger.info(f"Bulk delete: {len(message_ids)} messages marked as deleted")

        except Exception as e:
            logger.error(f"Error processing bulk message delete: {e}")

    async def handle_reaction_change(
        self,
        message: discord.Message,
        reaction_type: str,
        emoji: str,
        user_id: int,
        user_name: str,
    ):
        """Handle reaction add/remove event"""
        try:
            await self.db.update_message(
                message.id,
                {
                    "$push": {
                        "reaction_events": {
                            "type": reaction_type,
                            "emoji": emoji,
                            "user_id": user_id,
                            "user_name": user_name,
                            "timestamp": datetime.now(timezone.utc),
                        }
                    },
                    "$set": {
                        "reactions": [
                            {"emoji": str(r.emoji), "count": r.count, "me": r.me}
                            for r in message.reactions
                        ]
                    },
                },
            )

            logger.info(
                f"Reaction {emoji} {reaction_type} to message {message.id} by {user_name}"
            )

        except Exception as e:
            logger.error(f"Error logging reaction {reaction_type}: {e}")

    async def handle_raw_reaction_change(
        self, message_id: int, reaction_type: str, emoji: str, user_id: int
    ):
        """Handle raw reaction event (uncached message)"""
        try:
            await self.db.update_message(
                message_id,
                {
                    "$push": {
                        "reaction_events": {
                            "type": reaction_type,
                            "emoji": emoji,
                            "user_id": user_id,
                            "timestamp": datetime.now(timezone.utc),
                        }
                    }
                },
            )

        except Exception as e:
            logger.error(f"Error logging raw reaction {reaction_type}: {e}")
