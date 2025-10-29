import os
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional
from concurrent.futures import ThreadPoolExecutor

import discord
from discord.ext import commands
from pymongo import MongoClient
import gridfs
from dotenv import load_dotenv

# Load environment variables
load_dotenv('.env')

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# MongoDB document size limit (16MB) - use a safety margin
MAX_DOCUMENT_SIZE = 15 * 1024 * 1024  # 15MB
MAX_ATTACHMENT_INLINE_SIZE = 5 * 1024 * 1024  # 5MB - store larger files in GridFS


class DiscordScrapeBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.messages = True
        intents.reactions = True
        intents.guilds = True
        intents.members = True
        
        super().__init__(command_prefix="!", intents=intents)
        
        # MongoDB setup with pymongo
        self.mongo_client = MongoClient(os.getenv("MONGO_URI"))
        self.db = self.mongo_client["DiscordScrapeBot"]
        self.messages_collection = self.db["Messages"]
        
        # GridFS for large attachments
        self.fs = gridfs.GridFS(self.db)
        
        # Thread pool for running sync MongoDB operations
        self.executor = ThreadPoolExecutor(max_workers=5)
        
        # Track last boot time
        self.boot_time = None
        self.last_shutdown_time = None
        
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
        
    async def on_ready(self):
        """Called when bot successfully connects to Discord"""
        logger.info(f"Bot logged in as {self.user.name} ({self.user.id})")
        logger.info(f"Connected to {len(self.guilds)} guilds")
        
        # Get last shutdown time from database
        loop = asyncio.get_event_loop()
        bot_status = await loop.run_in_executor(
            self.executor,
            lambda: self.db["BotStatus"].find_one({"_id": "last_shutdown"})
        )
        
        if bot_status and bot_status.get("timestamp"):
            self.last_shutdown_time = bot_status["timestamp"]
            logger.info(f"Last shutdown was at: {self.last_shutdown_time}")
            
            # Catch up on missed messages
            await self.catch_up_messages(after=self.last_shutdown_time)
        else:
            logger.info("No previous shutdown time found - first boot or database reset")
        
        # Update boot time
        self.boot_time = datetime.now(timezone.utc)
        await loop.run_in_executor(
            self.executor,
            lambda: self.db["BotStatus"].update_one(
                {"_id": "last_boot"},
                {"$set": {"timestamp": self.boot_time}},
                upsert=True
            )
        )
        logger.info(f"Boot time recorded: {self.boot_time}")
        
    async def close(self):
        """Called when bot is shutting down"""
        shutdown_time = datetime.now(timezone.utc)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            self.executor,
            lambda: self.db["BotStatus"].update_one(
                {"_id": "last_shutdown"},
                {"$set": {"timestamp": shutdown_time}},
                upsert=True
            )
        )
        logger.info(f"Shutdown time recorded: {shutdown_time}")
        
        self.executor.shutdown(wait=True)
        self.mongo_client.close()
        await super().close()
        
    async def catch_up_messages(self, after: datetime):
        """Download all messages that were sent while bot was offline"""
        logger.info(f"Starting message catch-up from {after}")
        
        success_messages = 0
        failed_messages = 0
        
        for guild in self.guilds:
            logger.info(f"Catching up messages in guild: {guild.name}")
            
            for channel in guild.text_channels:
                channel_success = 0
                channel_failed = 0
                
                try:
                    async for message in channel.history(limit=None, after=after):
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
        
        logger.info(f"Total catch-up messages successfully inserted: {success_messages}")
        logger.info(f"Total catch-up messages failed: {failed_messages}")
        
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


async def main():
    bot = DiscordScrapeBot()
    
    try:
        await bot.start(os.getenv("BOT_TOKEN"))
    except KeyboardInterrupt:
        logger.info("Received shutdown signal")
    finally:
        await bot.close()


if __name__ == "__main__":
    asyncio.run(main())