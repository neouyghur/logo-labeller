# logo-labeller

Transfer repo for the logo-similarity labelling site (Nectar VM, https://203-101-229-244.sslip.io).
Aqua pushes here with `similarity_labelling/publish_repo.sh`; the VM deploys with `sudo labelweb-sync`.

- `similarity_labelling/` — the site's code; source of truth is `Logo-dataset-projects/similarity_labelling/` on Aqua
- `data/pairs.csv` — the pair set (`pair_key,img_a,img_b`, paths relative to `data/images/`)
- `data/images/` — the images those pairs use

Private: the images are scraped company logos.
