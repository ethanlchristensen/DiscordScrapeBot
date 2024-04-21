import sqlite3
import pandas as pd

db = 'db.sqlite'
table = 'DiscordMessages'
column = 'message_id'
conn = sqlite3.connect(db)
query = f'SELECT * FROM {table}'
df = pd.read_sql_query(query, conn)
df_cleaned = df.drop_duplicates(subset=[column])
conn.execute(f'DROP TABLE IF EXISTS {table}')
df_cleaned.to_sql(table, conn, index=False)
conn.close()
print(f'{len(df)} records to {len(df_cleaned)} records.')

