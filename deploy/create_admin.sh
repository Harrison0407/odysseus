#!/usr/bin/env bash
# Creates the first Django superuser (for /admin/ access) and seeds the
# named pilot users/roles/document taxonomy (spec section 7). Run once
# after the first deploy.sh.
set -euo pipefail
cd "$(dirname "$0")"

echo "==> Creating Django superuser (interactive)"
docker compose -f docker-compose.prod.yml --env-file .env.production exec web \
    python manage.py createsuperuser

echo "==> Seeding organization, departments, roles, document taxonomy, and pilot users"
docker compose -f docker-compose.prod.yml --env-file .env.production exec web \
    python manage.py seed_pilot_data

echo "==> Done. Pilot user passwords were printed once above — store them in your password manager now."
