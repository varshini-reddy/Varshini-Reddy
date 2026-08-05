"""
Sync publications into publications.json.

Google Scholar blocks datacenter IPs, so it cannot be scraped from GitHub
Actions (scholarly raises MaxTriesExceededException). This script uses
OpenAlex instead: an open catalogue with a public API, no key, no blocking.
Citation counts differ slightly from Scholar's — OpenAlex counts only
indexed sources.

    python scripts/sync_scholar.py

Set OPENALEX_AUTHOR_ID to pin a specific author record; otherwise the script
searches by name and picks the profile with the most works.

Safety: if the fetch returns fewer than MIN_WORKS entries, the script exits
without writing, so a bad API day cannot wipe a good publications.json.
"""

import json, re, os, sys, datetime, pathlib, urllib.parse, urllib.request

OUT = pathlib.Path(__file__).resolve().parent.parent / "publications.json"
AUTHOR_NAME = "Varshini Reddy"
AUTHOR_ID = os.environ.get("OPENALEX_AUTHOR_ID", "")
MAILTO = os.environ.get("OPENALEX_MAILTO", "varshini.bogolu@kensho.com")
MIN_WORKS = 10
API = "https://api.openalex.org"

# Papers by a different V. Reddy.
EXCLUDE = ["cobalt doped nickel ferrite", "5g network optimization"]

ALIASES = ["v reddy", "varshini reddy", "varshini bogolu", "v bogolu"]

# Same work indexed twice (preprint + camera-ready). Keys are normalised
# titles; values are the title to keep.
CANONICAL = {
    "the effect of scripts and formats on llm numeracy":
        "1,729 vs. 1729: The effect of scripts and formats on LLM numeracy",
}


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": f"portfolio-sync ({MAILTO})"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def norm(t):
    return re.sub(r"[^a-z0-9 ]", "", t.lower()).strip()


def initialise(name):
    """'Craig W Schmidt' -> 'C. W. Schmidt'"""
    parts = [p for p in name.replace(".", "").split() if p]
    if len(parts) < 2:
        return name
    return " ".join(p[0] + "." for p in parts[:-1]) + " " + parts[-1]


def format_authors(names, limit=7):
    out = [initialise(n) for n in names]
    if len(out) > limit:
        out = out[:limit] + ["et al."]
    return ", ".join(out)


def format_venue(w):
    year = w.get("publication_year", "")
    loc = (w.get("primary_location") or {}).get("source") or {}
    venue = (loc.get("display_name") or "").strip()
    venue = re.sub(r"^Proceedings of (the )?", "", venue).strip()
    if not venue or "arxiv" in venue.lower():
        ids = w.get("ids") or {}
        doi = (ids.get("doi") or "")
        m = re.search(r"arxiv\.(\d{4}\.\d{4,5})", doi)
        venue = f"arXiv {m.group(1)}" if m else "Preprint"
    return f"{venue} \u00b7 {year}" if year and str(year) not in venue else venue


def resolve_author():
    if AUTHOR_ID:
        return AUTHOR_ID
    q = urllib.parse.quote(AUTHOR_NAME)
    data = get(f"{API}/authors?search={q}&per-page=25&mailto={MAILTO}")
    cands = data.get("results", [])
    if not cands:
        sys.exit("no OpenAlex author found")
    best = max(cands, key=lambda a: a.get("works_count", 0))
    print(f"author: {best['display_name']} ({best['id']}, {best.get('works_count')} works)")
    return best["id"].rsplit("/", 1)[-1]


def main():
    aid = resolve_author()
    rows, seen, cursor = [], {}, "*"

    while cursor:
        url = (f"{API}/works?filter=author.id:{aid}&per-page=200"
               f"&cursor={urllib.parse.quote(cursor)}&mailto={MAILTO}")
        page = get(url)
        for w in page.get("results", []):
            title = (w.get("title") or "").strip()
            if not title:
                continue
            key = norm(title)
            if any(x in key for x in EXCLUDE):
                continue

            names = [(a.get("author") or {}).get("display_name", "")
                     for a in w.get("authorships", [])]
            names = [n for n in names if n]
            if not any(any(al in n.lower() for al in ALIASES) for n in names):
                continue

            canon = CANONICAL.get(key, title)
            key = norm(canon)
            cites = w.get("cited_by_count", 0)

            if key in seen:
                if cites <= seen[key]["cites"]:
                    continue
                rows = [r for r in rows if norm(r["title"]) != key]

            ids = w.get("ids") or {}
            row = {
                "title": canon,
                "authors": format_authors(names),
                "venue": format_venue(w),
                "cites": cites,
                "url": ids.get("doi") or w.get("doi") or
                       (w.get("primary_location") or {}).get("landing_page_url") or "",
            }
            seen[key] = row
            rows.append(row)
        cursor = (page.get("meta") or {}).get("next_cursor")

    if len(rows) < MIN_WORKS:
        sys.exit(f"only {len(rows)} works returned (need {MIN_WORKS}); leaving "
                 f"publications.json untouched")

    rows.sort(key=lambda r: -r["cites"])

    OUT.write_text(json.dumps({
        "_comment": "Generated by scripts/sync_scholar.py from OpenAlex. Sorted by citations, descending.",
        "updated": datetime.date.today().isoformat(),
        "publications": rows,
    }, indent=2, ensure_ascii=False) + "\n")

    print(f"wrote {len(rows)} publications to {OUT}")


if __name__ == "__main__":
    main()
