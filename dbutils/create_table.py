import sqlite3
conn = sqlite3.connect('db.sqlite')
cursor = conn.cursor()
create_table_query = """
CREATE TABLE IF NOT EXISTS DiscordMessages (
    id INTEGER PRIMARY KEY,
    record_inserted_timestamp DATETIME,
    message_id INTEGER,
    message_timestamp DATETIME,
    user_id INTEGER,
    user_name TEXT,
    channel_name TEXT,
    channel_id INTEGER,
    message_text TEXT,
    message_file_urls TEXT
);
"""
cursor.execute(create_table_query)
conn.commit()
conn.close()
