#!/usr/bin/env bash
# One-off (and rerun-safe) install of the labelling site on the Nectar VM.
# Prerequisite: `sudo certbot --nginx -d 203-101-229-244.sslip.io` has already succeeded.
# Run from the copied similarity_labelling/ directory:  sudo bash deploy/setup_vm.sh
set -euo pipefail
SRC=$(cd "$(dirname "$0")/.." && pwd)

apt-get install -y python3-venv sqlite3 nginx

id labelweb >/dev/null 2>&1 || useradd --system --home-dir /srv/labelweb --shell /usr/sbin/nologin labelweb
# ubuntu joins the group so it can read backups for copying off the VM
usermod -aG labelweb ubuntu

install -d -o labelweb -g labelweb -m 755 /srv/labelweb /srv/labelweb/app /srv/labelweb/images
install -d -o labelweb -g labelweb -m 750 /srv/labelweb/data /srv/labelweb/data/backups
cp -r "$SRC/app.py" "$SRC/templates" "$SRC/requirements.txt" /srv/labelweb/app/
chown -R labelweb:labelweb /srv/labelweb/app

[ -d /srv/labelweb/venv ] || python3 -m venv /srv/labelweb/venv
/srv/labelweb/venv/bin/pip install -q -r /srv/labelweb/app/requirements.txt

if [ ! -f /etc/labelweb.env ]; then
  (umask 077; cat > /etc/labelweb.env <<EOF
LABELWEB_SECRET=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
LABELWEB_DB=/srv/labelweb/data/labels.sqlite3
LABELWEB_IMAGES=/srv/labelweb/images
LABELWEB_X_ACCEL=1
EOF
  )
fi
# Firebase web config of project logo-labeller (public values); edit /etc/labelweb.env to change
for kv in LABELWEB_FIREBASE_API_KEY=AIzaSyDQeNniSscSGT-IqSofEjV_UwTkiZZHGNU \
          LABELWEB_FIREBASE_AUTH_DOMAIN=logo-labeller.firebaseapp.com \
          LABELWEB_FIREBASE_PROJECT_ID=logo-labeller \
          LABELWEB_OPEN_SIGNUP=1; do
  grep -q "^${kv%%=*}=" /etc/labelweb.env || echo "$kv" >> /etc/labelweb.env
done

install -m 755 "$SRC/deploy/labelweb-admin" /usr/local/bin/labelweb-admin
install -m 755 "$SRC/deploy/backup.sh" /usr/local/bin/labelweb-backup
install -m 755 "$SRC/deploy/labelweb-sync" /usr/local/bin/labelweb-sync
echo "17 3 * * * labelweb /usr/local/bin/labelweb-backup" > /etc/cron.d/labelweb-backup

install -m 644 "$SRC/deploy/labelweb.service" /etc/systemd/system/labelweb.service
install -m 644 "$SRC/deploy/nginx-labelweb.conf" /etc/nginx/sites-available/labelweb
ln -sf /etc/nginx/sites-available/labelweb /etc/nginx/sites-enabled/labelweb
rm -f /etc/nginx/sites-enabled/default
nginx -t

labelweb-admin init-db
systemctl daemon-reload
systemctl enable labelweb
systemctl restart labelweb
systemctl reload nginx
echo "done: https://203-101-229-244.sslip.io"
