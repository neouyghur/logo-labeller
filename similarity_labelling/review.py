"""Visual review of the finished pair labels, behind the audit passcode.

/review/        every query with how many of its positives each rule keeps
/review/<n>     query n (1-based, in img_a order) beside all its positives, each with the core
                labellers' answers, the volunteers' counts, the mean score and the verdict

Only trusted annotators count (attention checks >= LABELWEB_CERT_MIN, the export's default bar).
Score: yes 2, unsure 1, no 0, averaged over every trusted answer. Verdicts from the core votes:
strict (3/3 yes), majority (2/3 yes), rejected (>= 2 no), disputed (anything else).
"""
from flask import Blueprint, abort, render_template, url_for

from app import anon_ids, app, db
from audit import audit_required

bp = Blueprint("review", __name__, url_prefix="/review")


def queries():
    return [r[0] for r in db().execute(
        "SELECT DISTINCT img_a FROM pairs WHERE is_check = 0 ORDER BY img_a")]


def trusted_ids():
    """Annotators whose attention checks clear the bar, plus those who answered none."""
    return {r[0] for r in db().execute(
        """SELECT l.user_id FROM labels l JOIN pairs p ON p.id = l.pair_id GROUP BY l.user_id
           HAVING SUM(p.is_check) = 0 OR 1.0 * SUM(p.is_check AND l.label = 'no') / SUM(p.is_check) >= ?""",
        (app.config["CERT_MIN"],))}


def verdict(core_yes, core_no):
    if core_yes == 3:
        return "strict"
    if core_yes >= 2:
        return "majority"
    if core_no >= 2:
        return "rejected"
    return "disputed"


def pair_rows(where="", args=()):
    """One dict per real pair: its answers split into core and volunteer, score and verdict."""
    trusted = trusted_ids()
    anon = anon_ids()
    pairs = {}
    for r in db().execute(
            f"""SELECT p.id, p.img_a, p.img_b, p.pair_key, l.user_id, l.label, u.core
                FROM pairs p LEFT JOIN labels l ON l.pair_id = p.id LEFT JOIN users u ON u.id = l.user_id
                WHERE p.is_check = 0 {where} ORDER BY p.id, l.user_id""", args):
        p = pairs.setdefault(r["id"], {"id": r["id"], "img_a": r["img_a"], "img_b": r["img_b"],
                                       "key": r["pair_key"], "core": [],
                                       "vol": {"yes": 0, "unsure": 0, "no": 0}})
        if r["user_id"] is None or r["user_id"] not in trusted:
            continue
        if r["core"]:
            p["core"].append((anon[r["user_id"]], r["label"]))
        else:
            p["vol"][r["label"]] += 1
    score = {"yes": 2, "unsure": 1, "no": 0}
    for p in pairs.values():
        labels = [lab for _, lab in p["core"]] + [k for k, n in p["vol"].items() for _ in range(n)]
        p["score"] = sum(score[x] for x in labels) / len(labels) if labels else None
        p["verdict"] = verdict(sum(lab == "yes" for _, lab in p["core"]),
                               sum(lab == "no" for _, lab in p["core"]))
    return list(pairs.values())


@bp.route("/")
@audit_required
def index():
    order = queries()
    per = {q: {"n": 0, "strict": 0, "majority": 0, "rejected": 0, "disputed": 0} for q in order}
    for p in pair_rows():
        c = per[p["img_a"]]
        c["n"] += 1
        c[p["verdict"]] += 1
    rows = [dict(c, num=i, img_a=q, kept=c["strict"] + c["majority"]) for i, (q, c) in enumerate(per.items(), 1)]
    totals = {k: sum(r[k] for r in rows) for k in ("n", "strict", "majority", "rejected", "disputed")}
    return render_template("review.html", rows=rows, totals=totals)


@bp.route("/<int:num>")
@audit_required
def query(num):
    order = queries()
    if not 1 <= num <= len(order):
        abort(404)
    img_a = order[num - 1]
    pairs = sorted(pair_rows("AND p.img_a = ?", (img_a,)),
                   key=lambda p: (-(p["score"] or 0), p["key"]))
    counts = {v: sum(p["verdict"] == v for p in pairs) for v in ("strict", "majority", "rejected", "disputed")}
    return render_template("review_query.html", num=num, total=len(order), img_a=img_a, pairs=pairs,
                           counts=counts,
                           prev=url_for("review.query", num=num - 1) if num > 1 else None,
                           next=url_for("review.query", num=num + 1) if num < len(order) else None)


app.register_blueprint(bp)
