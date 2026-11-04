"""Summary statistics of a labelling export, to decide how to build the new query set.

    python analyze_labels.py EXPORT_DIR

EXPORT_DIR is what `labelweb-admin export` writes (answers.tsv, annotators.tsv,
participants.tsv). Untrusted annotators (attention checks below the export's bar) are left
out everywhere. Prints the report to stdout; nothing is written.
"""
import sys
from collections import Counter
from itertools import combinations

import pandas as pd

LABELS = ["yes", "unsure", "no"]


def fleiss(counts):
    """Fleiss' kappa over rows of per-category counts, each row with the same number of raters."""
    n = counts.sum(axis=1).iloc[0]
    p_cat = counts.sum() / counts.values.sum()
    p_row = ((counts ** 2).sum(axis=1) - n) / (n * (n - 1))
    pe = (p_cat ** 2).sum()
    return (p_row.mean() - pe) / (1 - pe)


def cohen(a, b):
    po = (a == b).mean()
    pe = sum((a == c).mean() * (b == c).mean() for c in LABELS)
    return (po - pe) / (1 - pe)


def majority(labels):
    """Most common label, or 'tie' when the top two are level."""
    top = Counter(labels).most_common()
    return "tie" if len(top) > 1 and top[0][1] == top[1][1] else top[0][0]


def pct(x, total):
    return f"{x:,} ({100 * x / total:.1f}%)" if total else f"{x:,}"


def main(d):
    ans = pd.read_csv(f"{d}/answers.tsv", sep="\t")
    ann = pd.read_csv(f"{d}/annotators.tsv", sep="\t")
    par = pd.read_csv(f"{d}/participants.tsv", sep="\t")
    trusted = set(ann.loc[ann.trusted == "yes", "annotator"])
    out = []
    say = out.append

    # --- participation
    vol, core = ann[ann.core == 0], ann[ann.core == 1]
    say("## Participation")
    say(f"annotators: {len(ann)} ({len(core)} core, {len(vol)} volunteers); "
        f"untrusted (checks < bar): {(ann.trusted != 'yes').sum()}")
    say(f"answers: {len(ans):,} (real {int((ans.is_check == 0).sum()):,}, checks {int(ans.is_check.sum()):,})")
    say(f"volunteer answers each: median {vol.n_answers.median():.0f}, "
        f"min {vol.n_answers.min()}, max {vol.n_answers.max()}; "
        f"finished a 50-page batch: {(vol.n_answers >= 50).sum()}")
    say(f"volunteer check accuracy: median {vol.check_accuracy.median():.2f}, "
        f"< 0.8: {(vol.check_accuracy < 0.8).sum()}")
    for _, r in core.iterrows():
        say(f"core {r.annotator}: {r.n_answers:,} answers, check accuracy {r.check_accuracy:.2f}")

    real = ans[(ans.is_check == 0) & ans.annotator.isin(trusted)].copy()
    n_pairs = real.pair_key.nunique()
    all_pairs = pd.read_csv(f"{d}/pairs_summary.tsv", sep="\t", usecols=["pair_key", "query"])
    say(f"real pairs with any trusted answer: {n_pairs:,} of {len(all_pairs):,}")

    # --- coverage
    cov = real.groupby("pair_key").core.agg(n_core="sum", n="count")
    cov["n_vol"] = cov.n - cov.n_core
    cov = cov.reindex(all_pairs.pair_key, fill_value=0)
    say("\n## Coverage (trusted answers per real pair)")
    say("core answers:      " + "  ".join(f"{k}: {v:,}" for k, v in sorted(Counter(cov.n_core).items())))
    say("volunteer answers: " + "  ".join(f"{k}: {v:,}" for k, v in sorted(Counter(cov.n_vol).items())))

    # --- core agreement
    say("\n## Core labellers' agreement")
    cr = real[real.core == 1].pivot_table(index="pair_key", columns="annotator", values="label", aggfunc="first")
    full = cr.dropna()
    say(f"pairs labelled by all {cr.shape[1]} core labellers: {len(full):,}")
    counts = full.apply(lambda r: r.value_counts(), axis=1).reindex(columns=LABELS, fill_value=0).fillna(0)
    say(f"Fleiss' kappa (3 classes): {fleiss(counts):.3f}")
    # yes vs not-yes, the decision that matters for a binary positive
    yb = counts.assign(no=counts.no + counts.unsure).drop(columns="unsure")[["yes", "no"]]
    say(f"Fleiss' kappa (yes vs unsure+no): {fleiss(yb):.3f}")
    say(f"unanimous: {pct(int((counts.max(axis=1) == cr.shape[1]).sum()), len(full))}")
    for a, b in combinations(cr.columns, 2):
        both = cr[[a, b]].dropna()
        say(f"  {a}-{b}: agree {(both[a] == both[b]).mean():.1%}, Cohen's kappa {cohen(both[a], both[b]):.3f} "
            f"(n={len(both):,})")
    for a in cr.columns:
        say(f"  {a} label mix: " + ", ".join(f"{k} {v:.0%}" for k, v in
                                               cr[a].value_counts(normalize=True).reindex(LABELS).fillna(0).items()))

    # --- majorities
    core_maj = real[real.core == 1].groupby("pair_key").label.agg(majority)
    vol_maj = real[real.core == 0].groupby("pair_key").label.agg(majority)
    all_maj = real.groupby("pair_key").label.agg(majority)
    say("\n## Majority label per pair")
    for name, m in [("core", core_maj), ("volunteers", vol_maj), ("everyone", all_maj)]:
        c = m.value_counts()
        say(f"{name:10s}: " + "  ".join(f"{k} {pct(int(c.get(k, 0)), len(m))}" for k in LABELS + ["tie"]))

    say("\n## Volunteers vs core (pairs with both majorities, ties dropped)")
    both = pd.concat([core_maj.rename("core"), vol_maj.rename("vol")], axis=1).dropna()
    both = both[(both.core != "tie") & (both.vol != "tie")]
    say(f"pairs: {len(both):,}; same majority: {(both.core == both.vol).mean():.1%}; "
        f"Cohen's kappa {cohen(both.core, both.vol):.3f}")
    say(pd.crosstab(both.core, both.vol, rownames=["core"], colnames=["volunteers"])
        .reindex(index=LABELS, columns=LABELS, fill_value=0).to_string())

    # --- per query: how many positives survive under each rule
    say("\n## Positives kept per query (core majority)")
    q = all_pairs.set_index("pair_key")["query"]
    keep = pd.DataFrame({"query": q, "maj": core_maj.reindex(q.index)})
    n_q = keep["query"].nunique()
    for name, ok in [("yes only", keep.maj == "yes"), ("yes + unsure", keep.maj.isin(["yes", "unsure"]))]:
        per = ok.groupby(keep["query"]).sum()
        say(f"{name:13s}: {int(ok.sum()):,} positives; per query median {per.median():.0f}, "
            f"queries with 0: {(per == 0).sum()}, <3: {(per < 3).sum()}, <5: {(per < 5).sum()} (of {n_q})")

    # --- does design experience matter
    say("\n## Volunteers' agreement with the core majority, by design experience")
    v = real[real.core == 0].merge(core_maj.rename("core_maj"), left_on="pair_key", right_index=True)
    v = v[v.core_maj != "tie"].merge(par, on="annotator", how="left")
    by = v.groupby("design_experience").apply(lambda g: pd.Series(
        {"annotators": g.annotator.nunique(), "answers": len(g), "agree": (g.label == g.core_maj).mean()}),
        include_groups=False)
    say(by.to_string(formatters={"agree": "{:.1%}".format, "answers": "{:,.0f}".format,
                                 "annotators": "{:.0f}".format}))

    # --- time per answer (gaps between consecutive answers under 2 minutes)
    t = ans.assign(ts=pd.to_datetime(ans.answered_at)).sort_values(["annotator", "ts"])
    gap = t.groupby("annotator").ts.diff().dt.total_seconds()
    gap = gap[(gap > 0) & (gap < 120)]
    say(f"\n## Time per answer: median {gap.median():.1f} s (core {gap[t.core == 1].median():.1f} s, "
        f"volunteers {gap[t.core == 0].median():.1f} s)")
    print("\n".join(out))


if __name__ == "__main__":
    main(sys.argv[1])
