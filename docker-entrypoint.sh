#!/bin/sh
# Align the container's runtime user with the ownership of the mounted data
# directory, then drop privileges.
#
# Why this exists: Unraid bind-mounts appdata and expects files owned by
# nobody:users (99:100). A fixed uid baked into the image would write files the
# host can't manage -- they'd show as an unknown owner and break CA Backup and
# SMB access. PUID/PGID is the convention Unraid users already expect.
#
# Plain Docker users can ignore it entirely; the defaults match the image.
set -e

PUID="${PUID:-10001}"
PGID="${PGID:-10001}"
DATA_DIR="${RP_DATA_DIR:-/data}"

if [ "$(id -u)" = "0" ]; then
    current_uid="$(id -u appuser)"
    current_gid="$(id -g appuser)"

    if [ "$current_gid" != "$PGID" ]; then
        groupmod -o -g "$PGID" appuser
    fi
    if [ "$current_uid" != "$PUID" ]; then
        usermod -o -u "$PUID" appuser
    fi

    mkdir -p "$DATA_DIR"
    # Only chown when it's actually wrong: recursive chown on a large appdata
    # dir every boot is slow, and pointless when ownership already matches.
    if [ "$(stat -c '%u:%g' "$DATA_DIR")" != "$PUID:$PGID" ]; then
        echo "[entrypoint] setting ownership of $DATA_DIR to $PUID:$PGID"
        chown -R appuser:appuser "$DATA_DIR"
    fi

    echo "[entrypoint] starting as uid=$PUID gid=$PGID"
    exec gosu appuser "$@"
fi

# Already unprivileged (e.g. compose set `user:`) -- nothing to adjust.
echo "[entrypoint] already running as uid=$(id -u); starting"
exec "$@"
