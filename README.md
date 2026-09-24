# Devpost Scraper

Crawls **all of devpost.com** — every hackathon and every project — into a local
SQLite database. Verified working September 2026.

## How it works

Devpost's site search (`/software/search?query=...`) is locked behind an AWS WAF
JavaScript challenge, so this scraper takes the structured route instead, which
also happens to give **full coverage**:

1. **Hackathon discovery** — the public JSON API
   (`https://devpost.com/api/hackathons`) exposes every hackathon
   (~13,900 as of 2026-09, 40 per page, ~350 requests total).
2. **Project galleries** — every hackathon has a paginated project gallery
   (`<hackathon>.devpost.com/project-gallery?page=N`). Each card yields: slug,
   title, tagline, likes, comments, winner badge, team members.
3. **Detail pages** — `devpost.com/software/<slug>` yields the full description,
   built-with tech tags, GitHub/demo/video links, like count, prize placements,
   team, and associated hackathons.

Everything lands in `data/devpost.db` (SQLite). All commands are **resumable** —
interrupt with Ctrl-C and re-run; per-hackathon gallery progress and per-project
detail state are tracked in the database.

## Install

```bash
python -m venv .venv
.venv/Scripts/pip install -e .        # Windows
# .venv/bin/pip install -e .          # Linux/macOS
```

Or without installing: `pip install requests beautifulsoup4` and run
`python -m devpost_rag ...` from this directory.

## Usage

```bash
# 1. discover hackathons (everything the API exposes, ~350 pages)
python -m devpost_rag crawl-hackathons

#    ...or just open/upcoming ones, capped:
python -m devpost_rag crawl-hackathons --statuses open,upcoming --max-pages 10

# 2. crawl project galleries (all hackathons, resumable)
python -m devpost_rag crawl-projects
#    scope it:
python -m devpost_rag crawl-projects --hackathon revenuecat-shipaton-2026
python -m devpost_rag crawl-projects --max-hackathons 20 --max-pages 5

# 3. scrape detail pages for the projects you care about
python -m devpost_rag enrich --limit 1000 --order low_attention
python -m devpost_rag enrich --slugs audionova,some-other-slug

# 4. inspect / export
python -m devpost_rag stats
python -m devpost_rag export projects.jsonl
python -m devpost_rag export projects.csv --details-only
```

The database is the deliverable — query it directly:

```sql
-- topLiked projects
SELECT title, likes, demo_url, github_url FROM projects ORDER BY likes DESC LIMIT 20;

-- winners with low attention (fewer than 10 likes)
SELECT title, winner_label, likes FROM projects
WHERE winner_label != '' AND likes < 10 ORDER BY winner_label;

-- all projects from one hackathon
SELECT p.* FROM projects p
JOIN project_hackathons ph ON ph.project_slug = p.slug
WHERE ph.hack_slug = 'revenuecat-shipaton-2026';
```

## Scale expectations (default 1.2 s/request)

| Stage                        | Requests            | Wall time (rough) |
|------------------------------|---------------------|-------------------|
| Hackathon discovery          | ~350                | ~10 min           |
| All galleries (listing data) | ~100k–400k          | 1.5–6 days        |
| All detail pages             | ~1 per project (M+) | weeks             |

Realistic strategy: run 1+2 fully (you get every project's title, tagline,
likes, winner badges), then `enrich` selectively — the `export`/SQL layer is
already useful with listing data alone.

Speed is controlled by `DEVPOST_RAG_DELAY` (seconds between requests per host);
**don't go below ~0.5** — the site WAF-throttles bursts, and this data is
publicly served by a small team.

## Notes on legality/politeness

- `devpost.com/robots.txt` (checked 2026-09) allows general crawlers; it only
  bans a handful of specific commercial bots (Bytespider, BLEXBot, ...).
- Requests use browser-shaped headers because AWS WAF fingerprints plain bots;
  the fetcher detects challenge pages and backs off exponentially instead of
  hammering.
- This is public data for personal research/aggregation. Don't republish the
  corpus wholesale; Devpost's ToS govern commercial/rehosting use.

## Layout

```
devpost_rag/
  config.py   paths + politeness knobs
  fetch.py    throttled, WAF-aware HTTP fetcher
  parse.py    HTML/JSON parsers (gallery cards, project pages, API)
  db.py       SQLite schema + upserts
  crawl.py    the three crawlers
  __main__.py CLI (dpr / python -m devpost_rag)
data/
  devpost.db  the scraped corpus
```
