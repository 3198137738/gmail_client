#!/bin/sh
set -e
# Bind-mounted dirs are often root-owned on the host; app user (uid 1000) must be able to write.
if [ "$(id -u)" = "0" ]; then
    chown -R 1000:1000 /app/data /app/attachments /app/secrets /app/logs || true
    exec gosu 1000:1000 /docker-entrypoint.sh "$@"
fi
exec "$@"
