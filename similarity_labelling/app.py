"""Pairwise logo-similarity labelling site.

One pair per page; the answer is yes / no / unsure. Every pair should get LABELWEB_TARGET
answers (default 3) from different annotators: each annotator is served a pair they have not
answered, fewest answers first, then in a fixed random order (pairs.shuffle). Once every pair
has reached the target, annotators see the done page; any number of annotators can join.
Answers can be changed by going back.
Sign-in is Firebase Authentication (Google). With LABELWEB_OPEN_SIGNUP=1 any verified Google
account gets a session on first sign-in; otherwise only emails added with allow-email do.
Behind nginx, images are handed off with X-Accel-Redirect so they stay behind the login.

Admin commands (run with `flask --app app <command>`, or `sudo labelweb-admin <command>` on the VM):
  init-db               create the tables
  load-pairs CSV        add pairs from a CSV with header pair_key,img_a,img_b (rerun-safe)
  allow-email EMAIL...  let these Google accounts sign in
  remove-email EMAIL... revoke access at the next request; their answers are kept
  list-users            emails, access, answer counts
  progress              how many pairs have reached the target number of answers
  export OUT_DIR        answers.tsv, annotators.tsv, pairs_summary.tsv, annotator_emails.tsv

Attention checks are pairs with is_check = 1: a query shown with another query's positive, so
the right answer is "no". They are served like any pair but count towards no progress figure.
"""
import csv
import mimetypes
import os
import secrets
import sqlite3
import sys
from functools import wraps

import click
import google.auth.exceptions
from flask import (Flask, abort, g, redirect, render_template, request,
                   send_from_directory, session, url_for)
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from werkzeug.security import safe_join

LABELS = ("yes", "no", "unsure")

SCHEMA = """
CREATE TABLE IF NOT EXISTS pairs (
    id       INTEGER PRIMARY KEY,
    pair_key TEXT UNIQUE NOT NULL,
    img_a    TEXT NOT NULL,
    img_b    TEXT NOT NULL,
    shuffle  INTEGER NOT NULL DEFAULT (abs(random())),
    is_check INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS users (
    id     INTEGER PRIMARY KEY,
    email  TEXT UNIQUE NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS labels (
    user_id    INTEGER NOT NULL REFERENCES users(id),
    pair_id    INTEGER NOT NULL REFERENCES pairs(id),
    label      TEXT NOT NULL CHECK (label IN ('yes', 'no', 'unsure')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, pair_id)
);
-- the gallery audit sheets (audit.py)
CREATE TABLE IF NOT EXISTS audit_access (
    user_id    INTEGER PRIMARY KEY REFERENCES users(id),
    granted_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS audit_state (
    user_id    INTEGER NOT NULL REFERENCES users(id),
    sheet      TEXT NOT NULL,
    state      TEXT NOT NULL,
    rows       TEXT NOT NULL,
    updated_ms INTEGER NOT NULL,
    PRIMARY KEY (user_id, sheet)
);
"""

use_x_accel = os.environ.get("LABELWEB_X_ACCEL", "0") == "1"
secret = os.environ.get("LABELWEB_SECRET")
if use_x_accel and not secret:
    # several gunicorn workers must share one key, or sessions break between requests
    sys.exit("LABELWEB_SECRET must be set in production")

app = Flask(__name__)
app.config.update(
    SECRET_KEY=secret or secrets.token_hex(32),
    DB_PATH=os.environ.get("LABELWEB_DB", "labels.sqlite3"),
    IMAGE_ROOT=os.environ.get("LABELWEB_IMAGES", "images"),
    USE_X_ACCEL=use_x_accel,
    TARGET=int(os.environ.get("LABELWEB_TARGET", "3")),
    # 1: any verified Google account may sign in; 0: only emails added with allow-email
    OPEN_SIGNUP=os.environ.get("LABELWEB_OPEN_SIGNUP", "0") == "1",
    # gallery audit sheets: one directory per sheet; empty passcode closes the audit
    AUDIT_ROOT=os.environ.get("LABELWEB_AUDIT", "audit"),
    AUDIT_PASSCODE=os.environ.get("LABELWEB_AUDIT_PASSCODE", ""),
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=use_x_accel,
    # the Firebase web config; these values are public by design
    FIREBASE={
        "apiKey": os.environ.get("LABELWEB_FIREBASE_API_KEY", ""),
        "authDomain": os.environ.get("LABELWEB_FIREBASE_AUTH_DOMAIN", ""),
        "projectId": os.environ.get("LABELWEB_FIREBASE_PROJECT_ID", ""),
    },
)


def firebase_configured():
    return all(v and v != "CHANGE_ME" for v in app.config["FIREBASE"].values())


def db():
    if "db" not in g:
        g.db = sqlite3.connect(app.config["DB_PATH"], timeout=10)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        uid = session.get("user_id")
        # re-checked every request so remove-email takes effect without waiting for the cookie to expire
        if uid is None or db().execute("SELECT 1 FROM users WHERE id = ? AND active = 1",
                                       (uid,)).fetchone() is None:
            session.clear()
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def safe_next(path):
    """A local path to return to after sign-in, or the labelling page."""
    return path if path and path.startswith("/") and not path.startswith("//") else url_for("index")


def progress():
    done = db().execute("SELECT COUNT(*) FROM labels WHERE user_id = ?",
                        (session["user_id"],)).fetchone()[0]
    total = db().execute("SELECT COUNT(*) FROM pairs WHERE is_check = 0").fetchone()[0]
    # answers that count towards the target; extra answers beyond it, and attention checks, are not
    counted = db().execute(
        """SELECT COALESCE(SUM(MIN(n, ?)), 0) FROM
           (SELECT COUNT(*) AS n FROM labels l JOIN pairs p ON p.id = l.pair_id
            WHERE p.is_check = 0 GROUP BY l.pair_id)""",
        (app.config["TARGET"],)).fetchone()[0]
    return {"done": done, "total": total, "counted": counted,
            "needed": total * app.config["TARGET"]}


def next_pair(skip_id=None):
    """The pair this annotator should see next, or None when there is nothing left for them.

    Within the fewest-answers level, a pair whose query (img_a) differs from the one this
    annotator saw last comes first, so the same query is not shown twice in a row.
    """
    last = db().execute(
        """SELECT p.img_a FROM labels l JOIN pairs p ON p.id = l.pair_id
           WHERE l.user_id = ? ORDER BY l.rowid DESC LIMIT 1""",
        (session["user_id"],)).fetchone()
    return db().execute(
        """SELECT p.* FROM pairs p LEFT JOIN labels l ON l.pair_id = p.id
           WHERE p.id IS NOT ?
           GROUP BY p.id
           HAVING COUNT(l.user_id) < ? AND COALESCE(SUM(l.user_id = ?), 0) = 0
           ORDER BY COUNT(l.user_id), p.img_a IS ?, p.shuffle LIMIT 1""",
        (skip_id, app.config["TARGET"], session["user_id"],
         last["img_a"] if last else None)).fetchone()


@app.route("/login")
def login():
    return render_template("login.html", firebase=app.config["FIREBASE"],
                           configured=firebase_configured(),
                           next=safe_next(request.args.get("next")))


@app.route("/session", methods=["POST"])
def session_login():
    token = (request.get_json(silent=True) or {}).get("idToken")
    if not token or not firebase_configured():
        return {"error": "Sign-in is not available."}, 400
    project_id = app.config["FIREBASE"]["projectId"]
    try:
        claims = id_token.verify_firebase_token(token, google_requests.Request(), audience=project_id)
    except (ValueError, google.auth.exceptions.GoogleAuthError):
        return {"error": "Sign-in could not be verified. Try again."}, 401
    if (claims.get("iss") != f"https://securetoken.google.com/{project_id}"
            or not claims.get("email_verified")):
        return {"error": "Sign-in could not be verified. Try again."}, 401

    email = claims.get("email", "").lower()
    if app.config["OPEN_SIGNUP"]:
        # an existing row is left alone, so remove-email (active = 0) still blocks that person
        db().execute("INSERT OR IGNORE INTO users (email) VALUES (?)", (email,))
        db().commit()
    row = db().execute("SELECT id FROM users WHERE email = ? AND active = 1", (email,)).fetchone()
    if row is None:
        return {"error": f"{email} is not on the annotator list. Ask the project owner to add it."}, 403
    session.clear()
    session["user_id"] = row["id"]
    session["name"] = email
    session.permanent = True
    return {"ok": True}


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    row = next_pair()
    if row is None:
        return render_template("done.html", **progress())
    return redirect(url_for("pair", pair_id=row["id"]))


@app.route("/pair/<int:pair_id>")
@login_required
def pair(pair_id):
    p = db().execute("SELECT * FROM pairs WHERE id = ?", (pair_id,)).fetchone()
    if p is None:
        abort(404)
    current = db().execute("SELECT rowid, label FROM labels WHERE user_id = ? AND pair_id = ?",
                           (session["user_id"], pair_id)).fetchone()
    # "previous" walks back through this annotator's own answers, in the order first given
    # (an upsert keeps the rowid, so changing an answer does not reorder them)
    prev = db().execute(
        "SELECT pair_id FROM labels WHERE user_id = ? AND rowid < ? ORDER BY rowid DESC LIMIT 1",
        (session["user_id"], current["rowid"] if current else sys.maxsize)).fetchone()
    return render_template("pair.html", pair=p, current=current["label"] if current else None,
                           prev_id=prev["pair_id"] if prev else None,
                           prefetch=next_pair(skip_id=pair_id), **progress())


@app.route("/pair/<int:pair_id>", methods=["POST"])
@login_required
def label(pair_id):
    value = request.form.get("label")
    if value not in LABELS:
        abort(400)
    if db().execute("SELECT 1 FROM pairs WHERE id = ?", (pair_id,)).fetchone() is None:
        abort(404)
    db().execute(
        """INSERT INTO labels (user_id, pair_id, label) VALUES (?, ?, ?)
           ON CONFLICT (user_id, pair_id)
           DO UPDATE SET label = excluded.label, updated_at = datetime('now')""",
        (session["user_id"], pair_id, value))
    db().commit()
    return redirect(url_for("index"))


@app.route("/img/<path:relpath>")
@login_required
def image(relpath):
    if safe_join(app.config["IMAGE_ROOT"], relpath) is None:
        abort(404)
    if app.config["USE_X_ACCEL"]:
        resp = app.response_class()
        resp.headers["X-Accel-Redirect"] = "/protected-images/" + relpath
        resp.mimetype = mimetypes.guess_type(relpath)[0] or "application/octet-stream"
        return resp
    return send_from_directory(app.config["IMAGE_ROOT"], relpath)


@app.cli.command("init-db")
def init_db():
    db().executescript(SCHEMA)
    columns = [r["name"] for r in db().execute("PRAGMA table_info(pairs)")]
    if "shuffle" not in columns:
        # databases created before the target-based assignment; random() is per row here
        db().execute("ALTER TABLE pairs ADD COLUMN shuffle INTEGER NOT NULL DEFAULT 0")
        db().execute("UPDATE pairs SET shuffle = abs(random())")
        db().commit()
    if "is_check" not in columns:
        db().execute("ALTER TABLE pairs ADD COLUMN is_check INTEGER NOT NULL DEFAULT 0")
        db().commit()
    db().execute("PRAGMA journal_mode = WAL")
    click.echo(f"initialised {app.config['DB_PATH']}")


@app.cli.command("load-pairs")
@click.argument("csv_path")
def load_pairs(csv_path):
    with open(csv_path, newline="") as f:
        rows = [(r["pair_key"], r["img_a"], r["img_b"], int(r.get("is_check") or 0))
                for r in csv.DictReader(f)]
    before = db().execute("SELECT COUNT(*) FROM pairs").fetchone()[0]
    db().executemany(
        "INSERT OR IGNORE INTO pairs (pair_key, img_a, img_b, is_check) VALUES (?, ?, ?, ?)", rows)
    db().commit()
    after = db().execute("SELECT COUNT(*) FROM pairs").fetchone()[0]
    click.echo(f"{len(rows)} rows read, {after - before} new pairs, {after} total")


@app.cli.command("allow-email")
@click.argument("emails", nargs=-1, required=True)
def allow_email(emails):
    for email in emails:
        db().execute("INSERT INTO users (email) VALUES (?) ON CONFLICT (email) DO UPDATE SET active = 1",
                     (email.strip().lower(),))
    db().commit()
    click.echo(f"allowed: {', '.join(e.strip().lower() for e in emails)}")


@app.cli.command("remove-email")
@click.argument("emails", nargs=-1, required=True)
def remove_email(emails):
    for email in emails:
        n = db().execute("UPDATE users SET active = 0 WHERE email = ?", (email.strip().lower(),)).rowcount
        click.echo(f"{email}: {'removed' if n else 'not found'}")
    db().commit()


@app.cli.command("list-users")
def list_users():
    rows = db().execute(
        """SELECT u.email, u.active, COUNT(l.pair_id) AS answers
           FROM users u LEFT JOIN labels l ON l.user_id = u.id
           GROUP BY u.id ORDER BY u.email""").fetchall()
    for r in rows:
        click.echo(f"{r['email']}\t{'active' if r['active'] else 'removed'}\t{r['answers']}")


@app.cli.command("progress")
def progress_cmd():
    target = app.config["TARGET"]
    rows = db().execute(
        """SELECT n, COUNT(*) AS pairs FROM
           (SELECT COUNT(l.user_id) AS n FROM pairs p LEFT JOIN labels l ON l.pair_id = p.id
            WHERE p.is_check = 0 GROUP BY p.id)
           GROUP BY n ORDER BY n""").fetchall()
    total = sum(r["pairs"] for r in rows)
    complete = sum(r["pairs"] for r in rows if r["n"] >= target)
    click.echo(f"{complete} / {total} pairs have >= {target} answers")
    for r in rows:
        click.echo(f"  {r['n']} answers: {r['pairs']} pairs")


def write_tsv(path, header, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(header)
        w.writerows(rows)


@app.cli.command("export")
@click.argument("out_dir")
@click.option("--min-check-accuracy", default=0.8, show_default=True,
              help="annotators below this attention-check accuracy are left out of pairs_summary")
def export(out_dir, min_check_accuracy):
    """Write answers, annotators, pairs_summary and annotator_emails TSVs into OUT_DIR."""
    os.makedirs(out_dir, exist_ok=True)
    users = db().execute("SELECT id, email FROM users ORDER BY id").fetchall()
    # anonymous ids in sign-up order; the email mapping goes to its own file
    anon = {u["id"]: f"A{i:02d}" for i, u in enumerate(users, 1)}
    answers = db().execute(
        """SELECT p.id AS pair_id, p.pair_key, p.img_a, p.img_b, p.is_check,
                  l.user_id, l.label, l.updated_at
           FROM labels l JOIN pairs p ON p.id = l.pair_id
           ORDER BY p.id, l.user_id""").fetchall()

    def query_of(p):
        return os.path.basename(p["img_a"])

    def positive_of(p):
        return "/".join(p["img_b"].split("/")[-2:])

    stats = {u["id"]: {"n": 0, "checks": 0, "correct": 0} for u in users}
    for a in answers:
        s = stats[a["user_id"]]
        s["n"] += 1
        if a["is_check"]:
            s["checks"] += 1
            s["correct"] += a["label"] == "no"
    trusted = {}
    for uid, s in stats.items():
        acc = s["correct"] / s["checks"] if s["checks"] else None
        # no checks answered yet: trusted until there is evidence otherwise
        trusted[uid] = acc is None or acc >= min_check_accuracy
        s["acc"] = acc

    write_tsv(os.path.join(out_dir, "answers.tsv"),
              ["pair_key", "query", "positive", "is_check", "annotator", "label", "answered_at"],
              [(a["pair_key"], query_of(a), positive_of(a), a["is_check"], anon[a["user_id"]],
                a["label"], a["updated_at"]) for a in answers])
    write_tsv(os.path.join(out_dir, "annotators.tsv"),
              ["annotator", "n_answers", "n_checks", "check_correct", "check_accuracy", "trusted"],
              [(anon[uid], s["n"], s["checks"], s["correct"],
                "" if s["acc"] is None else f"{s['acc']:.2f}", "yes" if trusted[uid] else "no")
               for uid, s in stats.items()])
    write_tsv(os.path.join(out_dir, "annotator_emails.tsv"), ["annotator", "email"],
              [(anon[u["id"]], u["email"]) for u in users])

    counts = {}
    for a in answers:
        if not a["is_check"] and trusted[a["user_id"]]:
            counts.setdefault(a["pair_id"], {"yes": 0, "no": 0, "unsure": 0})[a["label"]] += 1
    summary = []
    for p in db().execute("SELECT * FROM pairs WHERE is_check = 0 ORDER BY pair_key"):
        c = counts.get(p["id"], {"yes": 0, "no": 0, "unsure": 0})
        n = sum(c.values())
        top = max(c.values())
        winners = [k for k in LABELS if c[k] == top]
        majority = "" if n == 0 else winners[0] if len(winners) == 1 else "tie"
        summary.append((p["pair_key"], query_of(p), positive_of(p), n, c["yes"], c["no"],
                        c["unsure"], majority, f"{top / n:.2f}" if n else ""))
    write_tsv(os.path.join(out_dir, "pairs_summary.tsv"),
              ["pair_key", "query", "positive", "n_answers", "n_yes", "n_no", "n_unsure",
               "majority", "agreement"], summary)
    click.echo(f"{len(answers)} answers from {len(users)} annotators "
               f"({sum(not t for t in trusted.values())} untrusted) -> {out_dir}/")


import audit  # noqa: E402,F401  registers the /audit pages and audit-export
