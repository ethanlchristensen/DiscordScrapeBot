import logging
from datetime import datetime, timezone
from enum import IntEnum
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .backfill_service import BackfillService

logger = logging.getLogger(__name__)


class ConsentLevel(IntEnum):
    """Consent levels for data collection"""

    NONE = 0  # No consent (no logging)
    METADATA_ONLY = 1  # Log when a message was sent by the user
    CONTENT = 2  # Log metadata + message content
    FULL = 3  # Log metadata + content + attachments


class ConsentService:
    """Service for managing user consent and privacy settings"""

    def __init__(
        self, db_service, backfill_service: Optional["BackfillService"] = None
    ):
        self.db = db_service
        self.backfill_service = backfill_service

    async def get_user_consent(self, guild_id: int, user_id: int) -> Optional[dict]:
        """Get consent record for a user in a specific guild"""
        return await self.db.get_user_consent(guild_id, user_id)

    async def has_consent(
        self,
        guild_id: int,
        user_id: int,
        required_level: ConsentLevel = ConsentLevel.METADATA_ONLY,
    ) -> bool:
        """Check if user has given consent at or above the required level"""
        consent_record = await self.get_user_consent(guild_id, user_id)

        if not consent_record:
            return False

        # Check if consent is active and meets the required level
        return (
            consent_record.get("consent_active", False)
            and consent_record.get("consent_level", 0) >= required_level
        )

    async def grant_consent(
        self,
        guild_id: int,
        guild_name: str,
        user_id: int,
        user_name: str,
        consent_level: ConsentLevel,
        initials: str,
        backfill_historical: bool = False,
        joined_at: Optional[datetime] = None,
    ) -> dict:
        """Grant or update consent for a user

        Args:
            guild_id: Guild ID
            guild_name: Guild name
            user_id: User ID
            user_name: User name
            consent_level: Consent level to grant
            initials: User confirmation (username)
            backfill_historical: Whether to backfill historical messages
            joined_at: User's join date in guild (used for backfill)

        Returns:
            The consent record
        """
        consent_record = {
            "guild_id": guild_id,
            "guild_name": guild_name,
            "user_id": user_id,
            "user_name": user_name,
            "consent_level": int(consent_level),
            "consent_active": True,
            "initials": initials,
            "consented_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
            "backfill_historical": backfill_historical,
        }

        if joined_at:
            consent_record["user_joined_at"] = joined_at

        await self.db.upsert_user_consent(consent_record)
        logger.info(
            f"Consent granted for user {user_name} ({user_id}) in guild {guild_name} ({guild_id}) "
            f"at level {consent_level.name} (backfill: {backfill_historical})"
        )

        return consent_record

    async def revoke_consent(self, guild_id: int, user_id: int) -> bool:
        """Revoke consent for a user"""
        result = await self.db.revoke_user_consent(guild_id, user_id)

        if result:
            logger.info(f"Consent revoked for user {user_id} in guild {guild_id}")

        return result

    async def delete_user_data(self, guild_id: int, user_id: int) -> dict:
        """Delete all data for a user in a specific guild"""
        # Get count before deletion
        message_count = await self.db.count_user_messages(guild_id, user_id)

        # Delete all messages
        deleted_count = await self.db.delete_user_messages(guild_id, user_id)

        # Delete attachments from GridFS
        await self.db.delete_user_attachments(guild_id, user_id)

        logger.info(
            f"Deleted {deleted_count} messages for user {user_id} in guild {guild_id}"
        )

        return {
            "messages_found": message_count,
            "messages_deleted": deleted_count,
        }

    def get_consent_level_description(self, level: ConsentLevel) -> str:
        """Get a human-readable description of a consent level"""
        descriptions = {
            ConsentLevel.NONE: "No data collection",
            ConsentLevel.METADATA_ONLY: "Log when messages are sent (timestamp, channel)",
            ConsentLevel.CONTENT: "Log message timestamps and content",
            ConsentLevel.FULL: "Log timestamps, content, and attachments",
        }
        return descriptions.get(level, "Unknown")

    def should_log_message_content(self, consent_level: int) -> bool:
        """Check if message content should be logged at this consent level"""
        return consent_level >= ConsentLevel.CONTENT

    def should_log_attachments(self, consent_level: int) -> bool:
        """Check if attachments should be logged at this consent level"""
        return consent_level >= ConsentLevel.FULL
