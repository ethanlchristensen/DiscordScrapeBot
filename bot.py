import asyncio
import logging
from datetime import datetime, timezone

import discord
from discord.ext import commands

from services import ConfigService
from services.database_service import DatabaseService
from services.consent_service import ConsentService
from services.message_service import MessageService
from services.backfill_service import BackfillService, DEFAULT_LOOKBACK_DATE
from commands.backfill_commands import register_backfill_commands
from commands.admin_commands import register_admin_commands
from commands.consent_commands import register_consent_commands

# Setup logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class DiscordScrapeBot(commands.Bot):
    """Discord bot for scraping and archiving messages"""

    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.messages = True
        intents.reactions = True
        intents.guilds = True
        intents.members = True

        super().__init__(command_prefix="!", intents=intents)

        # Load configuration
        self.config = ConfigService().load()

        # Initialize services
        self.db_service = DatabaseService(self.config.mongoUri)
        self.consent_service = ConsentService(self.db_service)
        self.message_service = MessageService(self.db_service, self.consent_service)
        self.backfill_service = BackfillService(self.message_service)

        # Link backfill service to consent service for retroactive collection
        self.consent_service.backfill_service = self.backfill_service

        # Track last boot time
        self.boot_time = None

    async def setup_hook(self):
        """Called when the bot is starting up"""
        # Setup database indexes
        await self.db_service.setup_indexes()

        # Register commands
        register_backfill_commands(self.tree, self.backfill_service)
        register_admin_commands(self.tree, self.consent_service, self.backfill_service)
        register_consent_commands(
            self.tree, self.consent_service, self.backfill_service
        )

        logger.info("Bot setup complete")

    async def on_ready(self):
        """Called when bot successfully connects to Discord"""
        logger.info(f"Bot logged in as {self.user.name} ({self.user.id})")
        logger.info(f"Connected to {len(self.guilds)} guilds")

        # Update current boot time
        self.boot_time = datetime.now(timezone.utc)

        # Process each guild
        for guild in self.guilds:
            guild_status = await self.db_service.get_guild_status(guild.id)

            if guild_status and guild_status.get("last_boot"):
                last_boot = guild_status["last_boot"]
                logger.info(f"Guild {guild.name}: Last boot at {last_boot}")

                # Catch up on missed messages for this guild
                await self.backfill_service.catch_up_guild_messages(
                    guild, after=last_boot
                )
            else:
                logger.info(
                    f"Guild {guild.name}: New guild detected - "
                    f"downloading messages from {DEFAULT_LOOKBACK_DATE}"
                )

                # New guild - download messages from default lookback date
                await self.backfill_service.catch_up_guild_messages(
                    guild, after=DEFAULT_LOOKBACK_DATE
                )

            # Update last boot time for this guild
            await self.db_service.update_guild_status(
                guild.id, guild.name, {"last_boot": self.boot_time}
            )

        logger.info(f"Boot time recorded for all guilds: {self.boot_time}")

    async def close(self):
        """Called when bot is shutting down"""
        shutdown_time = datetime.now(timezone.utc)

        # Update shutdown time for all guilds
        for guild in self.guilds:
            await self.db_service.update_guild_status(
                guild.id, guild.name, {"last_shutdown": shutdown_time}
            )

        logger.info(f"Shutdown time recorded for all guilds: {shutdown_time}")

        self.db_service.close()
        await super().close()

    # Event handlers for real-time message tracking

    async def on_message(self, message: discord.Message):
        """Event: New message received"""
        # Don't log messages from this bot
        if message.author == self.user:
            return

        # Log message (consent is checked inside log_message)
        await self.message_service.log_message(message)
        await self.process_commands(message)

    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        """Event: Message edited"""
        await self.message_service.handle_message_edit(before, after)

    async def on_message_delete(self, message: discord.Message):
        """Event: Message deleted (cached message)"""
        await self.message_service.mark_message_deleted(message.id)
        logger.info(f"Message {message.id} deleted from {message.channel.name}")

    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        """Event: Message deleted (uncached message)"""
        await self.message_service.mark_message_deleted(payload.message_id)

    async def on_bulk_message_delete(self, messages: list[discord.Message]):
        """Event: Multiple messages deleted at once"""
        message_ids = [msg.id for msg in messages]
        await self.message_service.mark_messages_bulk_deleted(message_ids)

    async def on_raw_bulk_message_delete(
        self, payload: discord.RawBulkMessageDeleteEvent
    ):
        """Event: Bulk delete (uncached messages)"""
        message_ids = list(payload.message_ids)
        await self.message_service.mark_messages_bulk_deleted(message_ids)

    async def on_reaction_add(self, reaction: discord.Reaction, user: discord.User):
        """Event: Reaction added to message"""
        await self.message_service.handle_reaction_change(
            reaction.message, "add", str(reaction.emoji), user.id, user.name
        )

    async def on_reaction_remove(self, reaction: discord.Reaction, user: discord.User):
        """Event: Reaction removed from message"""
        await self.message_service.handle_reaction_change(
            reaction.message, "remove", str(reaction.emoji), user.id, user.name
        )

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        """Event: Reaction added (uncached message)"""
        await self.message_service.handle_raw_reaction_change(
            payload.message_id, "add", str(payload.emoji), payload.user_id
        )

    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        """Event: Reaction removed (uncached message)"""
        await self.message_service.handle_raw_reaction_change(
            payload.message_id, "remove", str(payload.emoji), payload.user_id
        )


async def main():
    """Main entry point for the bot"""
    bot = DiscordScrapeBot()

    try:
        await bot.start(bot.config.discordToken)
    except KeyboardInterrupt:
        logger.info("Received shutdown signal")
    finally:
        await bot.close()


if __name__ == "__main__":
    asyncio.run(main())
