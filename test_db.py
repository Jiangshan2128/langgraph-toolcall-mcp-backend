"""Test Supabase database connection once."""

import os
from dotenv import load_dotenv

load_dotenv()

conn_str = os.getenv("DATABASE_URL")
if not conn_str:
    print("❌ DATABASE_URL not set in .env")
    exit(1)

print("Connecting to Supabase PostgreSQL (single attempt)...")

try:
    import psycopg
    conn = psycopg.connect(conn_str, connect_timeout=10)
    with conn.cursor() as cur:
        cur.execute("SELECT version();")
        version = cur.fetchone()[0]
    conn.close()
    print("✅ Connection successful!")
    print(f"   Server: {version}")
    exit(0)
except Exception as e:
    print(f"❌ Connection failed: {e}")
    exit(1)
