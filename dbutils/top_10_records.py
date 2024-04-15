import os
import sqlite3

db_path = os.getcwd() + '/db.sqlite'
print(f'Selecting records from "{db_path}"')
conn = sqlite3.connect(db_path)
cursor = conn.cursor()
select_query = "SELECT message_timestamp, user_name, channel_name, message_text, message_image_url FROM DiscordMessages ORDER BY message_timestamp DESC LIMIT 10;"
cursor.execute(select_query)
records = cursor.fetchall()

if records:
    for record in records:
        print('| ' + ' | '.join([f'{str(val)[:25]:^25s}' for val in record]) + ' |')
else:
    print("No records in table.")

cursor.close()
conn.close()