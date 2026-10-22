#!/usr/bin/env bash
# Nightly consistent copy of the labels database (run by /etc/cron.d/labelweb-backup as labelweb).
# Keeps 30 days. Copy the backups off the VM too: Nectar does not back up instances.
set -euo pipefail
DB=/srv/labelweb/data/labels.sqlite3
OUT=/srv/labelweb/data/backups
sqlite3 "$DB" ".backup '$OUT/labels-$(date +%F).sqlite3'"
find "$OUT" -name 'labels-*.sqlite3' -mtime +30 -delete
