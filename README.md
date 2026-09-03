# Listings for Linda

A personal apartment-hunting tool: scrapes Booli.se listings near Malmö C and shows
them in a sortable table with photo galleries, favorites, and comments.

**Live site:** https://jba-1987.github.io/listings-for-linda/

## How it's built

- `scraper/scrape_booli.py` — Python + Playwright script that scrapes Booli.se.
  Booli is behind Cloudflare bot-protection, so this drives a real (headless)
  browser rather than raw HTTP requests, and reads listing data out of the
  page's embedded Apollo/Next.js state (`window.__NEXT_DATA__`) instead of
  parsing HTML. Runs at a light, human-like pace (delay between page loads) —
  this is a personal tool, not a bulk crawler, and Booli's ToS doesn't
  officially support scraping, so keep usage light and infrequent.
- `data/listings.json` — scraper output: `{ scraped_at, criteria, listings: [...] }`.
- `webapp/index.html` — the actual table UI (vanilla HTML/JS/CSS, no build step).
  Fetches `../data/listings.json` relative to itself.
- `docs/` — the deployed copy (GitHub Pages serves from here). It's a **copy**
  of `webapp/index.html` + `data/listings.json` with one fetch-path tweak
  (`data/listings.json` instead of `../data/listings.json`) — see "Deploying" below.

## Current search criteria

- Apartments (`Lägenhet`), 2–3 rooms, within **3.5 km** of Malmö Central Station
  (55.60906, 13.00074), municipality areaId `78`.
- Price between **1,000,000 and 3,000,000 kr**.
- Monthly fee (avgift) at most **4,600 kr/month**.
- Excludes the neighborhoods **Rosengård, Lindängen, and Kirseberg**.
- Excludes listings where the housing cooperative holds the land via
  **tomträtt** (site leasehold) rather than owning it outright. Booli doesn't
  expose this in its normal listing data — the scraper gets it by clicking
  the real "Föreningen" tab on each listing's detail page (a direct API call
  to that same endpoint gets 403'd by Cloudflare even from a genuine
  Playwright session, so it has to be a real UI interaction). When Booli has
  no plot-status info for a listing at all, it's kept (unverified, not
  assumed to fail the check).

Change any of these via CLI flags on the scraper (see `--help`), or edit the
defaults in `scrape_booli.py`.

## Re-running the scraper

```bash
cd scraper
python3 scrape_booli.py --delay 1.5 --out ../data/listings.json
```

- Because the fee and tomträtt checks both require visiting each listing's
  detail page, the scraper now detail-fetches **every** listing that passes
  the cheap filters (radius/price/area) rather than just the newest N — pass
  `--max-detail N` to cap that for a faster/smaller test run.
- Takes a while (~30-45 min for the full run) — it has to page through every
  matching listing in Malmö kommun first (Booli only filters by municipality,
  not by an arbitrary point or price), then visit each survivor's detail page.
- Needs Playwright installed (`pip install playwright && playwright install chromium`).

## Testing locally

```bash
python3 -m http.server 8643   # from the apartment-finder/ root
```
Then open `http://localhost:8643/webapp/index.html`. (Opening the HTML file
directly via `file://` won't work — the `fetch()` call needs a real HTTP server.)

## Deploying (publishing changes to the live site)

The live site is served from the `docs/` folder via GitHub Pages
(`.nojekyll` is required there — GitHub's default Jekyll build fails on our
large JSON file otherwise). After editing `webapp/index.html` or re-running
the scraper:

```bash
cp webapp/index.html docs/index.html
sed -i '' "s|fetch('../data/listings.json|fetch('data/listings.json|" docs/index.html
cp data/listings.json docs/data/listings.json   # if data changed
git add docs data
git commit -m "..."
git push
```

GitHub Pages rebuilds automatically after a push (usually live within
30–60 seconds).

## Notes / known quirks

- The site is on a **public** GitHub repo, so the deployed URL and repo
  contents (including the header photo) are visible to anyone with the link —
  not password-protected. If you want real privacy, Cloudflare Pages +
  Cloudflare Access (free) is the way to go; ask Claude to help set it up.
- Favorites and comments are stored in the browser's `localStorage`, per
  browser/device — they don't sync between people or devices.
- "Distance" in the data is straight-line (as-the-crow-flies) from Malmö C,
  not driving/walking distance.
- The UI (`webapp/index.html`) is in **Spanish**. Numbers/currency use
  `es-ES` locale formatting (period as thousand separator).
