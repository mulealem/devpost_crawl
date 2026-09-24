"""Command-line interface: `dpr` (or `python -m devpost_rag`).

Commands
  crawl-hackathons   discover hackathons via the JSON API
  crawl-projects     walk hackathon project galleries, collecting project cards
  enrich             scrape project detail pages (description, tech, links, team)
  stats              database summary
  export             dump projects to JSONL / CSV
"""
import argparse
import json
import sqlite3
import sys

from . import __version__, crawl, db
from .fetch import Fetcher


def _open_db() -> sqlite3.Connection:
    return db.connect()


def cmd_crawl_hackathons(args) -> None:
    statuses = [s.strip() for s in args.statuses.split(",")] if args.statuses else None
    fetcher = Fetcher()
    crawl.crawl_hackathons(fetcher, _open_db(), statuses=statuses, max_pages=args.max_pages)
    print(f"fetch stats: {fetcher.stats}")


def cmd_crawl_projects(args) -> None:
    fetcher = Fetcher()
    totals = crawl.crawl_galleries(
        fetcher, _open_db(),
        hackathon_slug=args.hackathon,
        max_hackathons=args.max_hackathons,
        max_pages_per=args.max_pages,
    )
    print(f"done: {totals}")
    print(f"fetch stats: {fetcher.stats}")


def cmd_enrich(args) -> None:
    fetcher = Fetcher()
    slugs = [s.strip() for s in args.slugs.split(",")] if args.slugs else None
    stats = crawl.enrich_details(
        fetcher, _open_db(), limit=args.limit, order=args.order, slugs=slugs,
    )
    print(f"done: {stats}")
    print(f"fetch stats: {fetcher.stats}")


def cmd_stats(args) -> None:
    conn = _open_db()
    h = conn.execute("SELECT COUNT(*) c FROM hackathons").fetchone()["c"]
    h_done = conn.execute("SELECT COUNT(*) c FROM hackathons WHERE gallery_done=1").fetchone()["c"]
    p = conn.execute("SELECT COUNT(*) c FROM projects").fetchone()["c"]
    p_det = conn.execute("SELECT COUNT(*) c FROM projects WHERE detail_fetched=1").fetchone()["c"]
    winners = conn.execute(
        "SELECT COUNT(*) c FROM projects WHERE winner_label != ''").fetchone()["c"]
    with_github = conn.execute(
        "SELECT COUNT(*) c FROM projects WHERE has_github=1").fetchone()["c"]
    print(f"hackathons:        {h} ({h_done} galleries fully crawled)")
    print(f"projects:          {p}")
    print(f"  detail scraped:  {p_det}")
    print(f"  with github:     {with_github}")
    print(f"  prize winners:   {winners}")
    top = conn.execute(
        "SELECT slug, title, likes FROM projects ORDER BY likes DESC LIMIT 5").fetchall()
    if top:
        print("most liked:")
        for r in top:
            print(f"  {r['likes']:>5}  {r['title'][:60]}  (devpost.com/software/{r['slug']})")


def cmd_export(args) -> None:
    conn = _open_db()
    where = "WHERE detail_fetched=1" if args.details_only else ""
    rows = conn.execute(f"SELECT * FROM projects {where}").fetchall()
    out_path = args.out
    with open(out_path, "w", encoding="utf-8") as fh:
        if out_path.endswith(".csv"):
            import csv
            cols = ["slug", "title", "tagline", "likes", "comments", "tech",
                    "github_url", "demo_url", "video_url", "winner_label",
                    "description", "url"]
            w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                d = crawl.dump_project(r)
                d["tech"] = ", ".join(d["tech"])
                d["url"] = f"https://devpost.com/software/{r['slug']}"
                w.writerow(d)
        else:
            for r in rows:
                fh.write(json.dumps(crawl.dump_project(r), ensure_ascii=False) + "\n")
    print(f"exported {len(rows)} projects -> {out_path}")


def main(argv: list[str] | None = None) -> None:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(
        prog="dpr",
        description="Crawl devpost.com hackathons and projects into SQLite.",
    )
    ap.add_argument("--version", action="version", version=f"dpr {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("crawl-hackathons", help="discover hackathons via the JSON API")
    p.add_argument("--statuses", default="",
                   help="comma list, e.g. open,upcoming (default: everything the API exposes)")
    p.add_argument("--max-pages", type=int, default=None, help="stop after N API pages")
    p.set_defaults(func=cmd_crawl_hackathons)

    p = sub.add_parser("crawl-projects", help="walk project galleries")
    p.add_argument("--hackathon", default=None, help="crawl one hackathon by slug")
    p.add_argument("--max-hackathons", type=int, default=None)
    p.add_argument("--max-pages", type=int, default=None,
                   help="max gallery pages per hackathon (default: until exhausted)")
    p.set_defaults(func=cmd_crawl_projects)

    p = sub.add_parser("enrich", help="scrape project detail pages")
    p.add_argument("--limit", type=int, default=500)
    p.add_argument("--order", default="random",
                   choices=["random", "attention", "low_attention", "newest"])
    p.add_argument("--slugs", default="",
                   help="comma-separated project slugs to scrape instead of --limit batch")
    p.set_defaults(func=cmd_enrich)

    sub.add_parser("stats", help="database summary").set_defaults(func=cmd_stats)

    p = sub.add_parser("export", help="dump projects to .jsonl or .csv")
    p.add_argument("out", help="output path, e.g. projects.jsonl or projects.csv")
    p.add_argument("--details-only", action="store_true",
                   help="only rows whose detail page was scraped")
    p.set_defaults(func=cmd_export)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
