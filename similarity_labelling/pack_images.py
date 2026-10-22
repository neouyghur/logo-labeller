"""Bundle the images a pairs CSV refers to into one tar for the labelling VM.

Input CSV header: pair_key,img_a,img_b — image paths as on Aqua (~/10MLOGO or /work form).
Writes into --out:
  images.tar   each image once, named by its path relative to --root
  pairs.csv    the input pairs with img_a/img_b rewritten to those relative paths;
               pairs with a missing image are dropped
  missing.txt  image paths that do not exist
"""
import argparse
import csv
import os
import sys
import tarfile


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pairs", required=True, help="input CSV with pair_key,img_a,img_b")
    ap.add_argument("--root", default="/work/SAIVT/LOGO", help="image paths are stored relative to this")
    ap.add_argument("--out", required=True, help="output directory")
    args = ap.parse_args()

    root = os.path.realpath(args.root)
    os.makedirs(args.out, exist_ok=True)

    pairs, images = [], {}
    with open(args.pairs, newline="") as f:
        for r in csv.DictReader(f):
            row = {"pair_key": r["pair_key"]}
            for col in ("img_a", "img_b"):
                path = os.path.realpath(os.path.expanduser(r[col]))
                rel = os.path.relpath(path, root)
                if rel.startswith(".."):
                    sys.exit(f"{r[col]} is not under {root}")
                images[path] = rel
                row[col] = rel
            pairs.append(row)

    missing = {p for p in images if not os.path.isfile(p)}
    with tarfile.open(os.path.join(args.out, "images.tar"), "w") as tar:
        for path, rel in sorted(images.items(), key=lambda kv: kv[1]):
            if path not in missing:
                tar.add(path, arcname=rel)

    missing_rel = {images[p] for p in missing}
    kept = [r for r in pairs if r["img_a"] not in missing_rel and r["img_b"] not in missing_rel]
    with open(os.path.join(args.out, "pairs.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["pair_key", "img_a", "img_b"])
        w.writeheader()
        w.writerows(kept)
    with open(os.path.join(args.out, "missing.txt"), "w") as f:
        f.writelines(p + "\n" for p in sorted(missing))

    print(f"{len(pairs)} pairs read, {len(kept)} kept, {len(pairs) - len(kept)} dropped; "
          f"{len(images) - len(missing)} images packed, {len(missing)} missing")


if __name__ == "__main__":
    main()
