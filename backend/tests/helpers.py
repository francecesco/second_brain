"""Utilità condivise dai test."""
import os

TEST_DB = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://sb:sb@localhost:55432/sb_test"
)
