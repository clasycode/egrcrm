#!/usr/bin/env sh
set -eu
# Run from repository root, before replacing a deployed version.
web_id=$(docker compose ps -q web)
if [ -n "$web_id" ]; then
  docker compose exec -T web python manage.py backup_crm
fi
docker compose up -d --build
