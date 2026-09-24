"""Crawlers: hackathon discovery (JSON API), project galleries, detail pages."""
import json

from . import db
from .config import HACKATHON_API, HACKATHONS_PER_PAGE
from .fetch import FetchError, Fetcher
from .parse import parse_gallery_cards, parse_hackathon_api, parse_project_page


def gallery_base_url(h) -> str:
    base = (h["gallery_url"] or "").strip()
    if not base:
        base = (h["url"] or "").rstrip("/") + "/project-gallery"
    return base.split("?")[0].rstrip("/")


def crawl_hackathons(fetcher: Fetcher, conn, statuses: list[str] | None = None,
                     max_pages: int | None = None) -> int:
    """Pull the hackathon list from the JSON API. statuses=None means 'everything the API exposes'."""
    total_seen = {}

    def crawl_query(status: str | None) -> int:
        page = 1
        added = 0
        total = None
        while True:
            q = f"?per_page={HACKATHONS_PER_PAGE}&page={page}"
            if status:
                q += f"&status[]={status}"
            resp = fetcher.get(HACKATHON_API + q, accept_json=True)
            payload = resp.json()
            items, total = parse_hackathon_api(payload)
            if not items:
                break
            for h in items:
                if h["slug"]:
                    db.upsert_hackathon(conn, h)
                    added += 1
            conn.commit()
            print(f"  [{status or 'all'}] page {page}: +{len(items)} (total on server: {total})")
            if max_pages and page >= max_pages:
                break
            if total and page * HACKATHONS_PER_PAGE >= total:
                break
            page += 1
        total_seen[status or "all"] = added
        return added

    if statuses:
        n = sum(crawl_query(s) for s in statuses)
    else:
        n = crawl_query(None)
    conn.commit()
    print(f"hackathons upserted: {n}")
    return n


def crawl_galleries(fetcher: Fetcher, conn, hackathon_slug: str | None = None,
                    max_hackathons: int | None = None, max_pages_per: int | None = None) -> dict:
    """Walk project galleries. Resumable: per-hackathon page progress is stored."""
    if hackathon_slug:
        rows = conn.execute("SELECT * FROM hackathons WHERE slug=?", (hackathon_slug,)).fetchall()
        if not rows:
            raise SystemExit(f"unknown hackathon slug: {hackathon_slug!r} "
                             f"(run crawl-hackathons first)")
    else:
        rows = db.pending_hackathons(conn, limit=max_hackathons)

    totals = {"hackathons": 0, "pages": 0, "cards": 0, "new": 0, "skipped": 0}
    for h in rows:
        base = gallery_base_url(h)
        start_page = h["gallery_pages_done"] + 1
        page = start_page
        new_for_hack = 0
        print(f"[{h['slug']}] {h['title'][:60]} (from page {start_page})")
        try:
            while True:
                if max_pages_per and (page - start_page) >= max_pages_per:
                    break
                try:
                    resp = fetcher.get(f"{base}?page={page}")
                except FetchError as exc:
                    print(f"    give up: {exc}")
                    totals["skipped"] += 1
                    break
                if resp is None:
                    totals["skipped"] += 1
                    break
                cards = parse_gallery_cards(resp.text)
                if not cards:
                    break
                new_this_page = 0
                for card in cards:
                    if db.upsert_project_listing(conn, card, h["slug"]):
                        new_this_page += 1
                conn.commit()
                totals["cards"] += len(cards)
                totals["new"] += new_this_page
                new_for_hack += new_this_page
                print(f"    page {page}: {len(cards)} cards (+{new_for_hack} new overall)")
                conn.execute("UPDATE hackathons SET gallery_pages_done=? WHERE id=?",
                             (page, h["id"]))
                conn.commit()
                totals["pages"] += 1
                page += 1
        except KeyboardInterrupt:
            print("interrupted - progress saved, safe to re-run")
            break
        if not (max_pages_per and max_pages_per > 0):
            conn.execute("UPDATE hackathons SET gallery_done=1 WHERE id=?", (h["id"],))
            conn.commit()
        totals["hackathons"] += 1
        print(f"    done: +{new_for_hack} projects")

    conn.commit()
    return totals


def enrich_details(fetcher: Fetcher, conn, limit: int, order: str = "random",
                   slugs: list[str] | None = None) -> dict:
    """Scrape project detail pages (description, tech, links, team, wins)."""
    if slugs:
        rows = [db.project_row(conn, s) for s in slugs]
        rows = [r for r in rows if r and not r["detail_fetched"]]
    else:
        rows = db.pending_details(conn, limit, order)

    stats = {"fetched": 0, "missing": 0, "errors": 0}
    for i, row in enumerate(rows, 1):
        url = f"https://devpost.com/software/{row['slug']}"
        try:
            resp = fetcher.get(url)
        except FetchError as exc:
            print(f"    [{i}/{len(rows)}] {row['slug']}: {exc}")
            stats["errors"] += 1
            continue
        if resp is None:
            print(f"    [{i}/{len(rows)}] {row['slug']}: 404 (deleted?)")
            stats["missing"] += 1
            continue
        detail = parse_project_page(resp.text, row["slug"])
        detail["likes"] = max(detail.get("likes", 0), row["likes"])
        db.apply_project_detail(conn, detail)
        conn.commit()
        stats["fetched"] += 1
        if i % 10 == 0 or i == len(rows):
            print(f"    scraped {i}/{len(rows)} (last: {row['slug']})")
    return stats


def dump_project(row) -> dict:
    return {
        "slug": row["slug"], "title": row["title"], "tagline": row["tagline"],
        "likes": row["likes"], "comments": row["comments"],
        "tech": json.loads(row["tech"] or "[]"),
        "github_url": row["github_url"], "demo_url": row["demo_url"],
        "video_url": row["video_url"],
        "winner_label": row["winner_label"], "detail_fetched": row["detail_fetched"],
        "url": f"https://devpost.com/software/{row['slug']}",
    }
