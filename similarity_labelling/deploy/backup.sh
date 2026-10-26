#!/usr/bin/env bash
# Nightly consistent copy of the labels database (run by /etc/cron.d/labelweb-backup as labelweb).
# Keeps 30 days on the VM. Once setup_backup_push.sh has cloned the private backups repo, each
# run also commits a text dump (labels.sql) there and pushes it: Nectar does not back up instances.
set -euo pipefail
# run by hand from a home directory labelweb cannot read, find fails on exit; start somewhere neutral
cd /
DB=/srv/labelweb/data/labels.sqlite3
OUT=/srv/labelweb/data/backups
COPY="$OUT/labels-$(date +%F).sqlite3"
sqlite3 "$DB" ".backup '$COPY'"
find "$OUT" -name 'labels-*.sqlite3' -mtime +30 -delete

REPO=/srv/labelweb/backup-repo
if [ -d "$REPO/.git" ]; then
  # a text dump, so git stores each night as a small delta rather than a whole new file
  sqlite3 "$COPY" .dump > "$REPO/labels.sql"
  cd "$REPO"
  git add labels.sql
  git diff --cached --quiet || git -c user.name=labelweb -c user.email=labelweb@localhost \
    commit -q -m "Backup $(date '+%F %H:%M')"
  GIT_SSH_COMMAND="ssh -i /srv/labelweb/.ssh/backup_key -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new" \
    git push -q origin HEAD:main
fi
