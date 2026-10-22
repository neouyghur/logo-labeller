# similarity_labelling

Website where annotators judge whether two logos are similar: **yes / no / unsure**.
Flask + SQLite behind nginx on the Nectar VM `logo-label` (203.101.229.244),
served at https://203-101-229-244.sslip.io.

Every annotator sees every pair, in the order of the loaded CSV (shuffle the CSV beforehand
if order matters). Keyboard: `Y`/`N`/`U` or `1`/`2`/`3`, `B` goes back. Images are only
served to logged-in users (nginx `internal` location + `X-Accel-Redirect`).

Sign-in is **Firebase Authentication with Google**. `LABELWEB_OPEN_SIGNUP=1` in
`/etc/labelweb.env` (the default from `setup_vm.sh`) lets any verified Google account in and
registers it on first sign-in; set it to `0` to admit only emails added with `allow-email`. The browser signs in with Firebase,
sends the ID token to `/session`, and the server verifies it (`google-auth`, against
Google's public keys) and starts its own Flask session.

## 0. Firebase project (once, in the Firebase console)

1. Create a project; **Authentication → Sign-in method → Google → Enable**.
2. **Authentication → Settings → Authorized domains → Add** `203-101-229-244.sslip.io`.
3. **Project settings → Your apps → Web app (`</>`)** — `apiKey`, `authDomain`, `projectId`.
   Done: project `logo-labeller`; `setup_vm.sh` writes its values into `/etc/labelweb.env`.
   These are public values; no service-account key is needed.

## 1. Pack the images (on Aqua, as a PBS job)

Input: a CSV with header `pair_key,img_a,img_b`; image paths as on Aqua.

```bash
mkdir -p /work/SAIVT/LOGO/logs/labelling
qsub -v PAIRS=/path/to/pairs.csv pack_images.pbs
```

Output in `/work/SAIVT/LOGO/labelling/pack/`: `images.tar`, `pairs.csv` (relative paths;
pairs with a missing image dropped), `missing.txt`.

## 2. Copy to the VM (from your laptop, or wherever your VM key is)

```bash
scp -r similarity_labelling ubuntu@203.101.229.244:
scp aqua:/work/SAIVT/LOGO/labelling/pack/{images.tar,pairs.csv} ubuntu@203.101.229.244:
```

## 3. Install (on the VM, once certbot has issued the certificate)

```bash
cd ~/similarity_labelling
sudo bash deploy/setup_vm.sh
sudo tar -xf ~/images.tar -C /srv/labelweb/images --no-same-owner --no-same-permissions
sudo chown -R labelweb:labelweb /srv/labelweb/images
sudo chmod -R a+rX /srv/labelweb/images     # nginx (www-data) must read them
sudo cp ~/pairs.csv /srv/labelweb/data/ && sudo chown labelweb /srv/labelweb/data/pairs.csv
sudo labelweb-admin load-pairs /srv/labelweb/data/pairs.csv
```

`setup_vm.sh` is rerun-safe: run it again after changing the code. It keeps `/etc/labelweb.env`
(the session secret, Firebase values) and the database.

## Annotators

```bash
sudo labelweb-admin allow-email alice@gmail.com bob@qut.edu.au
sudo labelweb-admin remove-email bob@qut.edu.au   # locked out at their next click; answers kept
sudo labelweb-admin list-users                    # email, active/removed, answer count
```

Send annotators the URL; they click **Sign in with Google**. With open sign-up anyone who
has the URL can label: check `list-users` and block strangers with `remove-email`
(a removed email stays blocked; its answers stay in the database and in `export`).
Any address that is a Google account works, including a QUT address registered as one.

## Results and backups

```bash
sudo labelweb-admin export /srv/labelweb/data/labels.tsv
```

Columns: `pair_key, img_a, img_b, annotator, label, updated_at` — one row per answer.

A cron job writes `/srv/labelweb/data/backups/labels-YYYY-MM-DD.sqlite3` at 03:17 daily,
keeping 30 days. **Nectar does not back up the VM** and deletes it 3 months after the
allocation ends, so copy backups off it regularly:

```bash
rsync -av ubuntu@203.101.229.244:/srv/labelweb/data/backups/ /work/SAIVT/LOGO/labelling/backups/
```

## Files

| File | Role |
|---|---|
| `app.py` | the site, plus admin commands `init-db`, `load-pairs`, `allow-email`, `remove-email`, `list-users`, `export` |
| `templates/` | login, pair, done pages |
| `pack_images.py`, `pack_images.pbs` | Aqua side: collect the pair images into one tar |
| `deploy/setup_vm.sh` | VM install: user, venv, env file, systemd, nginx, cron |
| `deploy/labelweb-admin` | runs an admin command as `labelweb` with the env loaded |
| `deploy/nginx-labelweb.conf`, `deploy/labelweb.service`, `deploy/backup.sh` | installed by `setup_vm.sh` |
