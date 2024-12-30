import os
import csv
import json
import time
import requests
import signal
import discord
import asyncio
import datetime
import discord
import logging
from dotenv import load_dotenv
from discord import Message, app_commands

logger = logging.getLogger("discord")


def save_last_boot_time():
    print("Saving last boot time.")
    if not os.path.exists("previous_boot.json"):
        with open("previous_boot.json", "w") as file:
            file.write("{}")
    previous_boot = None
    with open("previous_boot.json", "r") as file:
        previous_boot = json.loads(file.read())
    previous_boot["last_boot_time"] = str(datetime.datetime.now(datetime.UTC))
    with open("previous_boot.json", "w") as file:
        file.write(json.dumps(previous_boot, indent=4))


def signal_handler(signum, frame):
    save_last_boot_time()
    exit(1)


class DiscordScrapeBot(discord.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.logger_url = os.getenv("LOGGER_API_URL")

    def generate_message_payload(self, message: Message) -> dict:
        message_data = {
            "id": message.id,
            "content": message.content,
            "channel_id": message.channel.id,
            "channel_name": (
                message.channel.name if hasattr(message.channel, "name") else None
            ),
            "author_id": message.author.id,
            "author_name": message.author.name,
            "author_discriminator": message.author.discriminator,
            "created_at": message.created_at.isoformat(),
            "edited_at": message.edited_at.isoformat() if message.edited_at else None,
            "attachments": [
                {
                    "id": attachment.id,
                    "filename": attachment.filename,
                    "url": attachment.url,
                    "size": attachment.size,
                }
                for attachment in message.attachments
            ],
            "embeds": [embed.to_dict() for embed in message.embeds],
            "stickers": [
                {"id": sticker.id, "name": sticker.name, "url": sticker.url}
                for sticker in message.stickers
            ],
        }
        return message_data

    async def grab_messages_after(self, after):
        guild = self.get_guild(int(os.getenv("GUILD_ID")))
        success_messages = 0
        failed_messages = 0
        for channel in guild.text_channels[::-1]:
            channel_success_messages = 0
            channel_failed_messages = 0
            try:
                async for message in channel.history(limit=None, after=after):
                    payload = self.generate_message_payload(message)
                    response = requests.post(
                        self.logger_url,
                        data=json.dumps(payload),
                        headers={"Content-Type": "application/json"},
                    )
                    if response.status_code not in [200, 201]:
                        logger.error(f"Error encountered logging the data to the database: {response.text}")
                        channel_failed_messages += 1
                    else:
                        channel_success_messages += 1
                if channel_failed_messages or channel_success_messages:
                    logger.info(f"Successful Messages from channel {channel.name} inserted into database: {channel_success_messages: >6d}")
                    logger.info(f"Failed Messages from channel {channel.name} not inserted into database: {channel_failed_messages: >6d}")
            except discord.errors.Forbidden:
                logger.warning(f"Cannot access messages in {channel.name} of {guild.name}")
            except Exception as e:
                print(e)
            success_messages += channel_success_messages
            failed_messages += channel_failed_messages
        logger.info(f"Total messages successfully inserted since last boot at {after}: {success_messages}")
        logger.info(f"Total messages unsuccessfully inserted since last boot at {after}: {failed_messages}")

    async def on_ready(self):
        """
        When the bot is ready, grab all of the messages since the
        last time the bot was ran.
        """
        logger.info("Bot is ready!")
        previous_boot_data = None
        with open("previous_boot.json", "r") as f:
            previous_boot_data = json.loads(f.read())
        if previous_boot_data:
            date_format = "%Y-%m-%d %H:%M:%S.%f%z"
            previous_boot_time = datetime.datetime.strptime(
                previous_boot_data["last_boot_time"], date_format
            )
        logger.info(f"Grabbing and logging messages since last boot. Last boot: {previous_boot_time}")
        await self.grab_messages_after(previous_boot_time)

    async def on_message(self, message: Message):
        """
        When a message is sent, log this message to the database
        """

        if message.author == self.user:
            return

        message_data = self.generate_message_payload(message)

        logger.info(
            f"Inserting message at {message.created_at} from {message.author} into the database."
        )
        response = requests.post(
            self.logger_url,
            data=json.dumps(message_data),
            headers={"Content-Type": "application/json"},
        )
        logger.info(
            f"Logged message to database with status code of {response.status_code}"
        )
        if response.status_code not in [200, 201]:
            logger.error(
                f"Error encountered logging the data to the database: {response.text}"
            )

    async def on_message_edit(self, before, after):
        """
        Detect when a user edits a message and log the changes.
        """

        logger.info("on_message_edit event recieved!")

        if before.author == self.user:
            return
        

if __name__ == "__main__":
    load_dotenv(override=True)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True
    intents.voice_states = True

    client = DiscordScrapeBot(intents=intents)

    try:
        client.run(os.getenv("BOT_TOKEN"))
    except Exception as e:
        logging.error(f"EXCEPTION: exception encountered -> {e}")
    finally:
        save_last_boot_time()
