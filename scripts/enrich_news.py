#!/usr/bin/env python3
"""
Turn the raw notification titles in news.json into something a teacher can act on.

A title like "NOTIFICATION - Promotion of ASDEOs/ADEOs (EMC BS-16) to SDEOS/
Assistant Directors (Female EMC BPS-17) on Regular basis" tells a PST in Kulachi
nothing about whether it concerns them. This script asks Claude to add, for each
item: a plain-English summary, an Urdu line, who it affects, what to do about it,
and how urgent it is — plus one short digest for the whole day.

The API key is read from the ANTHROPIC_API_KEY environment variable, which in
this project comes from a GitHub Actions secret. It is never written into
news.json and never reaches the browser.

If the key is missing or the call fails, the script exits cleanly and leaves
news.json exactly as it was. The app renders fine without enrichment.

Run:  python3 scripts/enrich_news.py
"""

import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEWS = os.path.join(ROOT, "news.json")

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
# Haiku is the cheapest current model and this task is simple classification
# and rewriting. Override with MODEL=claude-sonnet-5 if you want richer prose.
MODEL = os.environ.get("MODEL", "claude-haiku-4-5-20251001")
MAX_ITEMS = 15
TIMEOUT = 90

SYSTEM = """You brief government primary and secondary school teachers in Khyber Pakhtunkhwa on notifications from their Elementary & Secondary Education Department.

Your readers are PST, CT, SST and AT teachers, and school heads. Many read English as a second language and are on a phone with poor signal. Write plainly. No officialese, no filler.

For each notification you receive, produce:
- summary: one sentence, max 22 words, saying what the notification actually does. Plain English.
- ur: the same thing in simple Urdu, max 18 words.
- who: who it concerns, as a short phrase. Be specific about cadre, BPS, zone or district when the title says so. Use "All staff" only when it truly applies to everyone.
- action: what the reader should do, max 12 words. If there is nothing to do, write "No action - for information".
- urgency: exactly one of "action" (the reader may need to apply, respond or comply), "watch" (may affect them soon), or "info" (background only).

Also produce one "digest": two sentences, max 45 words, telling an ordinary classroom teacher what actually matters in this batch and what does not. Speak directly to them.

Rules:
- Never invent dates, numbers, eligibility or deadlines that are not in the title given to you. If the title is vague, say so plainly rather than guessing.
- Do not tell anyone they are eligible or ineligible for anything. Point them at the notification.
- Return ONLY a JSON object. No preamble, no markdown fences.

Return this shape exactly:
{"digest": "...", "items": [{"i": 0, "summary": "...", "ur": "...", "who": "...", "action": "...", "urgency": "..."}]}
"""


def call_claude(api_key, items):
    listing = "\n".join(
        f'{n}. [{it.get("cat","Notice")}] [{it.get("date","")}] {it["title"]}'
        for n, it in enumerate(items)
    )
    body = {
        "model": MODEL,
        "max_tokens": 3000,
        "system": SYSTEM,
        "messages": [{
            "role": "user",
            "content": f"Brief these {len(items)} notifications:\n\n{listing}"
        }],
    }
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode(),
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": API_VERSION,
        },
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        data = json.loads(r.read().decode())

    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    usage = data.get("usage", {})
    return json.loads(text), usage


def main():
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        print("ANTHROPIC_API_KEY not set — skipping AI enrichment.")
        print("news.json left unchanged; the app works without it.")
        return 0

    if not os.path.exists(NEWS):
        print("news.json not found — run scripts/fetch_news.py first.", file=sys.stderr)
        return 1

    feed = json.load(open(NEWS, encoding="utf-8"))
    items = feed.get("items", [])
    if not items:
        print("No items to enrich.")
        return 0

    batch = items[:MAX_ITEMS]
    print(f"Briefing {len(batch)} notifications with {MODEL}...")

    try:
        result, usage = call_claude(key, batch)
    except urllib.error.HTTPError as e:
        print(f"API error {e.code}: {e.read().decode()[:300]}", file=sys.stderr)
        print("Leaving news.json unchanged.", file=sys.stderr)
        return 0
    except Exception as e:
        print(f"Enrichment failed: {e}", file=sys.stderr)
        print("Leaving news.json unchanged.", file=sys.stderr)
        return 0

    by_index = {int(r["i"]): r for r in result.get("items", []) if "i" in r}
    enriched = 0
    for n, it in enumerate(batch):
        r = by_index.get(n)
        if not r:
            continue
        it["summary"] = r.get("summary", "")
        it["ur"] = r.get("ur", "")
        it["who"] = r.get("who", "")
        it["action"] = r.get("action", "")
        it["urgency"] = r.get("urgency", "info")
        enriched += 1

    feed["digest"] = result.get("digest", "")
    feed["briefed"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    feed["model"] = MODEL

    with open(NEWS, "w", encoding="utf-8") as f:
        json.dump(feed, f, ensure_ascii=False, indent=1)

    ti = usage.get("input_tokens", 0)
    to = usage.get("output_tokens", 0)
    # Haiku 4.5 list price: $1 per MTok input, $5 per MTok output
    cost = ti / 1e6 * 1.0 + to / 1e6 * 5.0
    print(f"\nBriefed {enriched}/{len(batch)} notifications")
    print(f"Tokens: {ti} in, {to} out  (~${cost:.4f} this run, ~${cost*30:.2f}/month daily)")
    print(f"\nDigest: {feed['digest']}")
    for it in batch[:4]:
        if it.get("summary"):
            print(f"  [{it.get('urgency',''):6}] {it['summary']}")
            print(f"           who: {it.get('who','')} | do: {it.get('action','')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
