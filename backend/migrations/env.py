"""Ambiente Alembic: URL da sqlalchemy.url (test) oppure da DATABASE_URL (servizio)."""
import os

from alembic import context
from sqlalchemy import create_engine, pool

url = context.config.get_main_option("sqlalchemy.url") or os.environ["DATABASE_URL"]
engine = create_engine(url, poolclass=pool.NullPool)
with engine.connect() as connection:
    context.configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()
