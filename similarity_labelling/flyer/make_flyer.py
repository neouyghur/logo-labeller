"""Print flyers for the labelling site: one A4 page per version (METU, QUT), as HTML.

    python flyer/make_flyer.py OUTDIR [--metu-url URL] [--qut-url URL]

Writes OUTDIR/flyer_<version>.html; render each to PDF/PNG with headless Chrome
(see render_flyers.sh). Only the reward box and the link differ between versions:
QUT gets the plain site address, not the dar.vin short link.
"""
import argparse
import pathlib

import segno

HERE = pathlib.Path(__file__).resolve().parent
BRAND = HERE.parent / "static" / "brand"

REWARDS = {
    "metu": ("Your reward",
             "Finish your batch, download your certificate, and collect your reward from "
             "<strong>Prof. Sinan Kalkan</strong> (s.kalkan@metu.edu.tr)."),
    "qut": ("Get a treat!",
            "Finish your batch and show your certificate (on your phone is fine) "
            "to grab a <strong>chocolate or candy</strong>."),
}

BIRD = ('<svg viewBox="0 0 120 120"><circle cx="60" cy="60" r="50" fill="{c}"/>'
        '<path d="M28 64 Q44 38 60 58 Q76 38 92 64 Q76 52 60 74 Q44 52 28 64Z" fill="#fff"/></svg>')

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Logo look-alike study</title>
<style>
  @page {{ size: A4; margin: 0; }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; }}
  body {{ width: 210mm; height: 297mm; font-family: "Helvetica Neue", Arial, sans-serif;
         color: #1d1d1b; background: #fff; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
  .page {{ height: 297mm; display: flex; flex-direction: column; }}
  header {{ background: #12305f; color: #fff; padding: 11mm 16mm 8mm; }}
  .kicker {{ font-size: 12pt; letter-spacing: .14em; text-transform: uppercase; opacity: .8; margin: 0 0 4mm; }}
  h1 {{ font-size: 36pt; line-height: 1.02; margin: 0 0 5mm; }}
  .sub {{ font-size: 13.5pt; line-height: 1.35; margin: 0; max-width: 165mm; opacity: .95; }}
  main {{ flex: 1; padding: 8mm 16mm 0; display: flex; flex-direction: column; gap: 5mm; }}
  .demo {{ display: flex; align-items: center; justify-content: center; gap: 10mm;
          border: 1px solid #d9dde4; border-radius: 6mm; padding: 4mm; }}
  .demo svg {{ width: 24mm; height: 24mm; }}
  .vs {{ text-align: center; font-size: 15pt; font-weight: 700; }}
  .btns {{ display: flex; gap: 2.5mm; margin-top: 3mm; }}
  .btn {{ border-radius: 2mm; padding: 1.8mm 4mm; font-size: 11pt; font-weight: 700; color: #fff; }}
  .yes {{ background: #2e7d32; }} .unsure {{ background: #8a8f98; }} .no {{ background: #c62828; }}
  .row {{ display: flex; gap: 9mm; align-items: stretch; }}
  .steps {{ flex: 1; margin: 0; padding: 0; list-style: none; counter-reset: s; }}
  .steps li {{ counter-increment: s; position: relative; padding: 0 0 3mm 13mm; font-size: 12pt; line-height: 1.35; }}
  .steps li::before {{ content: counter(s); position: absolute; left: 0; top: -.5mm; width: 9mm; height: 9mm;
          border-radius: 50%; background: #12305f; color: #fff; font-weight: 700; text-align: center; line-height: 9mm; }}
  .steps b {{ display: block; }}
  .qr {{ width: 70mm; text-align: center; }}
  .qr svg {{ width: 55mm; height: 55mm; display: block; margin: 0 auto; }}
  .scan {{ font-weight: 700; font-size: 14pt; margin: 1mm 0; }}
  .url {{ font-family: Menlo, Consolas, monospace; font-size: 9.5pt; white-space: nowrap; margin: 0; }}
  .reward {{ background: #fff4d6; border: 2px solid #f0b429; border-radius: 6mm; padding: 4mm 7mm;
            display: flex; gap: 6mm; align-items: center; }}
  .reward .icon {{ font-size: 0; }}
  .reward .icon svg {{ width: 15mm; height: 15mm; }}
  .reward h2 {{ margin: 0 0 1mm; font-size: 17pt; }}
  .reward p {{ margin: 0; font-size: 12.5pt; line-height: 1.35; }}
  .note {{ font-size: 9.5pt; color: #555; line-height: 1.4; margin: 0; }}
  footer {{ padding: 5mm 16mm 7mm; display: flex; align-items: center; gap: 10mm; border-top: 1px solid #d9dde4; margin-top: 4mm; }}
  footer img {{ height: 12mm; }}
  footer {{ flex-wrap: wrap; row-gap: 3mm; }}
  footer .who {{ flex-basis: 100%; font-size: 9pt; color: #555; line-height: 1.4; }}
</style></head>
<body><div class="page">
<header>
  <p class="kicker">Volunteers wanted · Logo research</p>
  <h1>Can you spot a<br>look-alike logo?</h1>
  <p class="sub">Help AI research on trademark similarity: look at pairs of logos and tell us
  whether they look similar. No expertise needed.</p>
</header>
<main>
  <div class="demo">
    {bird_a}
    <div class="vs">Similar?<div class="btns"><span class="btn yes">Yes</span>
      <span class="btn unsure">Unsure</span><span class="btn no">No</span></div></div>
    {bird_b}
  </div>
  <div class="row">
    <ol class="steps">
      <li><b>Scan the QR code</b>Sign in with Google or email, on your phone or laptop.</li>
      <li><b>Take the short guide</b>A few slides show what counts as similar (about 4 min).</li>
      <li><b>Judge 50 logo pairs</b>Yes, Unsure or No for each one (about 10 min).</li>
      <li><b>Get your certificate</b>A printable certificate with a QR code to verify it.</li>
    </ol>
    <div class="qr">{qr}<p class="scan">Scan to start</p><p class="url">{url}</p></div>
  </div>
  <div class="reward">
    <div class="icon">{gift}</div>
    <div><h2>{reward_title}</h2><p>{reward_text}</p></div>
  </div>
  <p class="note">Your answers are used only for research. The two background questions
  (age range, design experience) are optional, and you can stop at any time.</p>
</main>
<footer>
  <img src="{qut}" alt="QUT"><img src="{metu}" alt="METU">
  <div class="who">Osman Tursun, Yusuf Efe Ozudogru, Efe Buke, Clinton Fookes, Sinan Kalkan<br>
  Contact: osman.tursun@qut.edu.au · s.kalkan@metu.edu.tr</div>
</footer>
</div></body></html>
"""

GIFT = ('<svg viewBox="0 0 64 64"><rect x="8" y="26" width="48" height="32" rx="3" fill="#e0533d"/>'
        '<rect x="5" y="18" width="54" height="11" rx="3" fill="#f06a52"/>'
        '<rect x="28" y="18" width="8" height="40" fill="#f0b429"/>'
        '<path d="M32 18 C22 4 10 12 20 18 Z M32 18 C42 4 54 12 44 18 Z" fill="#f0b429"/></svg>')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir", type=pathlib.Path)
    ap.add_argument("--metu-url", default="https://dar.vin/logo-similarity")
    ap.add_argument("--qut-url", default="https://203-101-229-244.sslip.io")
    a = ap.parse_args()
    a.outdir.mkdir(parents=True, exist_ok=True)
    urls = {"metu": a.metu_url, "qut": a.qut_url}
    for version, (title, text) in REWARDS.items():
        url = urls[version]
        qr = segno.make(url, error="m").svg_inline(omitsize=True, dark="#12305f", border=2)
        html = PAGE.format(
            bird_a=BIRD.format(c="#2457c5"), bird_b=BIRD.format(c="#1a9a9a"),
            qr=qr, url=url, gift=GIFT, reward_title=title, reward_text=text,
            qut=(BRAND / "qut.svg").as_uri(), metu=(BRAND / "metu.svg").as_uri())
        out = a.outdir / f"flyer_{version}.html"
        out.write_text(html)
        print(out)


if __name__ == "__main__":
    main()
