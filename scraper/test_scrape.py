"""Offline tests for the scraper's reading rules. Run: python scraper/test_scrape.py
No internet needed: pages are faked."""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import scrape as s

TODAY = dt.date(2026, 11, 14)


def test_dates():
    text = "a 30.09.2026 b 5/1/2027 c 15th December, 2026 d Jan 7, 2027 e 2026-12-01 f 31 Dec 2026 g 31.02.2026"
    got = [d.isoformat() for _, d in s.find_dates(text)]
    assert got == ["2026-09-30", "2027-01-05", "2026-12-15", "2027-01-07", "2026-12-01", "2026-12-31"], got


def test_deadline():
    f = lambda t: s.find_deadline(t, TODAY)
    assert f("Last date to apply online: 15.12.2026 (5 PM)") == dt.date(2026, 12, 15)
    assert f("Applications must reach on or before 31st January 2027.") == dt.date(2027, 1, 31)
    assert f("Last date 30.11.2026. Last date extended up to 20.12.2026") == dt.date(2026, 12, 20)
    assert f("Advertisement dated 01.10.2026 for faculty posts") is None          # a date, but not a last date
    assert f("PhD awarded before 2019; last date: 01.01.2015") is None            # too old to be the current call


def test_links_and_sites():
    y = 2026
    assert s.link_score("https://x.ac.in/docs/Faculty_Rolling_Advt_2026.pdf", "Rolling Advertisement", y) >= 6
    assert s.link_score("https://x.ac.in/recruit/non-teaching-2026.pdf", "Non-Teaching Staff Recruitment", y) < 4
    assert s.link_score("https://x.ac.in/jrf.pdf", "JRF position in project", y) < 4
    assert s.site_key("https://facapp.iitm.ac.in/x") == s.site_key("https://www.iitm.ac.in/")
    assert s.site_key("https://www.nitt.edu/a") == "nitt.edu"
    assert s.site_key("https://evil.example.com/iitm.ac.in") != "iitm.ac.in"


class FakeFetcher:
    session = None

    def __init__(self, pages):
        self.pages = pages

    def get(self, url):
        return self.pages.get(url, s.Page(url, error="HTTP 404"))


def page(url, text, links=(), pdf=False):
    return s.Page(url, ok=True, text=text, links=list(links), is_pdf=pdf)


def test_discovery_and_update():
    home, rec, ad = "https://www.nitx.ac.in/", "https://www.nitx.ac.in/recruitment/faculty", "https://www.nitx.ac.in/docs/faculty-advt-2026.pdf"
    f = FakeFetcher({
        home: page(home, "Welcome to NIT X", [(rec, "Faculty Recruitment"), ("https://www.nitx.ac.in/tenders", "Tenders"),
                                              ("https://other.com/faculty-recruitment", "Faculty Recruitment")]),
        rec: page(rec, "Faculty recruitment. Applications are invited for Assistant Professor and Professor posts.",
                  [(ad, "Advertisement for Faculty Positions 2026"), ("https://www.nitx.ac.in/docs/nonteaching.pdf", "Non-teaching staff advertisement")]),
        ad: page(ad, "Faculty advertisement. Last date for online application: 20.12.2026", pdf=True),
    })
    listing = {"name": "NIT X", "kind": "NIT", "status": "unverified", "applyUrl": "", "adUrl": "", "siteUrl": home,
               "note": "Recruitment page not added yet.", "deadline": "", "rolling": False, "checked": ""}
    finding = s.analyse(listing, f, TODAY)
    assert (finding.apply_url, finding.ad_url, finding.confidence) == (rec, ad, "high"), finding
    bucket, _ = s.apply_finding(listing, finding, TODAY)
    assert bucket == "updated" and listing["status"] == "active" and listing["deadline"] == "2026-12-20"
    assert listing["applyUrl"] == rec and listing["adUrl"] == ad and listing["note"] == "" and listing["checked"] == "2026-11-14"


def test_review_and_failure_paths():
    url = "https://www.iitx.ac.in/faculty"
    base = {"name": "IIT X", "kind": "IIT", "status": "active", "applyUrl": url, "adUrl": "", "siteUrl": "",
            "note": "", "deadline": "", "rolling": True, "checked": ""}
    # Rolling wording: stays open, high confidence.
    f = FakeFetcher({url: page(url, "Faculty positions: rolling advertisement, professor posts, applications invited.")})
    l = dict(base); fd = s.analyse(l, f, TODAY)
    assert fd.confidence == "high" and fd.rolling and s.apply_finding(l, fd, TODAY)[0] in ("updated", "same") and l["status"] == "active"
    # Page found but nothing readable about dates: review, status untouched.
    f = FakeFetcher({url: page(url, "Faculty recruitment. Professor posts. Applications invited. See the portal.")})
    l = dict(base, status="unverified", rolling=False); fd = s.analyse(l, f, TODAY)
    assert s.apply_finding(l, fd, TODAY)[0] == "review" and l["status"] == "unverified"
    # Listed open, but only an old closed advertisement is found: do not flip automatically.
    f = FakeFetcher({url: page(url, "Faculty recruitment for professor posts. Last date: 30.09.2026. Applications invited.")})
    l = dict(base, rolling=False); fd = s.analyse(l, f, TODAY)
    assert s.apply_finding(l, fd, TODAY)[0] == "review" and l["status"] == "active"
    # Site down: failed, nothing changes; a stored past last date still closes the listing.
    l = dict(base, rolling=False, deadline="2026-11-01"); fd = s.analyse(l, FakeFetcher({}), TODAY)
    assert s.apply_finding(l, fd, TODAY)[0] == "failed" and s.expire(l, TODAY) and l["status"] == "inactive"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
