#!/bin/sh
# Fly mounts volumes root-owned, so /data is not writable by the unprivileged app
# user until we fix it. Do that as root, then drop privileges before exec'ing the
# server. If the container is already running unprivileged, just run.
set -e

if [ "$(id -u)" = "0" ]; then
    mkdir -p /data
    chown -R app:app /data
    exec su app -s /bin/sh -c "exec $*"
fi

exec "$@"
