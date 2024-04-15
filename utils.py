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
    assert 'message_file_urls' in data, '"message_file_urls" must be provided'
    
    data['record_inserted_timestamp'] = datetime.datetime.now(datetime.UTC)
    
    conn = sqlite3.connect('db.sqlite')
    cursor = conn.cursor()
    
    insert_query = """
    INSERT INTO DiscordMessages (record_inserted_timestamp, message_timestamp, message_id, user_id, user_name, channel_id, channel_name, message_text, message_file_urls)
    VALUES (:record_inserted_timestamp, :message_timestamp, :message_id, :user_id, :user_name, :channel_id, :channel_name, :message_text, :message_file_urls);
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
        message_file_urls = None
        try:
            async for message in channel.history(limit=None, after=last_boot_time):
                if message.attachments:
                    for attachment in message.attachments:
                        message_file_urls.append(attachment.url)
                if message.embeds:
                    for embed in message.embeds:
                        if embed.image:
                            message_file_urls.append(embed.image.url)
                if message.stickers:
                    for sticker in message.stickers:
                        message_file_urls.append(sticker.url)
                if not message_file_urls:
                    message_file_urls = None
                else:
                    message_file_urls = " | ".join(message_file_urls)
                    
                user_id = message.author.id
                user_name = message.author.global_name
                if not user_name: user_name = message.author.name
                if not user_name: user_name = message.author.display_name
                if not user_name: user_name = "Unknown"
                message_timestamp = message.created_at
                message_id = message.id
                channel_id = channel.id
                channel_name = channel.name
                message_text = message.content
                if message_text is None or message_text.strip() == '':
                    if message.embeds:
                        for embed in message.embeds:
                            message_text = extract_text_from_embed(embed)
                            break
            
                insert_record({
                    'message_timestamp': message_timestamp,
                    'message_id': message_id,
                    'user_id': user_id,
                    'user_name': user_name,
                    'channel_id': channel_id,
                    'channel_name': channel_name,
                    'message_text': message_text,
                    'message_file_urls': message_file_urls
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
    message_file_urls = []
    try:
        if message.attachments:
            for attachment in message.attachments:
                message_file_urls.append(attachment.url)
        if message.embeds:
            for embed in message.embeds:
                if embed.image:
                    message_file_urls.append(embed.image.url)
        if message.stickers:
            for sticker in message.stickers:
                message_file_urls.append(sticker.url)
        
        if not message_file_urls:
            message_file_urls = None
        else:
            message_file_urls = " | ".join(message_file_urls)
        
        user_id = message.author.id
        user_name = message.author.global_name
        message_timestamp = message.created_at
        channel_id = message.channel.id
        channel_name = message.channel.name
        message_text = message.content
        
        if message_text is None or message_text.strip() == '':
            if message.embeds:
                for embed in message.embeds:
                    message_text = extract_text_from_embed(embed)
                    break
        
        insert_record({
            'message_timestamp': message_timestamp,
            'message_id': message_id,
            'user_id': user_id,
            'user_name': user_name,
            'channel_id': channel_id,
            'channel_name': channel_name,
            'message_text': message_text,
            'message_file_urls': message_file_urls
        })
    except Exception as e:
        print(f'ERROR: failed to insert new message into db: {e}')


def extract_text_from_embed(embed):
    full_text = ""
    if embed.title:
        full_text += embed.title + "\n"
    if embed.description:
        full_text += embed.description + "\n"
    for field in embed.fields:
        full_text += field.name + "\n" + field.value + "\n"
    if embed.footer:
        full_text += embed.footer.text + "\n"
    if embed.author:
        full_text += embed.author.name + "\n"
    return full_text