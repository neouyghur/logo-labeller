"""Hosts the gallery quality-audit sheets (quality_audit/make_sheets.py) for any number of labellers.

The sheets are the static A.html / app.js / data_A.js pages built for two labellers on file://.
Here every signed-in user who has entered the audit passcode gets each sheet as their own
labeller (L<user id>), with the items in their own shuffled order. app.js is served unchanged;
sync.js, loaded after it, saves the sheet's state and its TSV rows to the server every few
seconds, and the page restores the server copy when it is newer than the browser's.

Sheets live in AUDIT_ROOT/<sheet>/ (A.html, app.js, app.css, data_A.js, tiles/).
Labels come out with `flask --app app audit-export OUT_DIR`, one TSV per labeller and sheet in
the sheets' own export format.
"""
import csv
import json
import mimetypes
import os
import random
import re
import secrets
import time
from functools import wraps

import click
from flask import (Blueprint, Response, abort, current_app, redirect, render_template,
                   request, send_from_directory, session, url_for)
from werkzeug.security import safe_join

from app import app, batch_size, db, login_required, overall, safe_next

bp = Blueprint("audit", __name__, url_prefix="/audit")

SHEET_RE = re.compile(r"^[A-Za-z0-9_-]+$")
CFG_RE = re.compile(r"window\.CFG = (\{.*?\});</script>", re.S)
# the sheets' own export columns (app.js HEAD), plus labeller and adjudicated
HEAD = {"task1": ["item_id", "label", "reason", "flag", "generic", "page_confirmed"],
        "task2": ["item_id", "dup", "dup_ids", "unsure", "page_confirmed"],
        "company": ["item_id", "company_label", "page_confirmed"]}


def labeller_id(user_id):
    return f"L{user_id:02d}"


def sheet_dir(sheet):
    if not SHEET_RE.match(sheet):
        abort(404)
    path = os.path.join(current_app.config["AUDIT_ROOT"], sheet)
    if not os.path.isfile(os.path.join(path, "A.html")):
        abort(404)
    return path


def sheet_cfg(path):
    with open(os.path.join(path, "A.html")) as f:
        m = CFG_RE.search(f.read())
    return json.loads(m.group(1))


def sheets():
    root = current_app.config["AUDIT_ROOT"]
    names = sorted(n for n in os.listdir(root) if SHEET_RE.match(n)
                   and os.path.isfile(os.path.join(root, n, "A.html"))) if os.path.isdir(root) else []
    return [(n, sheet_cfg(os.path.join(root, n))["title"]) for n in names]


def has_access():
    return db().execute("SELECT 1 FROM audit_access WHERE user_id = ?",
                        (session["user_id"],)).fetchone() is not None


def audit_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not has_access():
            return redirect(url_for("audit.index", next=request.path))
        return view(*args, **kwargs)
    return login_required(wrapped)


@bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    error = None
    passcode = current_app.config["AUDIT_PASSCODE"]
    if request.method == "POST" and not has_access():
        # a simple shared word: case and surrounding spaces don't matter
        typed = request.form.get("passcode", "").strip().upper()
        if passcode and secrets.compare_digest(typed, passcode.strip().upper()):
            db().execute("INSERT OR IGNORE INTO audit_access (user_id) VALUES (?)", (session["user_id"],))
            db().commit()
            nxt = request.args.get("next")
            return redirect(safe_next(nxt) if nxt else url_for("audit.index"))
        error = "Wrong passcode." if passcode else "The audit is closed."
    return render_template("audit_index.html", access=has_access(), error=error,
                           sheets=sheets() if has_access() else [],
                           labeller=labeller_id(session["user_id"]))


@bp.route("/<sheet>/")
@audit_required
def page(sheet):
    path = sheet_dir(sheet)
    with open(os.path.join(path, "A.html")) as f:
        html = f.read()
    cfg = sheet_cfg(path)
    cfg["labeller"] = labeller_id(session["user_id"])
    row = db().execute("SELECT state, updated_ms FROM audit_state WHERE user_id = ? AND sheet = ?",
                       (session["user_id"], sheet)).fetchone()
    server = {"state": json.loads(row["state"]), "updated_ms": row["updated_ms"]} if row else None
    html = CFG_RE.sub(lambda m: f"window.CFG = {json.dumps(cfg)};</script>", html, count=1)
    # restore the server copy before app.js reads localStorage; then load the autosave
    restore = ("<script>window.SERVER_STATE = " + json.dumps(server).replace("</", "<\\/") +
               ";</script>\n<script src=\"restore.js\"></script>")
    html = html.replace('<script src="data_A.js"></script>',
                        '<script src="data.js"></script>\n' + restore, 1)
    html = html.replace('<script src="app.js"></script>',
                        '<script src="app.js"></script>\n<script src="sync.js"></script>', 1)
    return Response(html, mimetype="text/html")


@bp.route("/<sheet>/data.js")
@audit_required
def data(sheet):
    with open(os.path.join(sheet_dir(sheet), "data_A.js")) as f:
        text = f.read()
    d = json.loads(text[text.index("{"):text.rindex("}") + 1])
    # each labeller's own order, stable across visits
    random.Random(f"{sheet}:{session['user_id']}").shuffle(d["items"])
    return Response("window.DATA = " + json.dumps(d) + ";", mimetype="application/javascript")


@bp.route("/<sheet>/state", methods=["POST"])
@audit_required
def save_state(sheet):
    sheet_dir(sheet)
    body = request.get_json(silent=True) or {}
    if not isinstance(body.get("state"), dict) or not isinstance(body.get("rows"), list):
        abort(400)
    now = int(time.time() * 1000)
    db().execute(
        """INSERT INTO audit_state (user_id, sheet, state, rows, updated_ms) VALUES (?, ?, ?, ?, ?)
           ON CONFLICT (user_id, sheet) DO UPDATE
           SET state = excluded.state, rows = excluded.rows, updated_ms = excluded.updated_ms""",
        (session["user_id"], sheet, json.dumps(body["state"]), json.dumps(body["rows"]), now))
    db().commit()
    return {"ok": True, "updated_ms": now}


@bp.route("/<sheet>/<path:asset>")
@audit_required
def asset(sheet, asset):
    path = sheet_dir(sheet)
    if asset in ("restore.js", "sync.js"):
        return send_from_directory(os.path.join(current_app.root_path, "static"), asset)
    if asset.startswith("data_") or asset.endswith(".html") or safe_join(path, asset) is None:
        abort(404)
    if current_app.config["USE_X_ACCEL"]:
        resp = current_app.response_class()
        resp.headers["X-Accel-Redirect"] = f"/protected-audit/{sheet}/{asset}"
        resp.mimetype = mimetypes.guess_type(asset)[0] or "application/octet-stream"
        return resp
    return send_from_directory(path, asset)


@app.route("/status")
@audit_required
def status():
    """Overall progress of the pair task and the audit; behind the same passcode as the audit."""
    size = batch_size()
    annotators = db().execute(
        """SELECT u.email, u.active, COUNT(l.pair_id) AS answers,
                  COALESCE(SUM(p.is_check), 0) AS checks,
                  COALESCE(SUM(p.is_check AND l.label = 'no'), 0) AS correct,
                  MAX(l.updated_at) AS last
           FROM users u JOIN labels l ON l.user_id = u.id JOIN pairs p ON p.id = l.pair_id
           GROUP BY u.id ORDER BY answers DESC""").fetchall()
    audit = db().execute(
        """SELECT s.user_id, s.sheet, s.state, s.updated_ms, u.email FROM audit_state s
           JOIN users u ON u.id = s.user_id ORDER BY s.sheet, s.user_id""").fetchall()
    audit_rows = [(labeller_id(r["user_id"]), r["email"], r["sheet"],
                   len(json.loads(r["state"]).get("labels", {})),
                   len(json.loads(r["state"]).get("confirmed", {})),
                   time.strftime("%Y-%m-%d %H:%M", time.gmtime(r["updated_ms"] / 1000)))
                  for r in audit]
    return render_template("status.html", o=overall(), size=size, annotators=annotators,
                           audit=audit_rows)


@app.cli.command("audit-export")
@click.argument("out_dir")
def audit_export(out_dir):
    """One labels_<sheet>_<labeller>.tsv per labeller and sheet, plus audit_labellers.tsv."""
    os.makedirs(out_dir, exist_ok=True)
    rows = db().execute(
        """SELECT s.user_id, s.sheet, s.rows, s.updated_ms, u.email FROM audit_state s
           JOIN users u ON u.id = s.user_id ORDER BY s.sheet, s.user_id""").fetchall()
    for r in rows:
        task = sheet_cfg(os.path.join(app.config["AUDIT_ROOT"], r["sheet"]))["task"]
        who = labeller_id(r["user_id"])
        with open(os.path.join(out_dir, f"labels_{r['sheet']}_{who}.tsv"), "w", newline="") as f:
            w = csv.writer(f, delimiter="\t", lineterminator="\n")
            w.writerow(HEAD[task] + ["labeller", "adjudicated"])
            w.writerows(list(x) + [who, 0] for x in json.loads(r["rows"]))
    with open(os.path.join(out_dir, "audit_labellers.tsv"), "w", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(["labeller", "email", "sheet", "last_saved_utc"])
        w.writerows((labeller_id(r["user_id"]), r["email"], r["sheet"],
                     time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(r["updated_ms"] / 1000)))
                    for r in rows)
    click.echo(f"{len(rows)} labeller sheets -> {out_dir}/")


app.register_blueprint(bp)
