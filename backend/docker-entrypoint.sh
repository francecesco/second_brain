#!/bin/sh
# Migrazioni a ogni avvio (idempotenti), poi il servizio.
set -e
alembic -c /app/alembic.ini upgrade head
exec uvicorn --factory secondbrain.app:create_app_from_env --host 0.0.0.0 --port 8000
