"""HTML/JSON parsers for Devpost pages.

Page shapes (verified 2026-09):
- Hackathon API: {"hackathons": [...], "meta": {"meta": {"total_count", "per_page"}}}
- Project gallery pages: div.gallery-item[data-software-id] cards
- Project detail pages: server-rendered HTML (h1#app-title, #built-with, nav.app-links, ...)
"""
import json
import re

from bs4 import BeautifulSoup

SLUG_RE = re.compile(r"devpost\.com/software/([^/?#]+)")
SUBDOMAIN_RE = re.compile(r"https://([a-z0-9-]+)\.devpost\.com")
NUM_RE = re.compile(r"[\d,]+")

# Utility subdomains that appear in page nav/footer links, not hackathons.
NON_HACK_SUBDOMAINS = {
    "help", "info", "secure", "www", "blog", "forum", "status", "jobs",
    "api", "cdn", "assets", "static", "media", "docs", "getstarted",
}

DESCRIPTION_EXCLUDES = (
    "#software-header", "#built-with", "nav.app-links", "#app-team",
    "#comments", "#disqus_thread", "footer", ".side-unit", ".software-likes",
    "script", "style", ".reveal-modal", "form",
)


def parse_count(text: str) -> int:
    """'1,234' -> 1234, '+ 1' -> 1, '' -> 0. Handles '1.2k' style too."""
    if not text:
        return 0
    t = text.strip().replace(",", "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*k", t, re.I)
    if m:
        return int(float(m.group(1)) * 1000)
    m = re.search(r"(\d+(?:\.\d+)?)\s*m", t, re.I)
    if m:
        return int(float(m.group(1)) * 1_000_000)
    m = NUM_RE.search(t)
    return int(m.group(0).replace(",", "")) if m else 0


def slug_from_url(url: str) -> str | None:
    m = SLUG_RE.search(url or "")
    return m.group(1) if m else None


def parse_hackathon_api(payload: dict) -> tuple[list[dict], int]:
    """Returns (hackathons, total_count)."""
    items = payload.get("hackathons", [])
    meta = payload.get("meta") or {}
    total = meta.get("total_count", 0) if isinstance(meta, dict) else 0
    out = []
    for h in items:
        url = h.get("url") or ""
        m = SUBDOMAIN_RE.match(url)
        out.append({
            "id": h.get("id"),
            "title": h.get("title", ""),
            "slug": m.group(1) if m else None,
            "url": url,
            "gallery_url": h.get("submission_gallery_url") or "",
            "open_state": h.get("open_state", ""),
            "submission_dates": h.get("submission_period_dates", ""),
            "prize_amount": re.sub(r"<[^>]+>", "", h.get("prize_amount", "") or ""),
            "registrations_count": h.get("registrations_count") or 0,
            "winners_announced": 1 if h.get("winners_announced") else 0,
            "invite_only": 1 if h.get("invite_only") else 0,
            "themes": ", ".join(t.get("name", "") for t in h.get("themes", [])),
            "org": h.get("organization_name") or "",
        })
    return out, int(total)


def parse_gallery_cards(html: str) -> list[dict]:
    """Parse project cards out of a hackathon project-gallery page."""
    soup = BeautifulSoup(html, "html.parser")
    cards = []
    for item in soup.select("div.gallery-item[data-software-id]"):
        link = item.select_one("a.link-to-software[href]")
        if not link:
            continue
        slug = slug_from_url(link.get("href", ""))
        if not slug:
            continue
        title_el = item.select_one(".software-entry-name h5")
        thumb = item.select_one("figure img[alt]")
        tagline_el = item.select_one("p.tagline")
        likes_el = item.select_one(".like-count")
        comments_el = item.select_one(".comment-count")
        members = [img.get("title") or img.get("alt", "")
                   for img in item.select(".members .user-profile-link img")]
        cards.append({
            "software_id": int(item["data-software-id"]) if item["data-software-id"].isdigit() else None,
            "slug": slug,
            "title": (title_el.get_text(strip=True) if title_el
                      else (thumb.get("alt", "").strip() if thumb else "")),
            "tagline": tagline_el.get_text(strip=True) if tagline_el else "",
            "likes": parse_count(likes_el.get_text()) if likes_el else 0,
            "comments": parse_count(comments_el.get_text()) if comments_el else 0,
            "winner_badge": 1 if item.select_one("aside.entry-badge img.winner") else 0,
            "members": [m for m in members if m],
        })
    return cards


def _extract_description(soup: BeautifulSoup) -> str:
    import copy
    container = soup.select_one("section#container") or soup.body or soup
    tree = copy.copy(container)
    for sel in DESCRIPTION_EXCLUDES:
        for el in tree.select(sel):
            el.decompose()
    lines = (ln.strip() for ln in tree.get_text("\n").splitlines())
    return "\n".join(ln for ln in lines if ln)[:8000]


def parse_project_page(html: str, slug: str) -> dict:
    """Parse a project detail page (https://devpost.com/software/<slug>)."""
    soup = BeautifulSoup(html, "html.parser")

    title_el = soup.select_one("h1#app-title")
    tagline_el = soup.select_one("#software-header p.large")
    if not tagline_el:
        og = soup.select_one('meta[property="og:description"]')
        tagline_el = og

    # External links ("Try it out" nav)
    urls = [a["href"] for a in soup.select("nav.app-links ul[data-role='software-urls'] a[href]")]
    github = next((u for u in urls if "github.com" in u), "")
    demo = next((u for u in urls if "github.com" not in u and "devpost.com" not in u), "")

    video_el = soup.select_one("iframe.video-embed[src]")

    # Likes: "549 people like this" or a side-count next to the like button
    likes = 0
    likes_el = soup.select_one(".like-counts") or soup.select_one(".like-button .side-count")
    if likes_el:
        likes = parse_count(likes_el.get_text())

    # Winner placements: <span class="winner label">Winner</span> 1st Place
    winner_parts = []
    for w in soup.select(".winner.label"):
        holder = w.find_parent("li") or w.find_parent("div")
        if holder:
            txt = " ".join(holder.get_text(" ").split())
            if txt and txt not in winner_parts:
                winner_parts.append(txt)

    # Hackathons listed on the page (sidebar + winner blocks)
    hack_slugs = sorted({m.group(1) for a in soup.select("a[href]")
                         for m in [SUBDOMAIN_RE.match(a.get("href", ""))] if m
                         and m.group(1) not in NON_HACK_SUBDOMAINS})

    team = []
    for img in soup.select("#app-team .user-profile-link img"):
        name = img.get("alt", "").strip() or img.get("title", "").strip()
        if name:
            team.append(name)

    tech = [el.get_text(strip=True) for el in soup.select("#built-with .cp-tag")]
    tech = sorted({t for t in tech if t})

    return {
        "slug": slug,
        "title": title_el.get_text(strip=True) if title_el else "",
        "tagline": tagline_el.get_text(" ", strip=True) if tagline_el else "",
        "description": _extract_description(soup),
        "tech": tech,
        "urls": urls,
        "github_url": github,
        "demo_url": demo,
        "video_url": video_el["src"] if video_el else "",
        "likes": likes,
        "winner_label": " | ".join(winner_parts)[:300],
        "hackathon_slugs": hack_slugs,
        "team": team,
    }
