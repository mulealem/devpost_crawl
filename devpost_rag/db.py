"""SQLite storage. The database is the source of truth; the vector index is derived."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS hackathons(
  id INTEGER PRIMARY KEY,
  title TEXT, slug TEXT UNIQUE, url TEXT, gallery_url TEXT,
  open_state TEXT, submission_dates TEXT, prize_amount TEXT,
  registrations_count INTEGER DEFAULT 0,
  winners_announced INTEGER DEFAULT 0, invite_only INTEGER DEFAULT 0,
  themes TEXT, org TEXT,
  fetched_at TEXT,
  gallery_pages_done INTEGER DEFAULT 0,
  gallery_done INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS projects(
  slug TEXT PRIMARY KEY,
  software_id INTEGER,
  title TEXT DEFAULT '', tagline TEXT DEFAULT '',
  description TEXT DEFAULT '',
  tech TEXT DEFAULT '[]',
  urls TEXT DEFAULT '[]',
  github_url TEXT DEFAULT '', demo_url TEXT DEFAULT '', video_url TEXT DEFAULT '',
  likes INTEGER DEFAULT 0, comments INTEGER DEFAULT 0,
  winner_label TEXT DEFAULT '',
  team TEXT DEFAULT '[]',
  has_github INTEGER DEFAULT 0, has_demo INTEGER DEFAULT 0, has_video INTEGER DEFAULT 0,
  listing_source TEXT DEFAULT '',
  detail_fetched INTEGER DEFAULT 0,
  first_seen TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS project_hackathons(
  project_slug TEXT, hack_slug TEXT, winner INTEGER DEFAULT 0,
  PRIMARY KEY(project_slug, hack_slug)
);
CREATE TABLE IF NOT EXISTS indexed_slugs(
  slug TEXT PRIMARY KEY, indexed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_projects_detail ON projects(detail_fetched);
CREATE INDEX IF NOT EXISTS idx_projects_likes ON projects(likes DESC);
"""


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def upsert_hackathon(conn: sqlite3.Connection, h: dict) -> None:
    conn.execute(
        """INSERT INTO hackathons(id, title, slug, url, gallery_url, open_state,
               submission_dates, prize_amount, registrations_count,
               winners_announced, invite_only, themes, org, fetched_at)
           VALUES(:id,:title,:slug,:url,:gallery_url,:open_state,
               :submission_dates,:prize_amount,:registrations_count,
               :winners_announced,:invite_only,:themes,:org,:fetched_at)
           ON CONFLICT(id) DO UPDATE SET
               title=excluded.title, url=excluded.url, gallery_url=excluded.gallery_url,
               open_state=excluded.open_state, submission_dates=excluded.submission_dates,
               prize_amount=excluded.prize_amount,
               registrations_count=excluded.registrations_count,
               winners_announced=excluded.winners_announced,
               invite_only=excluded.invite_only, themes=excluded.themes,
               org=excluded.org, fetched_at=excluded.fetched_at""",
        {**h, "fetched_at": now()},
    )


def upsert_project_listing(conn: sqlite3.Connection, card: dict, hack_slug: str) -> bool:
    """Insert/refresh a project from gallery listing data. True if new."""
    ts = now()
    cur = conn.execute("SELECT 1 FROM projects WHERE slug=?", (card["slug"],))
    existed = cur.fetchone() is not None
    conn.execute(
        """INSERT INTO projects(slug, software_id, title, tagline, likes, comments,
               listing_source, first_seen, updated_at)
           VALUES(:slug,:software_id,:title,:tagline,:likes,:comments,:listing_source,:first_seen,:updated_at)
           ON CONFLICT(slug) DO UPDATE SET
               software_id=COALESCE(excluded.software_id, projects.software_id),
               title=CASE WHEN excluded.title != '' THEN excluded.title ELSE projects.title END,
               tagline=CASE WHEN excluded.tagline != '' THEN excluded.tagline ELSE projects.tagline END,
               likes=MAX(projects.likes, excluded.likes),
               comments=MAX(projects.comments, excluded.comments),
               updated_at=excluded.updated_at""",
        {
            "slug": card["slug"], "software_id": card.get("software_id"),
            "title": card.get("title", ""), "tagline": card.get("tagline", ""),
            "likes": card.get("likes", 0), "comments": card.get("comments", 0),
            "listing_source": hack_slug, "first_seen": ts, "updated_at": ts,
        },
    )
    conn.execute(
        """INSERT INTO project_hackathons(project_slug, hack_slug, winner) VALUES(?,?,?)
           ON CONFLICT(project_slug, hack_slug) DO UPDATE SET winner=MAX(winner, excluded.winner)""",
        (card["slug"], hack_slug, card.get("winner_badge", 0)),
    )
    return not existed


def apply_project_detail(conn: sqlite3.Connection, d: dict) -> None:
    ts = now()
    for slug in d.get("hackathon_slugs", []):
        conn.execute(
            "INSERT OR IGNORE INTO project_hackathons(project_slug, hack_slug, winner) VALUES(?,?,0)",
            (d["slug"], slug),
        )
    conn.execute(
        """UPDATE projects SET
               title=CASE WHEN ? != '' THEN ? ELSE title END,
               tagline=CASE WHEN ? != '' THEN ? ELSE tagline END,
               description=?, tech=?, urls=?, github_url=?, demo_url=?, video_url=?,
               likes=MAX(likes, ?),
               winner_label=CASE WHEN ? != '' THEN ? ELSE winner_label END,
               team=?,
               has_github=?, has_demo=?, has_video=?,
               detail_fetched=1, updated_at=?
           WHERE slug=?""",
        (
            d.get("title", ""), d.get("title", ""),
            d.get("tagline", ""), d.get("tagline", ""),
            d.get("description", ""),
            json.dumps(d.get("tech", [])),
            json.dumps(d.get("urls", [])),
            d.get("github_url", ""), d.get("demo_url", ""), d.get("video_url", ""),
            d.get("likes", 0),
            d.get("winner_label", ""), d.get("winner_label", ""),
            json.dumps(d.get("team", [])),
            1 if d.get("github_url") else 0,
            1 if d.get("demo_url") else 0,
            1 if d.get("video_url") else 0,
            ts, d["slug"],
        ),
    )


def project_row(conn: sqlite3.Connection, slug: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM projects WHERE slug=?", (slug,)).fetchone()


def pending_hackathons(conn: sqlite3.Connection, limit: int | None = None,
                       only_undone: bool = True) -> list[sqlite3.Row]:
    q = ("SELECT * FROM hackathons WHERE gallery_done=0" if only_undone
         else "SELECT * FROM hackathons")
    q += " ORDER BY registrations_count DESC, id DESC"
    if limit:
        q += f" LIMIT {int(limit)}"
    return conn.execute(q).fetchall()


def pending_details(conn: sqlite3.Connection, limit: int, order: str) -> list[sqlite3.Row]:
    order_by = {
        "attention": "likes DESC, comments DESC",
        "low_attention": "likes ASC, comments ASC",
        "random": "RANDOM()",
        "newest": "first_seen DESC",
    }[order]
    return conn.execute(
        f"SELECT * FROM projects WHERE detail_fetched=0 ORDER BY {order_by} LIMIT ?",
        (int(limit),),
    ).fetchall()
