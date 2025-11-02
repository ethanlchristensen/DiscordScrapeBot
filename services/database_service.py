import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Optional

import gridfs
from pymongo import MongoClient

logger = logging.getLogger(__name__)


class DatabaseService:
    """Service for handling all MongoDB and GridFS operations"""

    # MongoDB document size limit (16MB) - use a safety margin
    MAX_DOCUMENT_SIZE = 15 * 1024 * 1024
    MAX_ATTACHMENT_INLINE_SIZE = 5 * 1024 * 1024

    def __init__(self, mongo_uri: str):
        self.mongo_client = MongoClient(mongo_uri)
        self.db = self.mongo_client["DiscordScrapeBot"]
        self.messages_collection = self.db["Messages"]
        self.guild_status_collection = self.db["GuildStatus"]
        self.user_consent_collection = self.db["UserConsent"]
        self.fs = gridfs.GridFS(self.db)
        self.executor = ThreadPoolExecutor(max_workers=5)

    def _create_indexes(self):
        """Create database indexes for better query performance"""
        self.messages_collection.create_index("message_id", unique=True)
        self.messages_collection.create_index("channel_id")
        self.messages_collection.create_index("author_id")
        self.messages_collection.create_index("timestamp")
        self.messages_collection.create_index("deleted")
        self.messages_collection.create_index("guild_id")
        self.messages_collection.create_index([("guild_id", 1), ("author_id", 1)])
        self.guild_status_collection.create_index("guild_id", unique=True)
        self.user_consent_collection.create_index(
            [("guild_id", 1), ("user_id", 1)], unique=True
        )
        self.user_consent_collection.create_index("user_id")
        self.user_consent_collection.create_index("consent_active")

    async def setup_indexes(self):
        """Setup database indexes asynchronously"""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(self.executor, self._create_indexes)
        logger.info("Database indexes created/verified")

    async def upsert_message(self, message_payload: dict):
        """Insert or update a message in the database"""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            self.executor,
            lambda: self.messages_collection.update_one(
                {"message_id": message_payload["message_id"]},
                {"$set": message_payload},
                upsert=True,
            ),
        )

    async def update_message(self, message_id: int, update_data: dict):
        """Update a message in the database"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self.executor,
            lambda: self.messages_collection.update_one(
                {"message_id": message_id}, update_data
            ),
        )

    async def update_many_messages(self, filter_query: dict, update_data: dict):
        """Update multiple messages in the database"""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            self.executor,
            lambda: self.messages_collection.update_many(filter_query, update_data),
        )

    async def get_guild_status(self, guild_id: int) -> Optional[dict]:
        """Get guild status from database"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self.executor,
            lambda: self.guild_status_collection.find_one({"guild_id": guild_id}),
        )

    async def update_guild_status(self, guild_id: int, guild_name: str, data: dict):
        """Update guild status in database"""
        loop = asyncio.get_event_loop()
        update_data = {"guild_name": guild_name, **data}
        await loop.run_in_executor(
            self.executor,
            lambda: self.guild_status_collection.update_one(
                {"guild_id": guild_id}, {"$set": update_data}, upsert=True
            ),
        )

    async def store_file_in_gridfs(
        self,
        file_data: bytes,
        filename: str,
        message_id: int,
        attachment_id: int,
        content_type: str,
    ):
        """Store a file in GridFS"""
        loop = asyncio.get_event_loop()
        gridfs_id = await loop.run_in_executor(
            self.executor,
            lambda: self.fs.put(
                file_data,
                filename=filename,
                message_id=message_id,
                attachment_id=attachment_id,
                content_type=content_type,
                uploaded_at=datetime.now(timezone.utc),
            ),
        )
        return gridfs_id

    # Consent management methods

    async def get_user_consent(self, guild_id: int, user_id: int) -> Optional[dict]:
        """Get user consent record for a specific guild"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self.executor,
            lambda: self.user_consent_collection.find_one(
                {"guild_id": guild_id, "user_id": user_id}
            ),
        )

    async def upsert_user_consent(self, consent_record: dict):
        """Insert or update a user consent record"""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            self.executor,
            lambda: self.user_consent_collection.update_one(
                {
                    "guild_id": consent_record["guild_id"],
                    "user_id": consent_record["user_id"],
                },
                {"$set": consent_record},
                upsert=True,
            ),
        )

    async def revoke_user_consent(self, guild_id: int, user_id: int) -> bool:
        """Revoke user consent by setting consent_active to False"""
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            self.executor,
            lambda: self.user_consent_collection.update_one(
                {"guild_id": guild_id, "user_id": user_id},
                {
                    "$set": {
                        "consent_active": False,
                        "revoked_at": datetime.now(timezone.utc),
                    }
                },
            ),
        )
        return result.modified_count > 0

    async def count_user_messages(self, guild_id: int, user_id: int) -> int:
        """Count messages for a user in a specific guild"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self.executor,
            lambda: self.messages_collection.count_documents(
                {"guild_id": guild_id, "author_id": user_id}
            ),
        )

    async def delete_user_messages(self, guild_id: int, user_id: int) -> int:
        """Delete all messages for a user in a specific guild"""
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            self.executor,
            lambda: self.messages_collection.delete_many(
                {"guild_id": guild_id, "author_id": user_id}
            ),
        )
        return result.deleted_count

    async def delete_user_attachments(self, guild_id: int, user_id: int):
        """Delete all GridFS attachments for a user in a specific guild"""
        loop = asyncio.get_event_loop()

        # Find all messages with attachments stored in GridFS
        messages = await loop.run_in_executor(
            self.executor,
            lambda: list(
                self.messages_collection.find(
                    {
                        "guild_id": guild_id,
                        "author_id": user_id,
                        "attachments.storage": "gridfs",
                    },
                    {"attachments": 1},
                )
            ),
        )

        # Delete each GridFS file
        for message in messages:
            for attachment in message.get("attachments", []):
                if attachment.get("storage") == "gridfs":
                    gridfs_id = attachment.get("gridfs_id")
                    if gridfs_id:
                        try:
                            await loop.run_in_executor(
                                self.executor, lambda: self.fs.delete(gridfs_id)
                            )
                        except Exception as e:
                            logger.error(
                                f"Failed to delete GridFS file {gridfs_id}: {e}"
                            )

    def close(self):
        """Close database connections"""
        self.executor.shutdown(wait=True)
        self.mongo_client.close()
