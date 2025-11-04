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
        """Check if user has given consent at or above the required level

        Default behavior: Auto-consent enabled (opt-out model)
        - Users have consent by default at FULL level (Level 3)
        - Only returns False if user has explicitly revoked consent
        """
        consent_record = await self.get_user_consent(guild_id, user_id)

        if not consent_record:
            # No record = auto-consent enabled at FULL level by default
            return ConsentLevel.FULL >= required_level

        # Check if consent is explicitly revoked
        if not consent_record.get("consent_active", True):
            return False

        # Check if consent level meets requirement
        # Default to FULL level if not specified
        consent_level = consent_record.get("consent_level", ConsentLevel.FULL)
        return consent_level >= required_level

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
        """Grant or update consent for a user (explicit opt-in for higher levels or backfill)

        Args:
            guild_id: Guild ID
            guild_name: Guild name
            user_id: User ID
            user_name: User name
            consent_level: Consent level to grant
            initials: User confirmation (username)
            backfill_historical: Whether to backfill historical messages
            joined_at: Guild creation date (used for backfill to capture all messages)

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
            consent_record["backfill_from_date"] = joined_at

        await self.db.upsert_user_consent(consent_record)
        logger.info(
            f"Consent granted for user {user_name} ({user_id}) in guild {guild_name} ({guild_id}) "
            f"at level {consent_level.name} (backfill: {backfill_historical})"
        )

        return consent_record

    async def revoke_consent(self, guild_id: int, user_id: int) -> bool:
        """Revoke consent for a user (explicit opt-out)

        Creates/updates a consent record with consent_active=False
        to explicitly opt-out from the auto-consent default
        """
        # Get or create consent record
        consent_record = await self.get_user_consent(guild_id, user_id)

        if not consent_record:
            # Create new record with revoked status
            consent_record = {
                "guild_id": guild_id,
                "user_id": user_id,
                "consent_level": ConsentLevel.NONE,
                "consent_active": False,
                "revoked_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }
            await self.db.upsert_user_consent(consent_record)
            result = True
        else:
            # Update existing record to revoked
            result = await self.db.revoke_user_consent(guild_id, user_id)

        if result:
            logger.info(
                f"Consent revoked (opt-out) for user {user_id} in guild {guild_id}"
            )

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

    async def get_effective_consent_level(
        self, guild_id: int, user_id: int
    ) -> ConsentLevel:
        """Get the effective consent level for a user

        Returns:
            - ConsentLevel.FULL (3) by default (auto-consent)
            - ConsentLevel.NONE (0) if explicitly revoked
            - User's specified level if explicitly set
        """
        consent_record = await self.get_user_consent(guild_id, user_id)

        if not consent_record:
            # No record = default auto-consent at FULL level
            return ConsentLevel.FULL

        if not consent_record.get("consent_active", True):
            # Explicitly revoked
            return ConsentLevel.NONE

        # Return specified level or default to FULL
        return ConsentLevel(consent_record.get("consent_level", ConsentLevel.FULL))

    def should_log_message_content(self, consent_level: int) -> bool:
        """Check if message content should be logged at this consent level"""
        return consent_level >= ConsentLevel.CONTENT

    def should_log_attachments(self, consent_level: int) -> bool:
        """Check if attachments should be logged at this consent level"""
        return consent_level >= ConsentLevel.FULL

    async def auto_grant_consent_for_guild_members(
        self, guild_id: int, guild_name: str, members: list
    ) -> dict:
        """Auto-grant full consent for all guild members who don't have a consent record

        This creates explicit consent records in the database for all users,
        making it easier to track and audit consent status.

        Args:
            guild_id: Guild ID
            guild_name: Guild name
            members: List of discord.Member objects

        Returns:
            Dictionary with stats about created/existing/skipped records
        """
        created = 0
        existing = 0
        skipped_bots = 0

        for member in members:
            # Skip bot accounts
            if member.bot:
                skipped_bots += 1
                continue

            # Check if user already has a consent record
            existing_consent = await self.get_user_consent(guild_id, member.id)

            if existing_consent:
                # User already has a consent record (explicit or revoked), don't modify
                existing += 1
                continue

            # Create auto-consent record at FULL level
            consent_record = {
                "guild_id": guild_id,
                "guild_name": guild_name,
                "user_id": member.id,
                "user_name": member.name,
                "consent_level": int(ConsentLevel.FULL),
                "consent_active": True,
                "initials": "AUTO",
                "consented_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
                "backfill_historical": False,
                "auto_granted": True,  # Flag to indicate this was auto-granted
            }

            await self.db.upsert_user_consent(consent_record)
            created += 1

        logger.info(
            f"Auto-consent for guild {guild_name} ({guild_id}): "
            f"Created: {created}, Existing: {existing}, Skipped bots: {skipped_bots}"
        )

        return {
            "created": created,
            "existing": existing,
            "skipped_bots": skipped_bots,
            "total_processed": len(members),
        }
