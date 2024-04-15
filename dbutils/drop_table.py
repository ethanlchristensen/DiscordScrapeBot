import sqlite3
conn = sqlite3.connect('db.sqlite')
cursor = conn.cursor()
select_query = "DROP TABLE DiscordMessages;"
cursor.execute(select_query)
conn.close()