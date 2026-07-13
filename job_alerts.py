#!/usr/bin/env python3
"""
Feed-driven job alerts. No company list to maintain.

Sources (both auto-discover across thousands of companies):
  1. Simplify New-Grad-Positions feed  -> US/Canada/remote new-grad SWE/AI/ML,
     with sponsorship + post-date tags.
  2. The Muse public API               -> entry-level software, extra breadth.

New matching roles are pushed to your phone via ntfy.sh. State lives in
seen.json (committed back by the GitHub Actions workflow) so each posting
alerts once.
"""
import json
import os
import sys
import time
import requests

# ------------------------------- filters -------------------------------
# Keep a role only if its category matches one of these (substring, lowercased).
CATEGORY_KEEP = ["software", "ai", "ml", "data"]

# Drop obvious non-entry-level titles.
TITLE_EXCLUDE = [
    "senior", "staff", "principal", "lead", "manager", "director",
    "sr.", " ii", " iii", "vp", "head of", "architect",
]

# OPT-friendly filter: drop roles explicitly closed to sponsorship.
# "Other" means unlabeled (kept), "Offers Sponsorship" is the green flag.
SPONSORSHIP_EXCLUDE = {"U.S. Citizenship is Required", "Does Not Offer Sponsorship"}

# Optional location filter. Empty = keep all (feed is already US/Canada/remote).
# e.g. ["remote", "ca", "az", "ny", "tx", "bay area", "san francisco"]
LOCATION_KEEP = []

# Ignore roles posted more than this many days ago (guards against stale
# rows re-surfacing). Set high; diffing handles the rest.
MAX_AGE_DAYS = 21

# -----------------------------------------------------------------------
SIMPLIFY_URL = ("https://raw.githubusercontent.com/SimplifyJobs/"
                "New-Grad-Positions/dev/.github/scripts/listings.json")
MUSE_URL = "https://www.themuse.com/api/public/jobs"
STATE_FILE = "seen.json"
NTFY_TOPIC = os.environ["NTFY_TOPIC"]
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}"
HEADERS = {"User-Agent": "job-alert-bot/1.0"}
TIMEOUT = 30
NOW = time.time()


def _title_ok(title):
    return not any(k in title.lower() for k in TITLE_EXCLUDE)


def _category_ok(cat):
    c = (cat or "").lower()
    return any(k in c for k in CATEGORY_KEEP)


def _location_ok(locs):
    if not LOCATION_KEEP:
        return True
    blob = " ".join(locs).lower()
    return any(k in blob for k in LOCATION_KEEP)


def fetch_simplify():
    r = requests.get(SIMPLIFY_URL, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for j in r.json():
        if not (j.get("active") and j.get("is_visible")):
            continue
        if not _category_ok(j.get("category")):
            continue
        if not _title_ok(j.get("title", "")):
            continue
        if j.get("sponsorship") in SPONSORSHIP_EXCLUDE:
            continue
        if (NOW - (j.get("date_posted") or 0)) > MAX_AGE_DAYS * 86400:
            continue
        locs = j.get("locations") or []
        if not _location_ok(locs):
            continue
        out.append({
            "key": f"simplify:{j['id']}",
            "company": j.get("company_name", "?"),
            "title": j.get("title", ""),
            "url": j.get("url", ""),
            "location": ", ".join(locs),
            "sponsorship": j.get("sponsorship", ""),
        })
    return out


def fetch_muse(pages=3):
    out = []
    for page in range(pages):
        params = {"category": "Software Engineering", "level": "Entry Level", "page": page}
        r = requests.get(MUSE_URL, params=params, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            break
        for j in r.json().get("results", []):
            if not _title_ok(j.get("name", "")):
                continue
            locs = [l.get("name", "") for l in (j.get("locations") or [])]
            if not _location_ok(locs):
                continue
            out.append({
                "key": f"muse:{j['id']}",
                "company": (j.get("company") or {}).get("name", "?"),
                "title": j.get("name", ""),
                "url": (j.get("refs") or {}).get("landing_page", ""),
                "location": ", ".join(locs),
                "sponsorship": "",
            })
        time.sleep(0.4)
    return out


def load_seen():
    try:
        with open(STATE_FILE) as f:
            return set(json.load(f))
    except FileNotFoundError:
        return None  # first run -> seed silently


def save_seen(seen):
    with open(STATE_FILE, "w") as f:
        json.dump(sorted(seen), f, indent=0)


def notify(job):
    tag = f"  [{job['sponsorship']}]" if job.get("sponsorship") == "Offers Sponsorship" else ""
    headers = {"Title": f"{job['company']}{tag}", "Tags": "briefcase", "Priority": "high"}
    if job.get("url"):
        headers["Click"] = job["url"]
    body = job["title"] + (f"\n{job['location']}" if job.get("location") else "")
    try:
        requests.post(NTFY_URL, data=body.encode("utf-8"), headers=headers, timeout=TIMEOUT)
    except Exception as e:
        print(f"notify failed: {e}", file=sys.stderr)


def main():
    seen = load_seen()
    first_run = seen is None
    if first_run:
        seen = set()

    jobs = {}
    for name, fn in (("simplify", fetch_simplify), ("muse", fetch_muse)):
        try:
            for job in fn():
                jobs[job["key"]] = job
        except Exception as e:
            print(f"[warn] {name} failed: {e}", file=sys.stderr)

    new = [k for k in jobs if k not in seen]

    if first_run:
        print(f"First run: seeded {len(jobs)} live roles, no alerts sent.")
    else:
        print(f"{len(new)} new matching role(s) out of {len(jobs)} live.")
        for k in new:
            j = jobs[k]
            print(f"  -> {j['company']}: {j['title']}")
            notify(j)

    seen.update(jobs.keys())
    save_seen(seen)


if __name__ == "__main__":
    main()