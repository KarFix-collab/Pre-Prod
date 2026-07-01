#!/usr/bin/env bash
set -euo pipefail

echo "==> Running database migrations..."
python -m alembic upgrade head

echo "==> Database migrations complete."
echo "==> Starting application..."
exec gunicorn wsgi:application --bind 0.0.0.0:${PORT:-10000} --workers 2 --timeout 120
