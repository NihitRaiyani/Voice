#!/bin/sh
set -eu
umask 077
password="$(cat /run/secrets/redis_password)"
printf 'appendonly yes\nrequirepass %s\ndir /data\nbind 0.0.0.0\nprotected-mode yes\n' "$password" > /tmp/redis.conf
exec redis-server /tmp/redis.conf
