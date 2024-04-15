import os
import csv
import json
import time
import signal
import discord
import asyncio
import datetime

from dotenv import load_dotenv
from utils import grab_old_messages, insert_message
from discord import Message, app_commands


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
    print(f'Signal handler called with signal: {signum}')
    save_last_boot_time()
    exit(1)

class MyClient(discord.Client):
    async def on_ready(self):
        """
        When the bot is ready, grab all of the messages since the
        last time the bot was ran.
        """
        previous_boot_data = None
        with open('previous_boot.json', 'r') as f:
            previous_boot_data = json.loads(f.read())
        if previous_boot_data:
            date_format = "%Y-%m-%d %H:%M:%S.%f%z"
            previous_boot_time = datetime.datetime.strptime(previous_boot_data['last_boot_time'], date_format)
        
        print(f'Grabbing and logging messages since last boot. Last boot: {previous_boot_time}')
        await grab_old_messages(self, previous_boot_time)
        
    async def on_message(self, message: Message):
        """
        When a message is sent, log this message to the database
        """
        if message.author == self.user:
            return
        else:
            await insert_message(message)
    
    async def on_edit(self, before, after):
        """
        TODO: Log when the user edits a message and save this
        to a seperate table.
        """
        pass
            
# incase program is signal to end, save the last boot time
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# set up intents
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True

# create client
client = MyClient(intents=intents)

try:
    # run the bot
    load_dotenv(override=True)
    client.run(os.getenv("BOT_TOKEN"))
except Exception as e:
    print(f'EXCEPTION: exception encountered -> {e}')
finally:
    # save the last boot time
    save_last_boot_time()
        