#!/usr/bin/env python3
"""Find and verify faculty advertisements for every listing in data/listings.json.

For each institute it:
  1. opens the known recruitment page, or finds one from the institute homepage
     (optionally via a web-search API when BRAVE_API_KEY is set);
  2. picks the links that look like faculty advertisements (pages or PDFs);
  3. reads them and extracts the last date to apply, or detects a rolling advertisement;
  4. verifies: the page loads, is on the institute's own domain, and talks about faculty posts
     (optionally double-checked by Claude when ANTHROPIC_API_KEY is set);
  5. updates the listing when confidence is high, otherwise puts it in the review list.

Writes data/listings.json, data/listings.js (what the website loads) and data/scrape-report.md.

Usage:
  python scraper/scrape.py                 # check everything
  python scraper/scrape.py --only "IIT M"  # only names containing this text
  python scraper/scrape.py --kind NIT      # only one institute type
  python scraper/scrape.py --dry-run       # report only, change nothing
  python scraper/scrape.py --build-only    # just rebuild listings.js after a manual edit
"""
import argparse
import datetime as dt
import io
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib import robotparser
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
DATA_JSON = ROOT / "data" / "listings.json"
DATA_JS = ROOT / "data" / "listings.js"
REPORT = ROOT / "data" / "scrape-report.md"

USER_AGENT = os.environ.get(
    "SCRAPER_USER_AGENT",
    "AcademicJobsIndiaBot/0.1 (faculty openings directory; reads public recruitment pages; respects robots.txt)")
MAX_BYTES = 8_000_000
DELAY_SECONDS = 1.5          # pause between requests to the same site
PDF_PAGES = 6

# ---------------------------------------------------------------- text rules
FACULTY_RE = re.compile(r"faculty|professor", re.I)
ADVERT_RE = re.compile(r"advertis|advt|recruit|applications?\s+(?:are\s+)?invited|rolling|vacanc|positions?", re.I)
ROLLING_RE = re.compile(
    r"rolling\s+(?:advertisement|advt\.?|basis|recruitment|faculty)|standing\s+advertisement|"
    r"throughout\s+the\s+year|round\s+the\s+year|year[\s-]round|no\s+last\s+date|open\s+throughout", re.I)
DEADLINE_KEY_RE = re.compile(
    r"last\s+date|closing\s+date|clos(?:es|ing)\s+on|deadline|apply\s+by|on\s+or\s+before|"
    r"cut[\s-]?off\s+date|end\s+date|extended\s+(?:up\s*)?to|up\s*to|till|until", re.I)
NEGATIVE_RE = re.compile(
    r"non[\s-]?teaching|non[\s-]?faculty|staff|jrf|srf|project|technical|administrat|tender|admission|ph\.?d|"
    r"student|guest|result|shortlist|short-list|syllabus|intern|postdoc|post-doc|fellow|registrar|librarian|"
    r"contract|visiting|adjunct|walk-in|scholarship|quotation", re.I)

MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
_MON = "jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
DATE_PATTERNS = [
    (re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)"), "ymd"),
    (re.compile(r"(?<!\d)(\d{1,2})\s?[./-]\s?(\d{1,2})\s?[./-]\s?(\d{4})(?!\d)"), "dmy"),
    (re.compile(rf"(?<!\d)(\d{{1,2}})(?:st|nd|rd|th)?[\s,.-]+({_MON})[a-z]*\.?[\s,.-]+(\d{{4}})(?!\d)", re.I), "dMy"),
    (re.compile(rf"\b({_MON})[a-z]*\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})(?!\d)", re.I), "Mdy"),
]


def find_dates(text):
    """All dates in text as (position, date). Numeric dates are read day-first (Indian style)."""
    found = []
    for rx, kind in DATE_PATTERNS:
        for m in rx.finditer(text):
            try:
                if kind == "ymd":
                    d = dt.date(int(m[1]), int(m[2]), int(m[3]))
                elif kind == "dmy":
                    d = dt.date(int(m[3]), int(m[2]), int(m[1]))
                elif kind == "dMy":
                    d = dt.date(int(m[3]), MONTHS[m[2][:3].lower()], int(m[1]))
                else:
                    d = dt.date(int(m[3]), MONTHS[m[1][:3].lower()], int(m[2]))
            except (ValueError, KeyError):
                continue
            found.append((m.start(), d))
    return sorted(found)


def find_deadline(text, today):
    """The latest date that directly follows a 'last date' style phrase, within two years of today."""
    dates = find_dates(text)
    lo, hi = today - dt.timedelta(days=730), today + dt.timedelta(days=730)
    hits = []
    for k in DEADLINE_KEY_RE.finditer(text):
        reach = 45 if k.group(0).lower().replace(" ", "") in ("upto", "till", "until") else 110
        for pos, d in dates:
            if k.end() <= pos <= k.end() + reach:
                if lo <= d <= hi:
                    hits.append(d)
                break
    return max(hits) if hits else None


def site_key(url):
    """iitm.ac.in for facapp.iitm.ac.in, so sub-sites of one institute count as the same site."""
    host = (urlparse(url).hostname or "").lower()
    parts = host.split(".")
    n = 3 if re.search(r"\.(ac|edu|ernet|res|gov|nic|org|co)\.in$", host) else 2
    return ".".join(parts[-n:])


def link_score(url, text, year):
    """How much a link looks like a faculty advertisement."""
    s = f"{text} {urlparse(url).path}".lower().replace("_", " ").replace("-", " ").replace("%20", " ")
    score = 0
    if re.search(r"faculty", s): score += 3
    if re.search(r"professor", s): score += 3
    if re.search(r"rolling", s): score += 2
    if re.search(r"advertis|advt", s): score += 2
    if re.search(r"recruit|vacanc|opening|position|career|job", s): score += 1
    if url.lower().split("?")[0].endswith(".pdf"): score += 1
    if str(year) in s or str(year - 1) in s: score += 1
    if NEGATIVE_RE.search(s): score -= 5
    return score


# ---------------------------------------------------------------- fetching
@dataclass
class Page:
    url: str
    ok: bool = False
    error: str = ""
    text: str = ""
    links: list = field(default_factory=list)   # (absolute url, link text)
    is_pdf: bool = False


class Fetcher:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en"})
        self.last_hit, self.robots, self.cache = {}, {}, {}

    def allowed(self, url):
        p = urlparse(url)
        base = f"{p.scheme}://{p.netloc}"
        if base not in self.robots:
            rp = robotparser.RobotFileParser()
            try:
                r = self.session.get(base + "/robots.txt", timeout=15)
                rp.parse(r.text.splitlines() if r.status_code == 200 else [])
            except requests.RequestException:
                rp.parse([])
            self.robots[base] = rp
        return self.robots[base].can_fetch(USER_AGENT, url)

    def get(self, url):
        if url in self.cache:
            return self.cache[url]
        page = self._get(url)
        self.cache[url] = page
        return page

    def _get(self, url):
        if not self.allowed(url):
            return Page(url, error="blocked by the site's robots.txt")
        host = urlparse(url).netloc
        wait = DELAY_SECONDS - (time.time() - self.last_hit.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        try:
            r = self.session.get(url, timeout=30)
        except requests.exceptions.SSLError:
            return Page(url, error="security certificate problem")
        except requests.RequestException as e:
            return Page(url, error=type(e).__name__)
        finally:
            self.last_hit[host] = time.time()
        if r.status_code != 200:
            return Page(r.url, error=f"HTTP {r.status_code}")
        body = r.content[:MAX_BYTES]
        ctype = r.headers.get("content-type", "").lower()
        if body[:5] == b"%PDF-" or "pdf" in ctype:
            return Page(r.url, ok=True, text=pdf_text(body), is_pdf=True)
        soup = BeautifulSoup(body, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        links = []
        for a in soup.find_all("a", href=True):
            href = urljoin(r.url, a["href"].strip())
            if href.startswith(("http://", "https://")):
                links.append((href.split("#")[0], a.get_text(" ", strip=True) or a.get("title", "")))
        return Page(r.url, ok=True, text=soup.get_text(" ", strip=True), links=links)


def pdf_text(body):
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(body))
        return " ".join((p.extract_text() or "") for p in reader.pages[:PDF_PAGES])
    except Exception:
        return ""       # scanned or damaged PDF: no readable text


# ---------------------------------------------------------------- optional helpers (need API keys)
def web_search(query, session):
    """Search the web through the Brave Search API. Skipped unless BRAVE_API_KEY is set."""
    key = os.environ.get("BRAVE_API_KEY")
    if not key:
        return []
    try:
        r = session.get("https://api.search.brave.com/res/v1/web/search",
                        params={"q": query, "count": 8},
                        headers={"Accept": "application/json", "X-Subscription-Token": key}, timeout=20)
        return [x["url"] for x in r.json().get("web", {}).get("results", []) if x.get("url")]
    except Exception:
        return []


def ai_check(name, text):
    """Ask Claude to read the advertisement text. Skipped unless ANTHROPIC_API_KEY is set.
    Returns {"is_faculty_ad": bool, "deadline": "YYYY-MM-DD" or "", "rolling": bool} or None."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key or not text.strip():
        return None
    prompt = (f"Below is text from a web page or PDF of {name}. Reply with JSON only, no other words:\n"
              '{"is_faculty_ad": true or false, "deadline": "YYYY-MM-DD or empty", "rolling": true or false}\n'
              "is_faculty_ad: it invites applications for regular faculty posts (Assistant/Associate/Professor).\n"
              "deadline: the last date to apply online, if stated. rolling: applications accepted all year.\n\n"
              + text[:12000])
    try:
        r = requests.post("https://api.anthropic.com/v1/messages", timeout=60,
                          headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                                   "content-type": "application/json"},
                          json={"model": os.environ.get("ANTHROPIC_MODEL", "claude-haiku-5-5"),
                                "max_tokens": 200, "messages": [{"role": "user", "content": prompt}]})
        reply = r.json()["content"][0]["text"]
        return json.loads(reply[reply.index("{"):reply.rindex("}") + 1])
    except Exception:
        return None


# ---------------------------------------------------------------- analysis of one listing
@dataclass
class Finding:
    apply_url: str = ""
    ad_url: str = ""
    deadline: dt.date = None
    rolling: bool = False
    confidence: str = "low"      # high: safe to apply automatically; medium: needs a look; low: found nothing
    reason: str = ""


def looks_like_recruitment(page):
    return page.ok and len(FACULTY_RE.findall(page.text)) >= 2 and bool(ADVERT_RE.search(page.text))


def discover_page(home, fetcher, name):
    """Find the faculty recruitment page starting from the institute homepage."""
    year = dt.date.today().year
    start = fetcher.get(home)
    if not start.ok:
        return None, f"homepage did not load ({start.error})"
    seen, best = {start.url}, None
    frontier = [start]
    for _depth in range(2):
        nxt = []
        for page in frontier:
            cands = sorted({u: link_score(u, t, year) for u, t in page.links
                            if site_key(u) == site_key(home) and u not in seen}.items(),
                           key=lambda x: -x[1])
            for url, score in cands[:3]:
                if score < 1:
                    break
                seen.add(url)
                p = fetcher.get(url)
                if p.is_pdf or not p.ok:
                    continue
                if looks_like_recruitment(p):
                    strength = len(FACULTY_RE.findall(p.text))
                    if not best or strength > best[0]:
                        best = (strength, p)
                nxt.append(p)
        if best:
            return best[1], ""
        frontier = nxt
    for url in web_search(f"{name} faculty recruitment advertisement", fetcher.session):
        if site_key(url) == site_key(home):
            p = fetcher.get(url)
            if not p.is_pdf and looks_like_recruitment(p):
                return p, ""
    return None, "no faculty recruitment page found from the homepage"


def analyse(listing, fetcher, today):
    f = Finding()
    name = listing["name"]
    home = listing.get("siteUrl") or ""
    if not home and listing.get("applyUrl"):
        p = urlparse(listing["applyUrl"])
        home = f"{p.scheme}://{p.netloc}/"

    page, why = None, ""
    if listing.get("applyUrl"):
        p = fetcher.get(listing["applyUrl"])
        if p.ok and not p.is_pdf and (FACULTY_RE.search(p.text) or p.links):
            page = p
        else:
            why = f"saved recruitment link did not work ({p.error or 'no readable content'})"
    if page is None and home:
        page, why2 = discover_page(home, fetcher, name)
        why = "; ".join(x for x in (why, why2) if x)
    if page is None:
        f.reason = why or "no link to start from"
        return f
    f.apply_url = listing.get("applyUrl") if (listing.get("applyUrl") and not why) else page.url

    # Candidate advertisements: the page itself plus its best-scoring links on the same site.
    year = today.year
    scored = sorted({u: link_score(u, t, year) for u, t in page.links if site_key(u) == site_key(page.url)}.items(),
                    key=lambda x: -x[1])
    sources = [(page.url, page.text, False)]
    for url, score in scored[:4]:
        if score < 4:
            break
        p = fetcher.get(url)
        if p.ok and (FACULTY_RE.search(p.text) or (p.is_pdf and not p.text.strip())):
            sources.append((p.url, p.text, True))

    # Read each source; prefer the advertisement with the latest last date, then a rolling one.
    readings = [(url, find_deadline(text, today), bool(ROLLING_RE.search(text)), is_ad, text)
                for url, text, is_ad in sources]
    dated = [r for r in readings if r[1]]
    rolling = [r for r in readings if r[2]]
    ads = [r for r in readings if r[3]]
    pick = max(dated, key=lambda r: (r[1], r[3])) if dated else (rolling[0] if rolling else (ads[0] if ads else readings[0]))
    f.ad_url, f.deadline, f.rolling = pick[0], pick[1], bool(rolling)
    if not pick[3] and not (pick[1] or pick[2]):
        f.ad_url = ""                    # nothing that looks like an advertisement
    unreadable = [r for r in ads if not r[4].strip()]

    if f.deadline or f.rolling:
        f.confidence = "high"
        f.reason = ("rolling advertisement" if f.rolling and not f.deadline
                    else f"last date {f.deadline.isoformat()} read from the advertisement")
    else:
        f.confidence = "medium"
        f.reason = ("advertisement is a scanned PDF that cannot be read automatically" if unreadable
                    else "recruitment page found but no last date or rolling wording")

    ai = ai_check(name, pick[4])
    if ai is not None:
        if not ai.get("is_faculty_ad"):
            f.confidence, f.reason = "medium", "AI check: this does not read like a faculty advertisement"
        elif ai.get("deadline"):
            try:
                ai_date = dt.date.fromisoformat(ai["deadline"])
                if f.deadline is None:
                    f.deadline, f.confidence, f.reason = ai_date, "high", f"last date {ai_date} read by AI check"
                elif ai_date != f.deadline:
                    f.confidence = "medium"
                    f.reason = f"two different last dates found: {f.deadline} and {ai_date}"
            except ValueError:
                pass
        elif ai.get("rolling") and not f.deadline:
            f.rolling, f.confidence, f.reason = True, "high", "rolling advertisement (AI check)"
    return f


# ---------------------------------------------------------------- applying findings
def decide_status(deadline, rolling, today):
    if deadline and deadline >= today:
        return "active"
    if rolling:
        return "active"
    if deadline and deadline < today:
        return "inactive"
    return None


def apply_finding(listing, f, today):
    """Update the listing in place. Returns (bucket, message): bucket is updated | review | failed | same."""
    before = json.dumps(listing, sort_keys=True)
    listing["confidence"] = f.confidence
    if f.confidence == "low":
        return "failed", f.reason
    if f.apply_url and f.apply_url != listing.get("applyUrl"):
        listing["applyUrl"] = f.apply_url
    if listing.get("note", "").startswith("Recruitment page not added"):
        listing["note"] = ""
    if f.confidence == "medium":
        if f.ad_url and not listing.get("adUrl"):
            listing["adUrl"] = f.ad_url
        return "review", f.reason
    new_status = decide_status(f.deadline, f.rolling, today)
    new_deadline = f.deadline.isoformat() if f.deadline else ""
    if new_status == "inactive" and listing.get("status") == "active" and listing.get("deadline") != new_deadline:
        return "review", f"listed as open, but the newest advertisement found closed on {new_deadline}"
    listing.update(status=new_status, deadline=new_deadline, rolling=f.rolling, checked=today.isoformat())
    if f.ad_url:
        listing["adUrl"] = f.ad_url
    changed = json.dumps(listing, sort_keys=True) != before
    return ("updated" if changed else "same"), f.reason


def expire(listing, today):
    """Close a listing whose stored last date has passed. Runs even when the site could not be reached."""
    d = listing.get("deadline")
    if d and not listing.get("rolling") and listing.get("status") == "active" and dt.date.fromisoformat(d) < today:
        listing["status"] = "inactive"
        return True
    return False


# ---------------------------------------------------------------- files
def load():
    return json.loads(DATA_JSON.read_text(encoding="utf-8"))


def save(data):
    rows = ",\n".join(json.dumps(l, ensure_ascii=False) for l in data["listings"])
    DATA_JSON.write_text('{\n"profiles": ' + json.dumps(data["profiles"], ensure_ascii=False)
                         + ',\n"listings": [\n' + rows + "\n]\n}\n", encoding="utf-8")
    DATA_JS.write_text("// GENERATED from data/listings.json by scraper/scrape.py. Edit listings.json, not this file.\n"
                       "window.JOB_PROFILES = " + json.dumps(data["profiles"], ensure_ascii=False) + ";\n"
                       "window.JOB_LISTINGS = [\n" + rows + "\n];\n", encoding="utf-8")


def write_report(today, buckets, checked):
    lines = [f"# Scrape report, {today.isoformat()}", "",
             f"Checked {checked} listings: {len(buckets['updated'])} updated automatically, "
             f"{len(buckets['same'])} confirmed unchanged, {len(buckets['review'])} need your review, "
             f"{len(buckets['failed'])} could not be read.", ""]
    for key, title in [("review", "Needs your review"), ("updated", "Updated automatically"),
                       ("failed", "Could not be read"), ("expired", "Closed because the last date passed")]:
        if buckets[key]:
            lines += [f"## {title}", ""] + [f"- **{n}**: {msg}" for n, msg in buckets[key]] + [""]
    REPORT.write_text("\n".join(lines), encoding="utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--only", help="only listings whose name contains this text")
    ap.add_argument("--kind", help="only this institute type: IIT, IISc, NIT, IISER or CU")
    ap.add_argument("--limit", type=int, help="stop after this many listings")
    ap.add_argument("--dry-run", action="store_true", help="write the report but leave the data unchanged")
    ap.add_argument("--build-only", action="store_true", help="only rebuild data/listings.js")
    args = ap.parse_args(argv)

    data = load()
    if args.build_only:
        save(data)
        print("Rebuilt data/listings.js")
        return 0

    today = dt.date.today()
    fetcher = Fetcher()
    buckets = {k: [] for k in ("updated", "same", "review", "failed", "expired")}
    todo = [l for l in data["listings"]
            if not l.get("locked")
            and (not args.only or args.only.lower() in l["name"].lower())
            and (not args.kind or l["kind"].lower() == args.kind.lower())]
    todo = todo[:args.limit] if args.limit else todo
    for i, listing in enumerate(todo, 1):
        print(f"[{i}/{len(todo)}] {listing['name']} ...", end=" ", flush=True)
        try:
            finding = analyse(listing, fetcher, today)
            target = dict(listing) if args.dry_run else listing
            bucket, msg = apply_finding(target, finding, today)
        except Exception as e:                      # one bad site must not stop the run
            bucket, msg = "failed", f"unexpected error: {type(e).__name__}: {e}"
        buckets[bucket].append((listing["name"], msg))
        print(bucket, "-", msg)
    if not args.dry_run:
        for listing in data["listings"]:
            if not listing.get("locked") and expire(listing, today):
                buckets["expired"].append((listing["name"], f"last date was {listing['deadline']}"))
        save(data)
    write_report(today, buckets, len(todo))
    print(f"\nDone. See {REPORT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
