"""Pairwise logo-similarity labelling site.

One pair per page; the answer is yes / no / unsure. Every pair should get LABELWEB_TARGET
answers (default 3) from different annotators: each annotator is served a pair they have not
answered, fewest answers first, then in a fixed random order (pairs.shuffle). Once every pair
has reached the target, annotators see the done page; any number of annotators can join.
Answers can be changed by going back.

Each annotator does one batch (LABELWEB_BATCH pages, default 30; LABELWEB_MAX_BATCHES, default
1, allows more). LABELWEB_CHECKS of its pages (default 9) are attention checks at positions
fixed per annotator. At the end of the first batch the annotator gets a printable certificate
with a code (LS-XXXX-XXXX) if at least LABELWEB_CERT_MIN (0.8) of their checks were answered
"no"; anyone can check a code at /verify, and passcode holders can mark its reward given.
Sign-in is Firebase Authentication (Google). With LABELWEB_OPEN_SIGNUP=1 any verified Google
account gets a session on first sign-in; otherwise only emails added with allow-email do.
Behind nginx, images are handed off with X-Accel-Redirect so they stay behind the login.

Admin commands (run with `flask --app app <command>`, or `sudo labelweb-admin <command>` on the VM):
  init-db               create the tables
  load-pairs CSV        add pairs from a CSV with header pair_key,img_a,img_b (rerun-safe)
  allow-email EMAIL...  let these accounts sign in
  remove-email EMAIL... revoke access at the next request; their answers are kept
  list-users            emails, access, answer counts
  progress              how many pairs have reached the target number of answers
  export OUT_DIR        answers.tsv, annotators.tsv, pairs_summary.tsv, participants.tsv,
                        annotator_emails.tsv

Attention checks are pairs with is_check = 1: a query shown with another query's positive, so
the right answer is "no". They take the batch's check positions, are drawn in a per-annotator
random order with no answer limit (the pool is reused by everyone), and count towards no
progress figure.
"""
import csv
import mimetypes
import os
import random
import re
import secrets
import sqlite3
import sys
import time
from functools import wraps

import click
import segno
import google.auth.exceptions
from flask import (Flask, abort, g, redirect, render_template, request,
                   send_from_directory, session, url_for)
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import safe_join

LABELS = ("yes", "no", "unsure")
# the background questions: stored value -> wording; "" is "Prefer not to say"
AGE_RANGES = {"18-24": "18–24", "25-34": "25–34", "35-44": "35–44", "45-54": "45–54",
              "55-64": "55–64", "65+": "65 or over"}
DESIGN = {"none": "None",
          "hobby": "Hobby or self-taught",
          "studied": "Studied design (graphic, visual, branding…)",
          "professional": "Work professionally in design, branding or marketing"}

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
    id      INTEGER PRIMARY KEY,
    email   TEXT UNIQUE NOT NULL,
    active  INTEGER NOT NULL DEFAULT 1,
    batches INTEGER NOT NULL DEFAULT 1,
    name    TEXT NOT NULL DEFAULT '',
    guided  TEXT  -- when they finished the guide slides; NULL: the slides open over the first pair
);
CREATE TABLE IF NOT EXISTS labels (
    user_id    INTEGER NOT NULL REFERENCES users(id),
    pair_id    INTEGER NOT NULL REFERENCES pairs(id),
    label      TEXT NOT NULL CHECK (label IN ('yes', 'no', 'unsure')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, pair_id)
);
-- decided once, at the end of an annotator's first batch
CREATE TABLE IF NOT EXISTS certificates (
    user_id     INTEGER PRIMARY KEY REFERENCES users(id),
    code        TEXT UNIQUE,  -- NULL: too few attention checks answered "no"
    checks      INTEGER NOT NULL,
    correct     INTEGER NOT NULL,
    issued_at   TEXT NOT NULL DEFAULT (datetime('now')),
    redeemed_at TEXT
);
-- the optional background questions, answered once before the guide ('' = prefer not to say)
CREATE TABLE IF NOT EXISTS profiles (
    user_id     INTEGER PRIMARY KEY REFERENCES users(id),
    age_range   TEXT NOT NULL,
    design      TEXT NOT NULL,
    answered_at TEXT NOT NULL DEFAULT (datetime('now'))
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
if use_x_accel:
    # behind nginx: build https:// links (the certificate's QR code) from X-Forwarded-Proto
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1)
app.config.update(
    SECRET_KEY=secret or secrets.token_hex(32),
    DB_PATH=os.environ.get("LABELWEB_DB", "labels.sqlite3"),
    IMAGE_ROOT=os.environ.get("LABELWEB_IMAGES", "images"),
    USE_X_ACCEL=use_x_accel,
    TARGET=int(os.environ.get("LABELWEB_TARGET", "3")),
    # pages per batch (0 = one per query), attention checks among them, batches per annotator (0 = no limit)
    BATCH=int(os.environ.get("LABELWEB_BATCH", "30")),
    CHECKS=int(os.environ.get("LABELWEB_CHECKS", "9")),
    MAX_BATCHES=int(os.environ.get("LABELWEB_MAX_BATCHES", "1")),
    # the guide counts as read only after this many seconds (its slides lock Next for about 45 s)
    GUIDE_MIN=int(os.environ.get("LABELWEB_GUIDE_MIN", "30")),
    # share of attention checks answered "no" that earns a certificate
    CERT_MIN=float(os.environ.get("LABELWEB_CERT_MIN", "0.8")),
    # 1: any verified Google or email-and-password account may sign in; 0: only emails added with allow-email
    OPEN_SIGNUP=os.environ.get("LABELWEB_OPEN_SIGNUP", "0") == "1",
    # gallery audit sheets: one directory per sheet; empty passcode closes the audit
    AUDIT_ROOT=os.environ.get("LABELWEB_AUDIT", "audit"),
    AUDIT_PASSCODE=os.environ.get("LABELWEB_AUDIT_PASSCODE", ""),
    # shown on the welcome page
    # "Name <email>" entries separated by ";"
    CONTACTS=os.environ.get("LABELWEB_CONTACTS",
                            "Osman Tursun <osman.tursun@qut.edu.au>; Sinan Kalkan <s.kalkan@metu.edu.tr>"),
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


def batch_size():
    """Pages per batch: LABELWEB_BATCH, or else one per query (distinct img_a of the real pairs)."""
    return app.config["BATCH"] or db().execute(
        "SELECT COUNT(DISTINCT img_a) FROM pairs WHERE is_check = 0").fetchone()[0]


def progress():
    """This annotator's own position: answers so far, batch number, place within the batch."""
    uid = session["user_id"]
    done = db().execute("SELECT COUNT(*) FROM labels WHERE user_id = ?", (uid,)).fetchone()[0]
    allowed = db().execute("SELECT batches FROM users WHERE id = ?", (uid,)).fetchone()[0]
    size = batch_size()
    batch = min(done // size + 1, allowed) if size else 1
    return {"done": done, "size": size, "batch": batch, "allowed": allowed,
            "in_batch": done - (batch - 1) * size}


def check_slots(uid, batch, size):
    """Positions within a batch that hold attention checks: fixed per annotator and batch."""
    return set(random.Random(f"{uid}:{batch}").sample(range(size), min(app.config["CHECKS"], size)))


def next_pair(skip_id=None):
    """The pair this annotator should see next, or None when there is nothing left for them.

    The batch position decides between an attention check and a real pair. A batch shows each
    query (img_a) once where it can: pairs whose query this annotator has not yet seen in the
    current batch come first. Real pairs then go fewest answers first, in the fixed random
    order shared by everyone, so pairs fill up to the target one after another; checks have no
    answer limit and go in a random order of the annotator's own. With skip_id (the pair on
    screen, for prefetching) the position is the one after it.
    """
    uid = session["user_id"]
    size = batch_size()
    done = db().execute("SELECT COUNT(*) FROM labels WHERE user_id = ?", (uid,)).fetchone()[0]
    if skip_id is not None:
        done += 1
    start = (done // size) * size if size else 0
    want_check = int(bool(size) and done - start in check_slots(uid, done // size + 1, size))
    salt = random.Random(uid).getrandbits(31)
    row = db().execute(
        """WITH used AS (SELECT p.img_a FROM labels l JOIN pairs p ON p.id = l.pair_id
                         WHERE l.user_id = :uid ORDER BY l.rowid LIMIT -1 OFFSET :start)
           SELECT p.* FROM pairs p LEFT JOIN labels l ON l.pair_id = p.id
           WHERE p.id IS NOT :skip
           GROUP BY p.id
           HAVING COALESCE(SUM(l.user_id = :uid), 0) = 0 AND (p.is_check OR COUNT(l.user_id) < :target)
           ORDER BY p.is_check != :want, p.img_a IN (SELECT img_a FROM used),
                    CASE WHEN p.is_check THEN 0 ELSE COUNT(l.user_id) END,
                    CASE WHEN p.is_check THEN (p.shuffle | :salt) - (p.shuffle & :salt) ELSE p.shuffle END
           LIMIT 1""",
        {"uid": uid, "start": start, "skip": skip_id, "target": app.config["TARGET"],
         "want": want_check, "salt": salt}).fetchone()
    # only checks left: every real pair has its answers, so there is nothing to label
    if row is not None and row["is_check"] and not want_check:
        return None
    return row


CODE_CHARS = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"  # no 0/O, 1/I/L


def normalise_code(text):
    """LS-XXXX-XXXX from whatever was typed or scanned, or None."""
    chars = re.sub(r"[^0-9A-Z]", "", text.upper())
    chars = chars[2:] if chars.startswith("LS") else chars
    if len(chars) != 8 or any(c not in CODE_CHARS for c in chars):
        return None
    return f"LS-{chars[:4]}-{chars[4:]}"


def certificate(uid):
    """This annotator's certificate row, deciding it the first time their first batch is complete."""
    row = db().execute("SELECT * FROM certificates WHERE user_id = ?", (uid,)).fetchone()
    if row is not None:
        return row
    c = db().execute(
        """SELECT COUNT(*) AS checks, COALESCE(SUM(l.label = 'no'), 0) AS correct
           FROM labels l JOIN pairs p ON p.id = l.pair_id WHERE l.user_id = ? AND p.is_check = 1""",
        (uid,)).fetchone()
    passed = c["checks"] == 0 or c["correct"] / c["checks"] >= app.config["CERT_MIN"]
    code = None
    if passed:
        code = "LS-" + "".join(secrets.choice(CODE_CHARS) for _ in range(8))
        code = code[:7] + "-" + code[7:]
    # OR IGNORE: two tabs finishing at once keep the first decision
    db().execute("INSERT OR IGNORE INTO certificates (user_id, code, checks, correct) VALUES (?, ?, ?, ?)",
                 (uid, code, c["checks"], c["correct"]))
    db().commit()
    return db().execute("SELECT * FROM certificates WHERE user_id = ?", (uid,)).fetchone()


def passcode_ok(typed):
    """The shared team passcode (LABELWEB_AUDIT_PASSCODE): case and surrounding spaces don't matter."""
    passcode = app.config["AUDIT_PASSCODE"]
    return bool(passcode) and secrets.compare_digest(typed.strip().upper(), passcode.strip().upper())


def has_passcode(uid):
    return db().execute("SELECT 1 FROM audit_access WHERE user_id = ?", (uid,)).fetchone() is not None


def grant_passcode(uid):
    db().execute("INSERT OR IGNORE INTO audit_access (user_id) VALUES (?)", (uid,))
    db().commit()


def can_do_more(prog):
    """Past LABELWEB_MAX_BATCHES, only annotators who entered the team passcode get more batches."""
    return (not app.config["MAX_BATCHES"] or prog["allowed"] < app.config["MAX_BATCHES"]
            or has_passcode(session["user_id"]))


def overall():
    """Project-wide numbers for /status."""
    target = app.config["TARGET"]
    total = db().execute("SELECT COUNT(*) FROM pairs WHERE is_check = 0").fetchone()[0]
    hist = db().execute(
        """SELECT n, COUNT(*) AS pairs FROM
           (SELECT COUNT(l.user_id) AS n FROM pairs p LEFT JOIN labels l ON l.pair_id = p.id
            WHERE p.is_check = 0 GROUP BY p.id)
           GROUP BY n ORDER BY n""").fetchall()
    # answers that count towards the target; extra answers beyond it, and checks, do not
    counted = sum(min(r["n"], target) * r["pairs"] for r in hist)
    complete = sum(r["pairs"] for r in hist if r["n"] >= target)
    return {"total": total, "target": target, "hist": hist, "counted": counted,
            "needed": total * target, "complete": complete}


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
    name = (claims.get("name") or "").strip()
    if app.config["OPEN_SIGNUP"]:
        # an existing row is left alone, so remove-email (active = 0) still blocks that person
        db().execute("INSERT OR IGNORE INTO users (email) VALUES (?)", (email,))
        db().commit()
    row = db().execute("SELECT id FROM users WHERE email = ? AND active = 1", (email,)).fetchone()
    if row is None:
        return {"error": f"{email} is not on the annotator list. Ask the project owner to add it."}, 403
    if name:
        # the Google display name, printed on the certificate
        db().execute("UPDATE users SET name = ? WHERE id = ?", (name, row["id"]))
        db().commit()
    session.clear()
    session["user_id"] = row["id"]
    session["name"] = email
    session.permanent = True
    return {"ok": True}


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.context_processor
def study_contacts():
    contacts = [re.fullmatch(r"\s*(.*?)\s*<\s*(\S+?)\s*>\s*", c) for c in app.config["CONTACTS"].split(";")]
    return {"contacts": [m.groups() for m in contacts if m], "year": time.gmtime().tm_year}


def has_profile():
    return db().execute("SELECT 1 FROM profiles WHERE user_id = ?",
                        (session["user_id"],)).fetchone() is not None


@app.route("/about-you", methods=["GET", "POST"])
@login_required
def profile():
    """The background questions, once, before the guide; every question may be left unanswered."""
    if has_profile():
        return redirect(url_for("index"))
    if request.method == "POST":
        age = request.form.get("age_range", "")
        design = request.form.get("design", "")
        if (age and age not in AGE_RANGES) or (design and design not in DESIGN):
            abort(400)
        db().execute("INSERT OR IGNORE INTO profiles (user_id, age_range, design) VALUES (?, ?, ?)",
                     (session["user_id"], age, design))
        db().commit()
        return redirect(url_for("index"))
    return render_template("profile.html", ages=AGE_RANGES, design=DESIGN)


@app.route("/guide", methods=["POST"])
@login_required
def guide():
    """The annotator finished the guide slides (shown over their first pair); from now on, pairs.

    Finishing faster than GUIDE_MIN seconds after the slides first opened is not accepted: the
    slides come back, so their reading time cannot be skipped by posting here directly.
    """
    started = session.get("guide_started")
    if started is None or time.time() - started < app.config["GUIDE_MIN"]:
        return redirect(url_for("index"))
    db().execute("UPDATE users SET guided = datetime('now') WHERE id = ? AND guided IS NULL",
                 (session["user_id"],))
    db().commit()
    return redirect(url_for("index"))


def guided():
    return db().execute("SELECT guided FROM users WHERE id = ?",
                        (session["user_id"],)).fetchone()[0] is not None


@app.route("/")
@login_required
def index():
    if not has_profile():
        return redirect(url_for("profile"))
    prog = progress()
    total = db().execute("SELECT COUNT(*) FROM pairs").fetchone()[0]
    if prog["size"] and prog["done"] >= prog["allowed"] * prog["size"]:
        # batch finished: the certificate, and another batch if allowed
        left = next_pair() is not None
        return render_template("done.html", state="batch", cert=certificate(session["user_id"]),
                               more=left and can_do_more(prog), ask_passcode=left and not can_do_more(prog),
                               error=request.args.get("error"), **prog)
    row = next_pair()
    if row is None:
        return render_template("done.html", state="empty" if total == 0 else "nothing", **prog)
    return redirect(url_for("pair", pair_id=row["id"]))


@app.route("/more", methods=["POST"])
@login_required
def more():
    if "passcode" in request.form:
        if not passcode_ok(request.form["passcode"]):
            return redirect(url_for("index", error="Wrong passcode."))
        grant_passcode(session["user_id"])
    prog = progress()
    if prog["size"] and prog["done"] >= prog["allowed"] * prog["size"] and can_do_more(prog):
        db().execute("UPDATE users SET batches = batches + 1 WHERE id = ?", (session["user_id"],))
        db().commit()
    return redirect(url_for("index"))


@app.route("/pair/<int:pair_id>")
@login_required
def pair(pair_id):
    if not has_profile():
        return redirect(url_for("profile"))
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
    intro = not guided()
    if intro:
        # when the guide slides were first shown, for the minimum reading time
        session.setdefault("guide_started", time.time())
    return render_template("pair.html", pair=p, current=current["label"] if current else None,
                           intro=intro,
                           prev_id=prev["pair_id"] if prev else None,
                           prefetch=next_pair(skip_id=pair_id), **progress())


@app.route("/pair/<int:pair_id>", methods=["POST"])
@login_required
def label(pair_id):
    value = request.form.get("label")
    if value not in LABELS:
        abort(400)
    if not guided():
        # the guide is mandatory: no answers before it is finished
        return redirect(url_for("pair", pair_id=pair_id))
    if db().execute("SELECT 1 FROM pairs WHERE id = ?", (pair_id,)).fetchone() is None:
        abort(404)
    answered = db().execute("SELECT 1 FROM labels WHERE user_id = ? AND pair_id = ?",
                            (session["user_id"], pair_id)).fetchone()
    prog = progress()
    if not answered and prog["size"] and prog["done"] >= prog["allowed"] * prog["size"]:
        # a new answer past the end of the batch (a stale tab): show the batch-done page instead
        return redirect(url_for("index"))
    db().execute(
        """INSERT INTO labels (user_id, pair_id, label) VALUES (?, ?, ?)
           ON CONFLICT (user_id, pair_id)
           DO UPDATE SET label = excluded.label, updated_at = datetime('now')""",
        (session["user_id"], pair_id, value))
    db().commit()
    return redirect(url_for("index"))


@app.route("/certificate")
@login_required
def certificate_page():
    row = db().execute("SELECT c.code, c.issued_at, u.name, u.email FROM certificates c "
                       "JOIN users u ON u.id = c.user_id WHERE c.user_id = ?",
                       (session["user_id"],)).fetchone()
    if row is None or row["code"] is None:
        return redirect(url_for("index"))
    url = url_for("verify", code=row["code"], _external=True)
    qr = segno.make(url, error="m").svg_inline(scale=4, border=0)
    return render_template("certificate.html", c=row, url=url, qr=qr, size=batch_size())


@app.route("/verify", methods=["GET", "POST"])
@app.route("/verify/<code>", methods=["GET", "POST"])
def verify(code=None):
    """Public check of a certificate code; passcode holders can also mark its reward given."""
    typed = code or request.values.get("code", "")
    norm = normalise_code(typed) if typed else None
    if norm and norm != code:
        return redirect(url_for("verify", code=norm))
    row = db().execute("SELECT c.*, u.name FROM certificates c JOIN users u ON u.id = c.user_id "
                       "WHERE c.code = ?", (norm,)).fetchone() if norm else None
    uid = session.get("user_id")
    staff = uid is not None and has_passcode(uid)
    if request.method == "POST" and row is not None and staff:
        db().execute("UPDATE certificates SET redeemed_at = datetime('now') WHERE code = ? "
                     "AND redeemed_at IS NULL", (norm,))
        db().commit()
        return redirect(url_for("verify", code=norm))
    return render_template("verify.html", typed=typed, code=norm, c=row, staff=staff)


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
    user_columns = [r["name"] for r in db().execute("PRAGMA table_info(users)")]
    if "batches" not in user_columns:
        db().execute("ALTER TABLE users ADD COLUMN batches INTEGER NOT NULL DEFAULT 1")
        db().commit()
    if "name" not in user_columns:
        db().execute("ALTER TABLE users ADD COLUMN name TEXT NOT NULL DEFAULT ''")
        db().commit()
    if "guided" not in user_columns:
        db().execute("ALTER TABLE users ADD COLUMN guided TEXT")
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
    """Write answers, annotators, pairs_summary, participants and annotator_emails TSVs into OUT_DIR."""
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
    profiles = {r["user_id"]: r for r in db().execute("SELECT * FROM profiles")}
    write_tsv(os.path.join(out_dir, "participants.tsv"), ["annotator", "age_range", "design_experience"],
              [(anon[u["id"]], profiles[u["id"]]["age_range"] or "not_said",
                profiles[u["id"]]["design"] or "not_said") if u["id"] in profiles
               else (anon[u["id"]], "", "") for u in users])

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
