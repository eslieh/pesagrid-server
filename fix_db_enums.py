
import sqlalchemy
import os
from dotenv import load_dotenv

# Load credentials
load_dotenv()
db_url = os.getenv("DATABASE_URL")

if not db_url:
    print("❌ Error: No DATABASE_URL found in .env")
    exit(1)

# Connect and update
engine = sqlalchemy.create_engine(db_url)
print(f"Connecting to database...")

statements = [
    # Adding Uppercase to match SQLAlchemy Enum names (default behavior)
    "ALTER TYPE templatechannel ADD VALUE IF NOT EXISTS 'ALL'",
    "ALTER TYPE templatetype ADD VALUE IF NOT EXISTS 'OBLIGATION_CREATED'",
    "ALTER TYPE templatetype ADD VALUE IF NOT EXISTS 'OBLIGATION_CANCELLED'",
    
    # Keeping lowercase just in case
    "ALTER TYPE templatechannel ADD VALUE IF NOT EXISTS 'all'",
    "ALTER TYPE templatetype ADD VALUE IF NOT EXISTS 'obligation_created'",
    "ALTER TYPE templatetype ADD VALUE IF NOT EXISTS 'obligation_cancelled'"
]

# We MUST use AUTOCOMMIT isolation level for ENUM additions in PostgreSQL
with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
    for sql in statements:
        try:
            conn.execute(sqlalchemy.text(sql))
            print(f"✅ Ran: {sql}")
        except Exception as e:
            # If 'IF NOT EXISTS' isn't supported, we try without it and catch the 'already exists' error
            if "already exists" in str(e).lower():
                print(f"ℹ️ Info: Skipping '{sql}' (already exists)")
            else:
                try:
                    conn.execute(sqlalchemy.text(sql.replace(" IF NOT EXISTS", "")))
                    print(f"✅ Ran: {sql.replace(' IF NOT EXISTS', '')}")
                except Exception as e2:
                    print(f"❌ Error: Failed to run '{sql}': {e2}")

print("\n🚀 Database synchronization complete!")
