"""Add attention-check pairs to a packed pairs.csv.

A check shows a query with a positive of a different query, so the right answer is "no".
Checks reuse images already in the pack, so no repacking is needed. Output keeps the input
pairs (is_check = 0) and appends the checks (is_check = 1, pair_key check_<query>_<n>).

  python make_checks.py --pairs pack/pairs.csv --out pack/pairs_with_checks.csv --fraction 0.03
"""
import argparse
import csv
import os
import random


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pairs", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--fraction", type=float, default=0.03, help="checks per real pair")
    ap.add_argument("--seed", type=int, default=20261022)
    args = ap.parse_args()

    with open(args.pairs, newline="") as f:
        pairs = list(csv.DictReader(f))
    by_query = {}
    for p in pairs:
        by_query.setdefault(p["img_a"], []).append(p["img_b"])
    queries = sorted(by_query)

    rng = random.Random(args.seed)
    n_checks = round(len(pairs) * args.fraction)
    used, checks = set(), []
    while len(checks) < n_checks:
        query, other = rng.sample(queries, 2)
        positive = rng.choice(by_query[other])
        if (query, positive) in used:
            continue
        used.add((query, positive))
        name = os.path.splitext(os.path.basename(query))[0]
        checks.append({"pair_key": f"check_{name}_{len(checks):03d}", "img_a": query,
                       "img_b": positive, "is_check": 1})

    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["pair_key", "img_a", "img_b", "is_check"])
        w.writeheader()
        w.writerows({**p, "is_check": 0} for p in pairs)
        w.writerows(checks)
    print(f"{len(pairs)} pairs + {len(checks)} checks -> {args.out}")


if __name__ == "__main__":
    main()
