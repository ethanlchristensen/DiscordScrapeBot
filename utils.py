import os
import csv
import sqlite3
import discord
import datetime


def insert_record(data: dict[str, any]):
    assert 'message_timestamp' in data, '"message_timestamp" must be provided'
    assert 'message_id' in data, '"message_id" must be provided'
    assert 'user_id' in data, '"user_id" must be provided'
    assert 'user_name' in data, '"user_name" must be provided'
    assert 'channel_id' in data, '"channel_id" must be provided'
    assert 'channel_name' in data, '"channel_name" must be provided'
    assert 'message_text' in data, '"message_text" must be provided'
    assert 'message_image_url' in data, '"message_image_url" must be provided'
    
    data['record_inserted_timestamp'] = datetime.datetime.now(datetime.UTC)
    
    conn = sqlite3.connect('db.sqlite')
    cursor = conn.cursor()
    
    insert_query = """
    INSERT INTO DiscordMessages (record_inserted_timestamp, message_timestamp, message_id, user_id, user_name, channel_id, channel_name, message_text, message_image_url)
    VALUES (:record_inserted_timestamp, :message_timestamp, :message_id, :user_id, :user_name, :channel_id, :channel_name, :message_text, :message_image_url);
    """
    
    try:
        cursor.execute(insert_query, data)
        conn.commit()
    except Exception as e:
        print(f'ERROR: {e}')
    finally:
        conn.close()

async def grab_old_messages(client: discord.Client, last_boot_time):
    guild = client.get_guild(int(os.getenv("GUILD_ID")))
    total_messages = 0
    for channel in guild.text_channels:
        user_id = None
        user_name = None
        message_timestamp = None
        channel_id = None
        channel_name = None
        message_text = None
        message_image_url = None
        try:
            async for message in channel.history(limit=None, after=last_boot_time):
                if message.embeds:
                    for embed in message.embeds:
                        if embed.image:
                            message_image_url = embed.image.url
                            break
                user_id = message.author.id
                user_name = message.author.global_name
                message_timestamp = message.created_at
                message_id = message.id
                channel_id = channel.id
                channel_name = channel.name
                message_text = message.content
            
                insert_record({
                    'message_timestamp': message_timestamp,
                    'message_id': message_id,
                    'user_id': user_id,
                    'user_name': user_name,
                    'channel_id': channel_id,
                    'channel_name': channel_name,
                    'message_text': message_text,
                    'message_image_url': message_image_url
                })
                
                total_messages += 1
            
        except discord.errors.Forbidden:
            print(f'Cannot access messages in {channel.name} of {guild.name}')
        except Exception as e:
            print(e)
    
    print(f'Total messages since last boot at {last_boot_time}: {total_messages}')

async def insert_message(message: discord.Message):
    user_name = None
    message_timestamp = None
    message_id = message.id
    channel_id = None
    channel_name = None
    message_text = None
    message_image_url = None
    try:
        if message.embeds:
            for embed in message.embeds:
                if embed.image:
                    message_image_url = embed.image.url
                    break
        user_id = message.author.id
        user_name = message.author.global_name
        message_timestamp = message.created_at
        channel_id = message.channel.id
        channel_name = message.channel.name
        message_text = message.content
        
        insert_record({
            'message_timestamp': message_timestamp,
            'message_id': message_id,
            'user_id': user_id,
            'user_name': user_name,
            'channel_id': channel_id,
            'channel_name': channel_name,
            'message_text': message_text,
            'message_image_url': message_image_url
        })
    except Exception as e:
        print(f'ERROR: failed to insert new message into db: {e}')
