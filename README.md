# welzel.github.io

Personal website of Julius Welzel, built with [Quarto](https://quarto.org).
The rendered site lives in `docs/` and is also published to the `gh-pages` branch.

## Local preview

```bash
quarto preview        # live preview
quarto render         # build into docs/
```

## Publications

The publication list updates itself. Every Monday the GitHub Action
`.github/workflows/update-publications.yml` runs `scripts/update_publications.py`.
The script:

1. collects publications from ORCID, OpenAlex, Google Scholar and Crossref,
2. merges duplicates and folds preprints into their published versions,
3. writes `publications/publications.yml` plus the APA-formatted lists used on
   the publications page, the home page and the project pages,
4. re-renders the site, commits the result and publishes it.

To add a missing paper, hide a wrong one or correct an entry, edit
`publications/config.yml` (sections `add`, `exclude` and `overrides`). The
`projects` section of that file chooses which publications appear on which
project page. Pushing a change to that file starts the workflow; you can also
start it by hand under *Actions → Update publications → Run workflow*.

To run the updater locally:

```bash
pip install -r scripts/requirements.txt
python scripts/update_publications.py            # fetch and write
python scripts/update_publications.py --offline  # re-render from publications.yml
```

## Design

- Theme: `theme.scss` (fonts are self-hosted in `assets/fonts`)
- Animations: `assets/js/site-motion.js`, built on [Motion](https://motion.dev)
  (self-hosted in `assets/js`)
- Animated project figures: `projects/_figures/*.md` (inline SVG)
