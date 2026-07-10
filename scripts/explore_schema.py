import os
from databricks import sql

host = os.environ["DBT_DATABRICKS_HOST"]
http_path = os.environ["DBT_DATABRICKS_HTTP_PATH"]
token = os.environ["DBT_DATABRICKS_TOKEN"]
catalog = os.environ.get("DBT_DATABRICKS_CATALOG", "hive_metastore")
schema = os.environ.get("DBT_DATABRICKS_SCHEMA", "default")

conn = sql.connect(server_hostname=host, http_path=http_path, access_token=token)
cursor = conn.cursor()

print(f"=== Tables in {catalog}.{schema} ===")
cursor.execute(f"SHOW TABLES IN {catalog}.{schema}")
tables = [row.tableName for row in cursor.fetchall()]
print(tables)

for t in tables:
    fq = f"{catalog}.{schema}.{t}"
    print(f"\n=== DESCRIBE {fq} ===")
    cursor.execute(f"DESCRIBE TABLE {fq}")
    for row in cursor.fetchall():
        print(row)

    print(f"\n=== Row count {fq} ===")
    cursor.execute(f"SELECT COUNT(*) AS cnt FROM {fq}")
    print(cursor.fetchall())

    print(f"\n=== Sample rows {fq} ===")
    cursor.execute(f"SELECT * FROM {fq} LIMIT 5")
    cols = [c[0] for c in cursor.description]
    print(cols)
    for row in cursor.fetchall():
        print(row)

cursor.close()
conn.close()
