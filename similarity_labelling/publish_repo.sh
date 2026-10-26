#!/bin/bash
# Copy this code, and optionally one packed pair set, into the logo-labeller repo and push it.
# The VM then deploys it with `sudo labelweb-sync`.
#   Code only:          bash publish_repo.sh
#   Code + a pair set:  bash publish_repo.sh /work/SAIVT/LOGO/labelling/pack
# A pair set (images.tar + pairs.csv from pack_images.pbs) replaces data/ in the repo.
# Audit sheets: copy a sheet directory into $REPO/audit/<sheet>/ before running this.
# Pushes over ssh.github.com:443 via the `github-labeller` alias in ~/.ssh/config.
set -eo pipefail
REPO=${REPO:-/work/SAIVT/LOGO/labelling/logo-labeller}
SRC=$(cd "$(dirname "$0")" && pwd)
PACK=${1:-}

# exports hold annotator emails and answers: never copy them into the repo
rsync -a --delete --exclude __pycache__ --exclude 'export_*' --exclude 'audit_export_*' \
  "$SRC/" "$REPO/similarity_labelling/"
if [ -n "$PACK" ]; then
  rm -rf "$REPO/data"
  mkdir -p "$REPO/data/images"
  tar -xf "$PACK/images.tar" -C "$REPO/data/images"
  cp "$PACK/pairs.csv" "$REPO/data/pairs.csv"
fi

cd "$REPO"
git add -A similarity_labelling
[ -d data ] && git add -A data
[ -d audit ] && git add -A audit
if git diff --cached --quiet; then
  echo "nothing to publish"
else
  git commit -q -m "Publish labelling code${PACK:+ and pairs from $PACK}"
fi
if [ "${PUSH:-1}" = 1 ]; then
  git push -q origin main
  echo "pushed $(git log -1 --format='%h %s')"
else
  # compute nodes have no route to GitHub: commit in a job, push from the login node
  echo "committed $(git log -1 --format='%h %s'); push with: git -C $REPO push origin main"
fi
