import os
import sqlite3
import datetime
import pandas as pd

db_path = os.getcwd() + '/db.sqlite'
print(f'Selecting records from "{db_path}"')
conn = sqlite3.connect(db_path)
select_query = input('Enter your query: ')
df = pd.read_sql_query(select_query, conn)
conn.close()
current_datetime = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
filename = f'export-{current_datetime}.csv'
if not os.path.exists('/dbutils/exports'):
    os.makedirs('/dbutils/exports')
export_folder = os.getcwd() + '/dbutils/exports/'
df.to_csv(export_folder + filename, index=False)
print(f'DataFrame saved to {export_folder + filename}')
