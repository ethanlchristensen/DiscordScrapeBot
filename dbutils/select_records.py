import os
import sqlite3

db_path = os.getcwd() + '/db.sqlite'
print(f'Selecting records from "{db_path}"')
conn = sqlite3.connect(db_path)
cursor = conn.cursor()
select_query = "SELECT * FROM DiscordMessages;"
cursor.execute(select_query)
records = cursor.fetchall()

if records:
    print(f'Got {len(records)} rows from the table.')
else:
    print("No records in table.")

cursor.close()
conn.close()