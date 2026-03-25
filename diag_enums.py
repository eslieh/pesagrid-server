
import sqlalchemy
import os
from dotenv import load_dotenv

db_url = "postgresql://neondb_owner:npg_qrcQHVL91SOf@ep-shiny-term-alg4wuhv-pooler.c-3.eu-central-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
engine = sqlalchemy.create_engine(db_url)

with engine.connect() as conn:
    try:
        res = conn.execute(sqlalchemy.text("SELECT enum_range(NULL::templatechannel)"))
        print(f"templatechannel: {res.fetchone()[0]}")
    except Exception as e:
        print(f"Error reading templatechannel: {e}")

    try:
        res = conn.execute(sqlalchemy.text("SELECT enum_range(NULL::templatetype)"))
        print(f"templatetype: {res.fetchone()[0]}")
    except Exception as e:
        print(f"Error reading templatetype: {e}")
