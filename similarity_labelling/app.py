"""Pairwise logo-similarity labelling site.

Each annotator sees every pair once, in pair id order (the order of the loaded CSV),
and answers yes / no / unsure. Answers can be changed by going back.
Sign-in is Firebase Authentication (Google). With LABELWEB_OPEN_SIGNUP=1 any verified Google
account gets a session on first sign-in; otherwise only emails added with allow-email do.
Behind nginx, images are handed off with X-Accel-Redirect so they stay behind the login.

Admin commands (run with `flask --app app <command>`, or `sudo labelweb-admin <command>` on the VM):
  init-db               create the tables
  load-pairs CSV        add pairs from a CSV with header pair_key,img_a,img_b (rerun-safe)
  allow-email EMAIL...  let these Google accounts sign in
  remove-email EMAIL... revoke access at the next request; their answers are kept
  list-users            emails, access, answer counts
  export OUT.tsv        one row per (pair, annotator) answer
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
    img_b    TEXT NOT NULL
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
    # 1: any verified Google account may sign in; 0: only emails added with allow-email
    OPEN_SIGNUP=os.environ.get("LABELWEB_OPEN_SIGNUP", "0") == "1",
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
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def progress():
    done = db().execute("SELECT COUNT(*) FROM labels WHERE user_id = ?",
                        (session["user_id"],)).fetchone()[0]
    total = db().execute("SELECT COUNT(*) FROM pairs").fetchone()[0]
    return {"done": done, "total": total}


def next_unlabelled(after_id=0):
    return db().execute(
        """SELECT p.* FROM pairs p
           LEFT JOIN labels l ON l.pair_id = p.id AND l.user_id = ?
           WHERE l.pair_id IS NULL AND p.id > ?
           ORDER BY p.id LIMIT 1""",
        (session["user_id"], after_id)).fetchone()


@app.route("/login")
def login():
    return render_template("login.html", firebase=app.config["FIREBASE"],
                           configured=firebase_configured())


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
    row = next_unlabelled()
    if row is None:
        return render_template("done.html", **progress())
    return redirect(url_for("pair", pair_id=row["id"]))


@app.route("/pair/<int:pair_id>")
@login_required
def pair(pair_id):
    p = db().execute("SELECT * FROM pairs WHERE id = ?", (pair_id,)).fetchone()
    if p is None:
        abort(404)
    current = db().execute("SELECT label FROM labels WHERE user_id = ? AND pair_id = ?",
                           (session["user_id"], pair_id)).fetchone()
    prev_id = db().execute("SELECT MAX(id) FROM pairs WHERE id < ?", (pair_id,)).fetchone()[0]
    return render_template("pair.html", pair=p, current=current["label"] if current else None,
                           prev_id=prev_id, prefetch=next_unlabelled(pair_id), **progress())


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
    db().execute("PRAGMA journal_mode = WAL")
    click.echo(f"initialised {app.config['DB_PATH']}")


@app.cli.command("load-pairs")
@click.argument("csv_path")
def load_pairs(csv_path):
    with open(csv_path, newline="") as f:
        rows = [(r["pair_key"], r["img_a"], r["img_b"]) for r in csv.DictReader(f)]
    before = db().execute("SELECT COUNT(*) FROM pairs").fetchone()[0]
    db().executemany("INSERT OR IGNORE INTO pairs (pair_key, img_a, img_b) VALUES (?, ?, ?)", rows)
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


@app.cli.command("export")
@click.argument("out_path")
def export(out_path):
    rows = db().execute(
        """SELECT p.pair_key, p.img_a, p.img_b, u.email, l.label, l.updated_at
           FROM labels l JOIN pairs p ON p.id = l.pair_id JOIN users u ON u.id = l.user_id
           ORDER BY p.id, u.email""").fetchall()
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["pair_key", "img_a", "img_b", "annotator", "label", "updated_at"])
        w.writerows(tuple(r) for r in rows)
    click.echo(f"{len(rows)} answers -> {out_path}")
