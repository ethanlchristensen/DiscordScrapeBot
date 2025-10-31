import os
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional
from concurrent.futures import ThreadPoolExecutor

import discord
from discord import app_commands
from discord.ext import commands
from pymongo import MongoClient
import gridfs

from services import Config, ConfigService
from utils import admin_only, bot_owner_only

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# MongoDB document size limit (16MB) - use a safety margin
MAX_DOCUMENT_SIZE = 15 * 1024 * 1024
MAX_ATTACHMENT_INLINE_SIZE = 5 * 1024 * 1024
DEFAULT_LOOKBACK_DATE = datetime(2022, 1, 1, tzinfo=timezone.utc)


class DiscordScrapeBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.messages = True
        intents.reactions = True
        intents.guilds = True
        intents.members = True
        
        super().__init__(command_prefix="!", intents=intents)
        
        self.config = ConfigService().load()

        # MongoDB setup with pymongo
        self.mongo_client = MongoClient(self.config.mongoUri)
        self.db = self.mongo_client["DiscordScrapeBot"]
        self.messages_collection = self.db["Messages"]
        self.guild_status_collection = self.db["GuildStatus"]
        
        # GridFS for large attachments
        self.fs = gridfs.GridFS(self.db)
        
        # Thread pool for running sync MongoDB operations
        self.executor = ThreadPoolExecutor(max_workers=5)
        
        # Track last boot time
        self.boot_time = None
        
    async def setup_hook(self):
        """Called when the bot is starting up"""
        # Create indexes for better query performance
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(self.executor, self._create_indexes)
        logger.info("Database indexes created/verified")
        
    def _create_indexes(self):
        """Helper to create indexes synchronously"""
        self.messages_collection.create_index("message_id", unique=True)
        self.messages_collection.create_index("channel_id")
        self.messages_collection.create_index("author_id")
        self.messages_collection.create_index("timestamp")
        self.messages_collection.create_index("deleted")
        self.guild_status_collection.create_index("guild_id", unique=True)
        
    async def on_ready(self):
        """Called when bot successfully connects to Discord"""
        logger.info(f"Bot logged in as {self.user.name} ({self.user.id})")
        logger.info(f"Connected to {len(self.guilds)} guilds")
        
        # Update current boot time
        self.boot_time = datetime.now(timezone.utc)
        
        loop = asyncio.get_event_loop()

        # Process each guild
        for guild in self.guilds:
            guild_status = await loop.run_in_executor(
                self.executor,
                lambda g=guild: self.guild_status_collection.find_one({"guild_id": g.id})
            )
            
            if guild_status and guild_status.get("last_shutdown"):
                last_shutdown = guild_status["last_shutdown"]
                logger.info(f"Guild {guild.name}: Last shutdown at {last_shutdown}")
                
                # Catch up on missed messages for this guild
                await self.catch_up_guild_messages(guild, after=last_shutdown)
            else:
                logger.info(
                    f"Guild {guild.name}: New guild detected - "
                    f"downloading messages from {DEFAULT_LOOKBACK_DATE}"
                )
                
                # New guild - download messages from default lookback date
                await self.catch_up_guild_messages(guild, after=DEFAULT_LOOKBACK_DATE)
            
            # Update last boot time for this guild
            await loop.run_in_executor(
                self.executor,
                lambda g=guild: self.guild_status_collection.update_one(
                    {"guild_id": g.id},
                    {
                        "$set": {
                            "guild_name": g.name,
                            "last_boot": self.boot_time
                        }
                    },
                    upsert=True
                )
            )
        
        logger.info(f"Boot time recorded for all guilds: {self.boot_time}")
        
    async def close(self):
        """Called when bot is shutting down"""
        shutdown_time = datetime.now(timezone.utc)
        loop = asyncio.get_event_loop()
        
        # Update shutdown time for all guilds
        for guild in self.guilds:
            await loop.run_in_executor(
                self.executor,
                lambda g=guild: self.guild_status_collection.update_one(
                    {"guild_id": g.id},
                    {
                        "$set": {
                            "guild_name": g.name,
                            "last_shutdown": shutdown_time
                        }
                    },
                    upsert=True
                )
            )
        
        logger.info(f"Shutdown time recorded for all guilds: {shutdown_time}")
        
        self.executor.shutdown(wait=True)
        self.mongo_client.close()
        await super().close()
        
    async def catch_up_guild_messages(self, guild: discord.Guild, after: Optional[datetime]):
        """Download messages from a specific guild"""
        if after:
            if after == DEFAULT_LOOKBACK_DATE:
                logger.info(f"Downloading messages in new guild {guild.name} from {after}")
            else:
                logger.info(f"Catching up messages in guild {guild.name} from {after}")
        else:
            logger.info(f"Downloading all messages in guild: {guild.name}")
        
        success_messages = 0
        failed_messages = 0
        
        for channel in guild.text_channels:
            channel_success = 0
            channel_failed = 0
            
            try:
                # If after is None, get all messages; otherwise get messages after the timestamp
                if after:
                    message_history = channel.history(limit=None, after=after)
                else:
                    message_history = channel.history(limit=None)
                
                async for message in message_history:
                    try:
                        await self.log_message(message, is_catchup=True)
                        channel_success += 1
                    except Exception as e:
                        logger.error(f"Failed to log message {message.id}: {e}")
                        channel_failed += 1
                
                if channel_success or channel_failed:
                    logger.info(
                        f"Channel {channel.name}: "
                        f"Success: {channel_success:>6d}, Failed: {channel_failed:>6d}"
                    )
                    
            except discord.errors.Forbidden:
                logger.warning(f"Cannot access messages in {channel.name} of {guild.name}")
            except Exception as e:
                logger.error(f"Error processing channel {channel.name}: {e}")
            
            success_messages += channel_success
            failed_messages += channel_failed
        
        logger.info(
            f"Guild {guild.name} - "
            f"Successfully inserted: {success_messages}, Failed: {failed_messages}"
        )
        
    async def download_attachment(self, attachment: discord.Attachment, message_id: int) -> dict:
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
                "downloaded_at": datetime.now(timezone.utc)
            }
            
            # If file is large, store in GridFS
            if file_size > MAX_ATTACHMENT_INLINE_SIZE:
                loop = asyncio.get_event_loop()
                gridfs_id = await loop.run_in_executor(
                    self.executor,
                    lambda: self.fs.put(
                        file_data,
                        filename=attachment.filename,
                        message_id=message_id,
                        attachment_id=attachment.id,
                        content_type=attachment.content_type,
                        uploaded_at=datetime.now(timezone.utc)
                    )
                )
                attachment_info["storage"] = "gridfs"
                attachment_info["gridfs_id"] = gridfs_id
                logger.info(f"Stored large attachment {attachment.id} ({file_size / 1024 / 1024:.2f}MB) in GridFS")
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
                "downloaded_at": datetime.now(timezone.utc)
            }
    
    def generate_message_payload(self, message: discord.Message, is_catchup: bool = False) -> dict:
        """Generate complete message payload for database"""
        return {
            "message_id": message.id,
            "channel_id": message.channel.id,
            "channel_name": message.channel.name if hasattr(message.channel, 'name') else None,
            "guild_id": message.guild.id if message.guild else None,
            "guild_name": message.guild.name if message.guild else None,
            "author_id": message.author.id,
            "author_name": message.author.name,
            "author_discriminator": message.author.discriminator,
            "author_bot": message.author.bot,
            "content": message.content,
            "timestamp": message.created_at,
            "edited_timestamp": message.edited_at,
            "tts": message.tts,
            "mention_everyone": message.mention_everyone,
            "mentions": [
                {
                    "id": user.id,
                    "name": user.name,
                    "discriminator": user.discriminator,
                    "bot": user.bot
                }
                for user in message.mentions
            ],
            "mention_roles": [role.id for role in message.role_mentions],
            "mention_channels": [channel.id for channel in message.channel_mentions] if hasattr(message, 'channel_mentions') else [],
            "embeds": [embed.to_dict() for embed in message.embeds],
            "reactions": [
                {
                    "emoji": str(reaction.emoji),
                    "count": reaction.count,
                    "me": reaction.me
                }
                for reaction in message.reactions
            ],
            "pinned": message.pinned,
            "type": str(message.type),
            "flags": message.flags.value,
            "reference": {
                "message_id": message.reference.message_id,
                "channel_id": message.reference.channel_id,
                "guild_id": message.reference.guild_id
            } if message.reference else None,
            "deleted": False,
            "logged_at": datetime.now(timezone.utc),
            "is_catchup": is_catchup
        }
    
    async def log_message(self, message: discord.Message, is_catchup: bool = False):
        """Log a message to the database"""
        try:
            payload = self.generate_message_payload(message, is_catchup)
            
            # Download attachments if present
            if message.attachments:
                attachments_data = []
                for attachment in message.attachments:
                    attachment_data = await self.download_attachment(attachment, message.id)
                    attachments_data.append(attachment_data)
                payload["attachments"] = attachments_data
                logger.info(f"Downloaded {len(attachments_data)} attachments for message {message.id}")
            else:
                payload["attachments"] = []
            
            # Upsert to database using executor
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                self.executor,
                lambda: self.messages_collection.update_one(
                    {"message_id": message.id},
                    {"$set": payload},
                    upsert=True
                )
            )
            
            if not is_catchup:
                logger.info(f"Logged message {message.id} from {message.author.name} in {message.channel.name}")
                
        except Exception as e:
            logger.error(f"Error logging message {message.id}: {e}")
            raise
    
    async def on_message(self, message: discord.Message):
        """Event: New message received"""
        # Don't log messages from this bot (optional - remove if you want to log bot messages)
        if message.author == self.user:
            return
        
        await self.log_message(message)
        await self.process_commands(message)
    
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        """Event: Message edited"""
        try:
            # Update the message with new content
            payload = self.generate_message_payload(after)
            payload["edit_history"] = {
                "old_content": before.content,
                "new_content": after.content,
                "edited_at": datetime.now(timezone.utc)
            }
            
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                self.executor,
                lambda: self.messages_collection.update_one(
                    {"message_id": after.id},
                    {
                        "$set": payload,
                        "$push": {
                            "edits": {
                                "old_content": before.content,
                                "new_content": after.content,
                                "timestamp": datetime.now(timezone.utc)
                            }
                        }
                    }
                )
            )
            
            logger.info(f"Message {after.id} edited by {after.author.name}")
            
        except Exception as e:
            logger.error(f"Error logging message edit {after.id}: {e}")
    
    async def on_message_delete(self, message: discord.Message):
        """Event: Message deleted (cached message)"""
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                self.executor,
                lambda: self.messages_collection.update_one(
                    {"message_id": message.id},
                    {
                        "$set": {
                            "deleted": True,
                            "deleted_at": datetime.now(timezone.utc)
                        }
                    }
                )
            )
            
            logger.info(f"Message {message.id} deleted from {message.channel.name}")
            
        except Exception as e:
            logger.error(f"Error marking message {message.id} as deleted: {e}")
    
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        """Event: Message deleted (uncached message)"""
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                self.executor,
                lambda: self.messages_collection.update_one(
                    {"message_id": payload.message_id},
                    {
                        "$set": {
                            "deleted": True,
                            "deleted_at": datetime.now(timezone.utc)
                        }
                    }
                )
            )
            
            if result.modified_count > 0:
                logger.info(f"Message {payload.message_id} marked as deleted (raw event)")
            
        except Exception as e:
            logger.error(f"Error processing raw message delete {payload.message_id}: {e}")
    
    async def on_bulk_message_delete(self, messages: list[discord.Message]):
        """Event: Multiple messages deleted at once"""
        try:
            message_ids = [msg.id for msg in messages]
            
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                self.executor,
                lambda: self.messages_collection.update_many(
                    {"message_id": {"$in": message_ids}},
                    {
                        "$set": {
                            "deleted": True,
                            "deleted_at": datetime.now(timezone.utc),
                            "bulk_delete": True
                        }
                    }
                )
            )
            
            logger.info(f"Bulk delete: {len(messages)} messages marked as deleted")
            
        except Exception as e:
            logger.error(f"Error processing bulk message delete: {e}")
    
    async def on_raw_bulk_message_delete(self, payload: discord.RawBulkMessageDeleteEvent):
        """Event: Bulk delete (uncached messages)"""
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                self.executor,
                lambda: self.messages_collection.update_many(
                    {"message_id": {"$in": list(payload.message_ids)}},
                    {
                        "$set": {
                            "deleted": True,
                            "deleted_at": datetime.now(timezone.utc),
                            "bulk_delete": True
                        }
                    }
                )
            )
            
            logger.info(f"Raw bulk delete: {len(payload.message_ids)} messages marked as deleted")
            
        except Exception as e:
            logger.error(f"Error processing raw bulk message delete: {e}")
    
    async def on_reaction_add(self, reaction: discord.Reaction, user: discord.User):
        """Event: Reaction added to message"""
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                self.executor,
                lambda: self.messages_collection.update_one(
                    {"message_id": reaction.message.id},
                    {
                        "$push": {
                            "reaction_events": {
                                "type": "add",
                                "emoji": str(reaction.emoji),
                                "user_id": user.id,
                                "user_name": user.name,
                                "timestamp": datetime.now(timezone.utc)
                            }
                        },
                        "$set": {
                            f"reactions": [
                                {
                                    "emoji": str(r.emoji),
                                    "count": r.count,
                                    "me": r.me
                                }
                                for r in reaction.message.reactions
                            ]
                        }
                    }
                )
            )
            
            logger.info(f"Reaction {reaction.emoji} added to message {reaction.message.id} by {user.name}")
            
        except Exception as e:
            logger.error(f"Error logging reaction add: {e}")
    
    async def on_reaction_remove(self, reaction: discord.Reaction, user: discord.User):
        """Event: Reaction removed from message"""
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                self.executor,
                lambda: self.messages_collection.update_one(
                    {"message_id": reaction.message.id},
                    {
                        "$push": {
                            "reaction_events": {
                                "type": "remove",
                                "emoji": str(reaction.emoji),
                                "user_id": user.id,
                                "user_name": user.name,
                                "timestamp": datetime.now(timezone.utc)
                            }
                        },
                        "$set": {
                            f"reactions": [
                                {
                                    "emoji": str(r.emoji),
                                    "count": r.count,
                                    "me": r.me
                                }
                                for r in reaction.message.reactions
                            ]
                        }
                    }
                )
            )
            
            logger.info(f"Reaction {reaction.emoji} removed from message {reaction.message.id} by {user.name}")
            
        except Exception as e:
            logger.error(f"Error logging reaction remove: {e}")
    
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        """Event: Reaction added (uncached message)"""
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                self.executor,
                lambda: self.messages_collection.update_one(
                    {"message_id": payload.message_id},
                    {
                        "$push": {
                            "reaction_events": {
                                "type": "add",
                                "emoji": str(payload.emoji),
                                "user_id": payload.user_id,
                                "timestamp": datetime.now(timezone.utc)
                            }
                        }
                    }
                )
            )
            
        except Exception as e:
            logger.error(f"Error logging raw reaction add: {e}")
    
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        """Event: Reaction removed (uncached message)"""
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                self.executor,
                lambda: self.messages_collection.update_one(
                    {"message_id": payload.message_id},
                    {
                        "$push": {
                            "reaction_events": {
                                "type": "remove",
                                "emoji": str(payload.emoji),
                                "user_id": payload.user_id,
                                "timestamp": datetime.now(timezone.utc)
                            }
                        }
                    }
                )
            )
            
        except Exception as e:
            logger.error(f"Error logging raw reaction remove: {e}")


@app_commands.command(name="backfill", description="Manually backfill messages for a date range")
@app_commands.describe(
    from_date="Start date in YYYY-MM-DD format (e.g., 2022-01-01)",
    to_date="End date in YYYY-MM-DD format (optional, defaults to now)",
    guild_id="Specific guild ID to backfill (optional, defaults to current server)",
    channel_ids="Comma-separated channel IDs to backfill (optional, defaults to all channels)"
)
@app_commands.default_permissions(administrator=True)
@admin_only
async def backfill_messages(
    interaction: discord.Interaction, 
    from_date: str, 
    to_date: str = None, 
    guild_id: str = None,
    channel_ids: str = None
):
    """Manually backfill messages for a date range"""
    # Send initial deferred response immediately to avoid token expiration
    await interaction.response.defer(ephemeral=True)
    
    try:
        # Parse dates - enforce YYYY-MM-DD format
        from_datetime = datetime.strptime(from_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        
        if to_date:
            to_datetime = datetime.strptime(to_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        else:
            to_datetime = datetime.now(timezone.utc)
        
        # Validate date range
        if from_datetime >= to_datetime:
            await interaction.followup.send("❌ From date must be before to date!", ephemeral=True)
            return
        
        # Determine guild
        if guild_id:
            try:
                guild = interaction.client.get_guild(int(guild_id))
                if not guild:
                    await interaction.followup.send(f"❌ Guild with ID {guild_id} not found!", ephemeral=True)
                    return
            except ValueError:
                await interaction.followup.send(f"❌ Invalid guild ID format!", ephemeral=True)
                return
        else:
            guild = interaction.guild
            if not guild:
                await interaction.followup.send("❌ This command must be used in a server or provide a guild_id!", ephemeral=True)
                return
        
        # Parse channel IDs if provided
        target_channels = []
        if channel_ids:
            try:
                parsed_ids = [int(cid.strip()) for cid in channel_ids.split(',')]
                for cid in parsed_ids:
                    channel = guild.get_channel(cid)
                    if channel and isinstance(channel, discord.TextChannel):
                        target_channels.append(channel)
                    else:
                        await interaction.followup.send(
                            f"⚠️ Warning: Channel ID {cid} not found or is not a text channel. Skipping.",
                            ephemeral=True
                        )
                        logger.warning(f"Channel {cid} not found in guild {guild.name}")
                
                if not target_channels:
                    await interaction.followup.send("❌ No valid channels found!", ephemeral=True)
                    return
            except ValueError as e:
                await interaction.followup.send(
                    f"❌ Invalid channel ID format! Use comma-separated numbers (e.g., 123456789,987654321)",
                    ephemeral=True
                )
                return
        else:
            # No specific channels - use all text channels
            target_channels = guild.text_channels
        
        # Send status update
        channel_list = ", ".join([f"#{c.name}" for c in target_channels[:5]])
        if len(target_channels) > 5:
            channel_list += f" and {len(target_channels) - 5} more"
        
        await interaction.followup.send(
            f"🔄 Starting backfill for **{guild.name}**\n"
            f"📅 From: `{from_datetime.date()}`\n"
            f"📅 To: `{to_datetime.date()}`\n"
            f"📺 Channels: {channel_list}\n"
            f"⏳ This may take a while...\n\n"
            f"**Status updates will be logged to the console.**",
            ephemeral=True
        )
        
        logger.info(
            f"Manual backfill started by {interaction.user.name} for guild {guild.name} "
            f"from {from_datetime} to {to_datetime} - {len(target_channels)} channels"
        )
        
        # Perform backfill with progress tracking
        success_messages = 0
        failed_messages = 0
        channels_processed = 0
        last_update_time = asyncio.get_event_loop().time()
        
        for channel in target_channels:
            channel_success = 0
            channel_failed = 0
            
            try:
                logger.info(f"Starting backfill for channel #{channel.name}")
                
                async for message in channel.history(limit=None, after=from_datetime, before=to_datetime):
                    try:
                        await interaction.client.log_message(message, is_catchup=True)
                        channel_success += 1
                    except Exception as e:
                        logger.error(f"Failed to log message {message.id} in #{channel.name}: {e}")
                        channel_failed += 1
                
                if channel_success or channel_failed:
                    logger.info(
                        f"Backfill channel #{channel.name}: "
                        f"Success: {channel_success:>6d}, Failed: {channel_failed:>6d}"
                    )
                    
            except discord.errors.Forbidden:
                logger.warning(f"Cannot access messages in #{channel.name} of {guild.name}")
            except Exception as e:
                logger.error(f"Error processing channel #{channel.name}: {e}", exc_info=True)
            
            success_messages += channel_success
            failed_messages += channel_failed
            channels_processed += 1
            
            # Send periodic updates every 5 minutes to keep interaction alive
            current_time = asyncio.get_event_loop().time()
            if current_time - last_update_time > 300:  # 5 minutes
                try:
                    await interaction.followup.send(
                        f"📊 Progress Update:\n"
                        f"✅ Channels: {channels_processed}/{len(target_channels)}\n"
                        f"📝 Messages: {success_messages:,} succeeded, {failed_messages:,} failed",
                        ephemeral=True
                    )
                    last_update_time = current_time
                except discord.errors.HTTPException as e:
                    logger.warning(f"Failed to send progress update: {e}")
        
        # Send completion message
        try:
            await interaction.followup.send(
                f"✅ Backfill complete for **{guild.name}**!\n"
                f"📺 Channels processed: **{channels_processed}**\n"
                f"📊 Successfully logged: **{success_messages:,}** messages\n"
                f"❌ Failed: **{failed_messages:,}** messages",
                ephemeral=True
            )
        except discord.errors.HTTPException as e:
            logger.error(f"Failed to send completion message (token likely expired): {e}")
        
        logger.info(
            f"Manual backfill completed for guild {guild.name} - "
            f"Channels: {channels_processed}, Success: {success_messages}, Failed: {failed_messages}"
        )
        
    except ValueError as e:
        try:
            await interaction.followup.send(
                f"❌ Invalid date format! Use: YYYY-MM-DD (e.g., 2022-01-01)",
                ephemeral=True
            )
        except discord.errors.HTTPException:
            logger.error(f"Failed to send error message (token expired): {e}")
    except Exception as e:
        logger.error(f"Error in backfill command: {e}", exc_info=True)
        try:
            await interaction.followup.send(f"❌ Error during backfill: {e}", ephemeral=True)
        except discord.errors.HTTPException:
            logger.error(f"Failed to send error message (token expired): {e}")


@app_commands.command(name="backfill_channels", description="Backfill messages for specific channels")
@app_commands.describe(
    from_date="Start date in YYYY-MM-DD format (e.g., 2022-01-01)",
    to_date="End date in YYYY-MM-DD format (optional, defaults to now)",
    channel="First channel to backfill",
    channel2="Second channel (optional)",
    channel3="Third channel (optional)",
    channel4="Fourth channel (optional)",
    channel5="Fifth channel (optional)"
)
@app_commands.default_permissions(administrator=True)
@admin_only
async def backfill_channels(
    interaction: discord.Interaction,
    from_date: str,
    channel: discord.TextChannel,
    to_date: str = None,
    channel2: discord.TextChannel = None,
    channel3: discord.TextChannel = None,
    channel4: discord.TextChannel = None,
    channel5: discord.TextChannel = None
):
    """Backfill messages for specific channels"""
    # Send initial deferred response
    await interaction.response.defer(ephemeral=True)
    
    try:
        # Parse dates
        from_datetime = datetime.strptime(from_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        
        if to_date:
            to_datetime = datetime.strptime(to_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        else:
            to_datetime = datetime.now(timezone.utc)
        
        # Validate date range
        if from_datetime >= to_datetime:
            await interaction.followup.send("❌ From date must be before to date!", ephemeral=True)
            return
        
        # Collect all specified channels
        target_channels = [channel]
        for ch in [channel2, channel3, channel4, channel5]:
            if ch is not None:
                target_channels.append(ch)
        
        # Send status
        channel_list = ", ".join([f"#{c.name}" for c in target_channels])
        await interaction.followup.send(
            f"🔄 Starting backfill for **{interaction.guild.name}**\n"
            f"📅 From: `{from_datetime.date()}`\n"
            f"📅 To: `{to_datetime.date()}`\n"
            f"📺 Channels: {channel_list}\n"
            f"⏳ This may take a while...",
            ephemeral=True
        )
        
        logger.info(
            f"Channel backfill started by {interaction.user.name} for {len(target_channels)} channels "
            f"from {from_datetime} to {to_datetime}"
        )
        
        # Perform backfill
        success_messages = 0
        failed_messages = 0
        channels_processed = 0
        last_update_time = asyncio.get_event_loop().time()
        
        for ch in target_channels:
            channel_success = 0
            channel_failed = 0
            
            try:
                logger.info(f"Starting backfill for channel #{ch.name}")
                
                async for message in ch.history(limit=None, after=from_datetime, before=to_datetime):
                    try:
                        await interaction.client.log_message(message, is_catchup=True)
                        channel_success += 1
                    except Exception as e:
                        logger.error(f"Failed to log message {message.id} in #{ch.name}: {e}")
                        channel_failed += 1
                
                if channel_success or channel_failed:
                    logger.info(
                        f"Backfill channel #{ch.name}: "
                        f"Success: {channel_success:>6d}, Failed: {channel_failed:>6d}"
                    )
                    
            except discord.errors.Forbidden:
                logger.warning(f"Cannot access messages in #{ch.name}")
            except Exception as e:
                logger.error(f"Error processing channel #{ch.name}: {e}", exc_info=True)
            
            success_messages += channel_success
            failed_messages += channel_failed
            channels_processed += 1
            
            # Send periodic updates
            current_time = asyncio.get_event_loop().time()
            if current_time - last_update_time > 300:  # 5 minutes
                try:
                    await interaction.followup.send(
                        f"📊 Progress: {channels_processed}/{len(target_channels)} channels, "
                        f"{success_messages:,} messages logged",
                        ephemeral=True
                    )
                    last_update_time = current_time
                except discord.errors.HTTPException as e:
                    logger.warning(f"Failed to send progress update: {e}")
        
        # Send completion
        try:
            await interaction.followup.send(
                f"✅ Backfill complete!\n"
                f"📺 Channels: **{channels_processed}**\n"
                f"📊 Messages logged: **{success_messages:,}**\n"
                f"❌ Failed: **{failed_messages:,}**",
                ephemeral=True
            )
        except discord.errors.HTTPException as e:
            logger.error(f"Failed to send completion message: {e}")
        
        logger.info(
            f"Channel backfill completed - "
            f"Channels: {channels_processed}, Success: {success_messages}, Failed: {failed_messages}"
        )
        
    except ValueError:
        try:
            await interaction.followup.send(
                f"❌ Invalid date format! Use: YYYY-MM-DD (e.g., 2022-01-01)",
                ephemeral=True
            )
        except discord.errors.HTTPException as e:
            logger.error(f"Failed to send error message: {e}")
    except Exception as e:
        logger.error(f"Error in backfill_channels command: {e}", exc_info=True)
        try:
            await interaction.followup.send(f"❌ Error during backfill: {e}", ephemeral=True)
        except discord.errors.HTTPException:
            logger.error(f"Failed to send error message (token expired): {e}")


@app_commands.command(name="backfill_categories", description="Backfill messages for entire channel categories/groups")
@app_commands.describe(
    from_date="Start date in YYYY-MM-DD format (e.g., 2022-01-01)",
    to_date="End date in YYYY-MM-DD format (optional, defaults to now)",
    category="First category to backfill",
    category2="Second category (optional)",
    category3="Third category (optional)",
    category4="Fourth category (optional)",
    category5="Fifth category (optional)"
)
@app_commands.default_permissions(administrator=True)
@admin_only
async def backfill_categories(
    interaction: discord.Interaction,
    from_date: str,
    category: discord.CategoryChannel,
    to_date: str = None,
    category2: discord.CategoryChannel = None,
    category3: discord.CategoryChannel = None,
    category4: discord.CategoryChannel = None,
    category5: discord.CategoryChannel = None
):
    """Backfill messages for entire channel categories"""
    # Send initial deferred response
    await interaction.response.defer(ephemeral=True)
    
    try:
        # Parse dates
        from_datetime = datetime.strptime(from_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        
        if to_date:
            to_datetime = datetime.strptime(to_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        else:
            to_datetime = datetime.now(timezone.utc)
        
        # Validate date range
        if from_datetime >= to_datetime:
            await interaction.followup.send("❌ From date must be before to date!", ephemeral=True)
            return
        
        # Collect all specified categories
        target_categories = [category]
        for cat in [category2, category3, category4, category5]:
            if cat is not None:
                target_categories.append(cat)
        
        # Extract all text channels from the categories
        target_channels = []
        category_info = []
        for cat in target_categories:
            text_channels_in_cat = [ch for ch in cat.channels if isinstance(ch, discord.TextChannel)]
            target_channels.extend(text_channels_in_cat)
            category_info.append(f"**{cat.name}** ({len(text_channels_in_cat)} channels)")
        
        if not target_channels:
            await interaction.followup.send("❌ No text channels found in the specified categories!", ephemeral=True)
            return
        
        # Send status
        categories_list = ", ".join(category_info)
        await interaction.followup.send(
            f"🔄 Starting backfill for **{interaction.guild.name}**\n"
            f"📅 From: `{from_datetime.date()}`\n"
            f"📅 To: `{to_datetime.date()}`\n"
            f"📁 Categories: {categories_list}\n"
            f"📺 Total channels: **{len(target_channels)}**\n"
            f"⏳ This may take a while...\n\n"
            f"**Status updates will be logged to the console.**",
            ephemeral=True
        )
        
        logger.info(
            f"Category backfill started by {interaction.user.name} for {len(target_categories)} categories "
            f"({len(target_channels)} channels) from {from_datetime} to {to_datetime}"
        )
        
        # Perform backfill with progress tracking
        success_messages = 0
        failed_messages = 0
        channels_processed = 0
        last_update_time = asyncio.get_event_loop().time()
        
        for ch in target_channels:
            channel_success = 0
            channel_failed = 0
            
            try:
                category_name = ch.category.name if ch.category else "No Category"
                logger.info(f"Starting backfill for channel #{ch.name} (in {category_name})")
                
                async for message in ch.history(limit=None, after=from_datetime, before=to_datetime):
                    try:
                        await interaction.client.log_message(message, is_catchup=True)
                        channel_success += 1
                    except Exception as e:
                        logger.error(f"Failed to log message {message.id} in #{ch.name}: {e}")
                        channel_failed += 1
                
                if channel_success or channel_failed:
                    logger.info(
                        f"Backfill channel #{ch.name} ({category_name}): "
                        f"Success: {channel_success:>6d}, Failed: {channel_failed:>6d}"
                    )
                    
            except discord.errors.Forbidden:
                logger.warning(f"Cannot access messages in #{ch.name}")
            except Exception as e:
                logger.error(f"Error processing channel #{ch.name}: {e}", exc_info=True)
            
            success_messages += channel_success
            failed_messages += channel_failed
            channels_processed += 1
            
            # Send periodic updates every 5 minutes
            current_time = asyncio.get_event_loop().time()
            if current_time - last_update_time > 300:  # 5 minutes
                try:
                    await interaction.followup.send(
                        f"📊 Progress Update:\n"
                        f"✅ Channels: {channels_processed}/{len(target_channels)}\n"
                        f"📝 Messages: {success_messages:,} succeeded, {failed_messages:,} failed",
                        ephemeral=True
                    )
                    last_update_time = current_time
                except discord.errors.HTTPException as e:
                    logger.warning(f"Failed to send progress update: {e}")
        
        # Send completion
        try:
            await interaction.followup.send(
                f"✅ Backfill complete for **{interaction.guild.name}**!\n"
                f"📁 Categories processed: **{len(target_categories)}**\n"
                f"📺 Channels processed: **{channels_processed}**\n"
                f"📊 Messages logged: **{success_messages:,}**\n"
                f"❌ Failed: **{failed_messages:,}**",
                ephemeral=True
            )
        except discord.errors.HTTPException as e:
            logger.error(f"Failed to send completion message: {e}")
        
        logger.info(
            f"Category backfill completed - "
            f"Categories: {len(target_categories)}, Channels: {channels_processed}, "
            f"Success: {success_messages}, Failed: {failed_messages}"
        )
        
    except ValueError:
        try:
            await interaction.followup.send(
                f"❌ Invalid date format! Use: YYYY-MM-DD (e.g., 2022-01-01)",
                ephemeral=True
            )
        except discord.errors.HTTPException as e:
            logger.error(f"Failed to send error message: {e}")
    except Exception as e:
        logger.error(f"Error in backfill_categories command: {e}", exc_info=True)
        try:
            await interaction.followup.send(f"❌ Error during backfill: {e}", ephemeral=True)
        except discord.errors.HTTPException:
            logger.error(f"Failed to send error message (token expired): {e}")


@app_commands.command(name="backfill_all", description="Backfill messages for ALL guilds (use with caution!)")
@app_commands.describe(
    from_date="Start date in YYYY-MM-DD format (e.g., 2022-01-01)",
    to_date="End date in YYYY-MM-DD format (optional, defaults to now)"
)
@app_commands.default_permissions(administrator=True)
@admin_only
async def backfill_all_guilds(interaction: discord.Interaction, from_date: str, to_date: str = None):
    """Backfill messages for ALL guilds the bot is in"""
    try:
        # Parse dates - enforce YYYY-MM-DD format
        from_datetime = datetime.strptime(from_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        
        if to_date:
            to_datetime = datetime.strptime(to_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        else:
            to_datetime = datetime.now(timezone.utc)
        
        # Validate date range
        if from_datetime >= to_datetime:
            await interaction.response.send_message("❌ From date must be before to date!", ephemeral=True)
            return
        
        # Send confirmation message
        await interaction.response.send_message(
            f"⚠️ **WARNING**: This will backfill **{len(interaction.client.guilds)}** guilds!\n"
            f"📅 From: `{from_datetime.date()}`\n"
            f"📅 To: `{to_datetime.date()}`\n\n"
            f"Click the button below to confirm within 30 seconds.",
            view=BackfillConfirmView(interaction.user, from_datetime, to_datetime),
            ephemeral=True
        )
        
    except ValueError as e:
        await interaction.response.send_message(
            f"❌ Invalid date format! Use: YYYY-MM-DD (e.g., 2022-01-01)",
            ephemeral=True
        )
    except Exception as e:
        await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)
        logger.error(f"Error in backfill_all command: {e}", exc_info=True)


@app_commands.command(name="sync", description="Sync slash commands (owner only)")
@app_commands.describe(
    scope="Sync scope: 'global' for all servers, 'guild' for current server only"
)
@bot_owner_only
async def sync_commands(interaction: discord.Interaction, scope: str = "guild"):
    """Sync slash commands to Discord"""
    
    try:
        await interaction.response.defer(ephemeral=True)
        
        if scope.lower() == "global":
            # Sync globally (takes up to 1 hour to propagate)
            synced = await interaction.client.tree.sync()
            await interaction.followup.send(
                f"✅ Synced {len(synced)} commands globally.\n"
                f"⏳ May take up to 1 hour to appear in all servers.",
                ephemeral=True
            )
            logger.info(f"Commands synced globally by {interaction.user.name}: {len(synced)} commands")
            
        elif scope.lower() == "guild":
            # Sync to current guild (instant)
            if not interaction.guild:
                await interaction.followup.send("❌ This command must be used in a server for guild sync!", ephemeral=True)
                return
                
            interaction.client.tree.copy_global_to(guild=interaction.guild)
            synced = await interaction.client.tree.sync(guild=interaction.guild)
            await interaction.followup.send(
                f"✅ Synced {len(synced)} commands to **{interaction.guild.name}**.\n"
                f"Commands should appear immediately.",
                ephemeral=True
            )
            logger.info(f"Commands synced to guild {interaction.guild.name} by {interaction.user.name}: {len(synced)} commands")
            
        else:
            await interaction.followup.send(
                f"❌ Invalid scope! Use 'global' or 'guild'.",
                ephemeral=True
            )
            
    except Exception as e:
        await interaction.followup.send(f"❌ Error syncing commands: {e}", ephemeral=True)
        logger.error(f"Error in sync command: {e}", exc_info=True)


class BackfillConfirmView(discord.ui.View):
    """View with confirmation button for backfill_all"""
    def __init__(self, user: discord.User, from_datetime: datetime, to_datetime: datetime):
        super().__init__(timeout=30.0)
        self.user = user
        self.from_datetime = from_datetime
        self.to_datetime = to_datetime
        
    @discord.ui.button(label="✅ Confirm Backfill", style=discord.ButtonStyle.danger)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.user:
            await interaction.response.send_message("❌ Only the command user can confirm!", ephemeral=True)
            return
        
        # Disable the button
        button.disabled = True
        await interaction.response.edit_message(view=self)
        
        # Start backfill
        await interaction.followup.send("🔄 Starting backfill for all guilds...", ephemeral=True)
        
        total_success = 0
        total_failed = 0
        
        for guild in interaction.client.guilds:
            logger.info(f"Backfilling guild: {guild.name}")
            
            guild_success = 0
            guild_failed = 0
            
            for channel in guild.text_channels:
                try:
                    async for message in channel.history(limit=None, after=self.from_datetime, before=self.to_datetime):
                        try:
                            await interaction.client.log_message(message, is_catchup=True)
                            guild_success += 1
                        except Exception as e:
                            logger.error(f"Failed to log message {message.id}: {e}")
                            guild_failed += 1
                            
                except discord.errors.Forbidden:
                    logger.warning(f"Cannot access messages in {channel.name} of {guild.name}")
                except Exception as e:
                    logger.error(f"Error processing channel {channel.name}: {e}")
            
            total_success += guild_success
            total_failed += guild_failed
            
            logger.info(
                f"Completed guild {guild.name} - "
                f"Success: {guild_success}, Failed: {guild_failed}"
            )
        
        await interaction.followup.send(
            f"✅ Backfill complete for **all {len(interaction.client.guilds)} guilds**!\n"
            f"📊 Total messages logged: **{total_success:,}**\n"
            f"❌ Total failed: **{total_failed:,}**",
            ephemeral=True
        )
    
    async def on_timeout(self):
        # Disable all buttons on timeout
        for item in self.children:
            item.disabled = True


async def main():
    bot = DiscordScrapeBot()

    bot.tree.add_command(backfill_messages)
    bot.tree.add_command(backfill_channels)
    bot.tree.add_command(backfill_categories)
    bot.tree.add_command(backfill_all_guilds)
    bot.tree.add_command(sync_commands)
    
    try:
        await bot.start(bot.config.discordToken)
    except KeyboardInterrupt:
        logger.info("Received shutdown signal")
    finally:
        await bot.close()


if __name__ == "__main__":
    asyncio.run(main())