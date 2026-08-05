#!/bin/bash
set -e

# Auto-generate SECRET_KEY if not provided via environment
# The key is persisted to the data volume so it survives container rebuilds
if [ -z "$SECRET_KEY" ]; then
    KEY_FILE="/app/data/.secret_key"
    if [ ! -f "$KEY_FILE" ]; then
        echo "Generating new SECRET_KEY..."
        python -c "import secrets; print(secrets.token_urlsafe(50))" > "$KEY_FILE"
        chmod 600 "$KEY_FILE"
    fi
    export SECRET_KEY=$(cat "$KEY_FILE")
fi

echo "Running migrations..."
python manage.py migrate --noinput

echo "Collecting static files..."
python manage.py collectstatic --noinput

# Create superuser if environment variables are set and user doesn't exist
python manage.py ensure_superuser

# Start scheduled tasks (heartbeat, backups, etc.) via supercronic
# supercronic runs as non-root and inherits the current environment
echo "Starting scheduled tasks..."
supercronic -passthrough-logs /app/scripts/smallstack-cron &

# Run db_worker inline by default (single-container deployment)
# Set WORKER_INLINE=false to disable when using a separate worker container.
if [ "${WORKER_INLINE:-true}" = "true" ]; then
    echo "Starting inline db_worker..."
    (
        while true; do
            python manage.py db_worker --queue-name "*"
            echo "Inline worker exited (code $?), restarting in 5s..."
            sleep 5
        done
    ) &
    WORKER_PID=$!
    trap "kill $WORKER_PID 2>/dev/null; wait $WORKER_PID 2>/dev/null" EXIT
fi

echo "Starting application..."
# Execute the main container command
exec "$@"
