import logging
from datetime import datetime, timezone
from typing import Optional

import discord

from services.message_service import MessageService

logger = logging.getLogger(__name__)

DEFAULT_LOOKBACK_DATE = datetime(2022, 1, 1, tzinfo=timezone.utc)


class BackfillService:
    """Service for handling message backfilling operations"""

    def __init__(self, message_service: MessageService):
        self.message_service = message_service

    async def catch_up_guild_messages(
        self, guild: discord.Guild, after: Optional[datetime]
    ):
        """Download messages from a specific guild"""
        if after:
            if after == DEFAULT_LOOKBACK_DATE:
                logger.info(
                    f"Downloading messages in new guild {guild.name} from {after}"
                )
            else:
                logger.info(f"Catching up messages in guild {guild.name} from {after}")
        else:
            logger.info(f"Downloading all messages in guild: {guild.name}")

        success_messages = 0
        failed_messages = 0

        for channel in guild.text_channels:
            channel_success, channel_failed = await self.backfill_channel(
                channel, after
            )
            success_messages += channel_success
            failed_messages += channel_failed

        logger.info(
            f"Guild {guild.name} - "
            f"Successfully inserted: {success_messages}, Failed: {failed_messages}"
        )

        return success_messages, failed_messages

    async def backfill_channel(
        self,
        channel: discord.TextChannel,
        after: Optional[datetime] = None,
        before: Optional[datetime] = None,
        user_id: Optional[int] = None,
    ) -> tuple[int, int]:
        """Backfill messages for a single channel, optionally filtering by user"""
        channel_success = 0
        channel_failed = 0

        try:
            # Construct history query
            history_kwargs = {"limit": None}
            if after:
                history_kwargs["after"] = after
            if before:
                history_kwargs["before"] = before

            message_history = channel.history(**history_kwargs)

            async for message in message_history:
                # Skip if filtering by user and message is not from that user
                if user_id and message.author.id != user_id:
                    continue

                try:
                    await self.message_service.log_message(message, is_catchup=True)
                    channel_success += 1
                except Exception as e:
                    logger.error(f"Failed to log message {message.id}: {e}")
                    channel_failed += 1

            if channel_success or channel_failed:
                user_info = f" for user {user_id}" if user_id else ""
                logger.info(
                    f"Channel {channel.name}{user_info}: "
                    f"Success: {channel_success:>6d}, Failed: {channel_failed:>6d}"
                )

        except discord.errors.Forbidden:
            logger.warning(
                f"Cannot access messages in {channel.name} of {channel.guild.name}"
            )
        except Exception as e:
            logger.error(f"Error processing channel {channel.name}: {e}")

        return channel_success, channel_failed

    async def backfill_channels(
        self,
        channels: list[discord.TextChannel],
        after: Optional[datetime] = None,
        before: Optional[datetime] = None,
        user_id: Optional[int] = None,
    ) -> tuple[int, int]:
        """Backfill messages for multiple channels, optionally filtering by user"""
        success_messages = 0
        failed_messages = 0

        for channel in channels:
            channel_success, channel_failed = await self.backfill_channel(
                channel, after, before, user_id
            )
            success_messages += channel_success
            failed_messages += channel_failed

        return success_messages, failed_messages

    async def backfill_categories(
        self,
        categories: list[discord.CategoryChannel],
        after: Optional[datetime] = None,
        before: Optional[datetime] = None,
    ) -> tuple[int, int, list[discord.TextChannel]]:
        """Backfill messages for entire categories"""
        # Extract all text channels from the categories
        target_channels = []
        for cat in categories:
            text_channels_in_cat = [
                ch for ch in cat.channels if isinstance(ch, discord.TextChannel)
            ]
            target_channels.extend(text_channels_in_cat)

        success_messages, failed_messages = await self.backfill_channels(
            target_channels, after, before
        )

        return success_messages, failed_messages, target_channels

    async def backfill_all_guilds(
        self,
        guilds: list[discord.Guild],
        after: Optional[datetime] = None,
        before: Optional[datetime] = None,
    ) -> tuple[int, int]:
        """Backfill messages for all guilds"""
        total_success = 0
        total_failed = 0

        for guild in guilds:
            logger.info(f"Backfilling guild: {guild.name}")

            guild_success, guild_failed = await self.catch_up_guild_messages(
                guild, after
            )

            total_success += guild_success
            total_failed += guild_failed

            logger.info(
                f"Completed guild {guild.name} - "
                f"Success: {guild_success}, Failed: {guild_failed}"
            )

        return total_success, total_failed

    async def backfill_user_messages(
        self,
        guild: discord.Guild,
        user_id: int,
        after: Optional[datetime] = None,
        before: Optional[datetime] = None,
    ) -> tuple[int, int]:
        """Backfill all messages from a specific user in a guild

        Args:
            guild: The Discord guild to search
            user_id: The user ID to backfill messages for
            after: Only fetch messages after this datetime (e.g., user's join date)
            before: Only fetch messages before this datetime

        Returns:
            Tuple of (success_count, failed_count)
        """
        logger.info(
            f"Backfilling messages for user {user_id} in guild {guild.name} "
            f"(after: {after}, before: {before})"
        )

        success_messages = 0
        failed_messages = 0

        # Iterate through all text channels in the guild
        for channel in guild.text_channels:
            channel_success, channel_failed = await self.backfill_channel(
                channel, after, before, user_id
            )
            success_messages += channel_success
            failed_messages += channel_failed

        logger.info(
            f"Completed backfill for user {user_id} in guild {guild.name} - "
            f"Success: {success_messages}, Failed: {failed_messages}"
        )

        return success_messages, failed_messages
