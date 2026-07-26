#!/usr/bin/env python3
"""
Fetch the latest notifications / news from kpese.gov.pk and write news.json.

Strategy:
  1. Try the WordPress REST API (kpese.gov.pk is a WordPress site).
     This is fast, structured and does not break when the theme changes.
  2. If the REST API is disabled or blocked, fall back to parsing the
     category listing pages with a plain regex — no external deps needed.
  3. If both fail, keep the existing news.json rather than writing an
     empty file. A stale feed is better than a blank one.

Run:  python3 scripts/fetch_news.py
Out:  news.json  (in the repo root)
"""

import json
import os
import re
import sys
import html
import urllib.request
import urllib.error
from datetime import datetime, timezone

BASE = "https://kpese.gov.pk"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "news.json")
UA = "Mozilla/5.0 (compatible; KPESED-HRIS-companion/1.0; +https://github.com/)"
TIMEOUT = 25
MAX_ITEMS = 30

# Category slug -> label shown in the app
CATEGORIES = [
    ("notifications", "Notification"),
    ("induction-program", "Induction"),
    ("initiatives", "Initiative"),
    ("news", "News"),
    ("careers", "Careers"),
    ("appointments", "Appointments"),
    ("seniority-list", "Seniority"),
    ("tender", "Tender"),
]


def get(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/json, text/html;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", "replace")


def clean(s):
    """Strip HTML tags and decode entities from a WordPress title."""
    s = re.sub(r"<[^>]+>", "", s or "")
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


# ---------------------------------------------------------------- REST API

def category_ids():
    """Map category slug -> numeric id via the REST API."""
    ids = {}
    try:
        data = json.loads(get(f"{BASE}/wp-json/wp/v2/categories?per_page=100&_fields=id,slug"))
        for c in data:
            ids[c.get("slug")] = c.get("id")
    except Exception as e:
        print(f"  categories lookup failed: {e}", file=sys.stderr)
    return ids


def via_rest():
    items = []
    ids = category_ids()
    if not ids:
        raise RuntimeError("no categories returned")

    for slug, label in CATEGORIES:
        cid = ids.get(slug)
        if not cid:
            continue
        url = (f"{BASE}/wp-json/wp/v2/posts?categories={cid}&per_page=8"
               f"&orderby=date&order=desc&_fields=date,link,title")
        try:
            posts = json.loads(get(url))
        except Exception as e:
            print(f"  {slug}: {e}", file=sys.stderr)
            continue
        for p in posts:
            title = clean((p.get("title") or {}).get("rendered", ""))
            link = p.get("link", "")
            date = (p.get("date") or "")[:10]
            if title and link:
                items.append({"title": title, "url": link, "date": date, "cat": label})
        print(f"  {slug}: {len(posts)} posts")
    return items


# ---------------------------------------------------------------- HTML fallback

POST_RE = re.compile(
    r'<h3[^>]*>\s*<a\s+href="(?P<url>https://kpese\.gov\.pk/[^"]+)"[^>]*>(?P<title>.*?)</a>',
    re.S | re.I,
)
DATE_RE = re.compile(r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),\s+(\d{4})")
MONTHS = {m: i + 1 for i, m in enumerate(
    "January February March April May June July August September October November December".split())}


def via_html():
    items = []
    for slug, label in CATEGORIES:
        try:
            page = get(f"{BASE}/category/{slug}/")
        except Exception as e:
            print(f"  {slug}: {e}", file=sys.stderr)
            continue
        # dates appear in document order alongside the post links
        dates = [f"{y}-{MONTHS[mo]:02d}-{int(d):02d}" for mo, d, y in DATE_RE.findall(page)]
        found = list(POST_RE.finditer(page))
        for i, m in enumerate(found[:8]):
            title = clean(m.group("title"))
            if not title:
                continue
            items.append({
                "title": title,
                "url": m.group("url"),
                "date": dates[i] if i < len(dates) else "",
                "cat": label,
            })
        print(f"  {slug}: {len(found)} posts (html)")
    return items


# ---------------------------------------------------------------- main

# Titles matching these get re-tagged so induction news is easy to spot in the app.
INDUCTION_RE = re.compile(r"\binduction\b|\bphase[- ]?(ii|iii|iv|v|vi|\d)\b|\bRPDC\b|\bToT\b|foundation course",
                          re.I)


def tag_induction(items):
    n = 0
    for it in items:
        if INDUCTION_RE.search(it.get("title", "")):
            it["cat"] = "Induction"
            n += 1
    if n:
        print(f"  tagged {n} induction item(s)")
    return items


def dedupe_and_sort(items):
    seen, out = set(), []
    for it in items:
        if it["url"] in seen:
            continue
        seen.add(it["url"])
        out.append(it)
    out.sort(key=lambda x: x.get("date") or "", reverse=True)
    return out[:MAX_ITEMS]


def main():
    items = []
    print("Trying WordPress REST API...")
    try:
        items = via_rest()
    except Exception as e:
        print(f"REST API unavailable ({e}); falling back to HTML", file=sys.stderr)

    if not items:
        print("Trying HTML listing pages...")
        try:
            items = via_html()
        except Exception as e:
            print(f"HTML fallback failed: {e}", file=sys.stderr)

    if not items:
        print("No items fetched — keeping the existing news.json", file=sys.stderr)
        return 1 if not os.path.exists(OUT) else 0

    items = dedupe_and_sort(tag_induction(items))
    payload = {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": BASE,
        "count": len(items),
        "items": items,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"\nWrote {OUT} with {len(items)} items")
    for it in items[:6]:
        print(f"  {it['date']}  [{it['cat']}]  {it['title'][:70]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
