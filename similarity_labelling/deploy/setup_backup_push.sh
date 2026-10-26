#!/usr/bin/env bash
# One-off: let the nightly backup push to a private GitHub repo of its own.
# Run twice on the VM:  sudo bash deploy/setup_backup_push.sh [git@github.com:OWNER/REPO.git]
#   1st run: makes labelweb's key and prints it; add it to the repo as a deploy key WITH write access
#   2nd run: clones the repo into /srv/labelweb/backup-repo and pushes a first backup
set -euo pipefail
URL=${1:-git@github.com:neouyghur/logo-labeller-backups.git}
KEY=/srv/labelweb/.ssh/backup_key
export GIT_SSH_COMMAND="ssh -i $KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
cd /

install -d -o labelweb -g labelweb -m 700 /srv/labelweb/.ssh
[ -f "$KEY" ] || sudo -u labelweb ssh-keygen -q -t ed25519 -N "" -C labelweb-backup@logo-label -f "$KEY"

if ! sudo -u labelweb --preserve-env=GIT_SSH_COMMAND git ls-remote "$URL" >/dev/null 2>&1; then
  echo "On GitHub, open $URL (create it as a PRIVATE repo if needed) ->"
  echo "Settings -> Deploy keys -> Add deploy key, tick 'Allow write access', and paste:"
  echo
  cat "$KEY.pub"
  echo
  echo "Then run this script again."
  exit 1
fi

if [ ! -d /srv/labelweb/backup-repo/.git ]; then
  sudo -u labelweb --preserve-env=GIT_SSH_COMMAND git clone -q "$URL" /srv/labelweb/backup-repo 2>/dev/null
fi
sudo -u labelweb /usr/local/bin/labelweb-backup
echo "backup pushed to $URL; the 03:17 cron job pushes every night from now on"
