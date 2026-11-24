"""Shared helpers: the screening record, time parsing, title cleanup, HTTP."""
from __future__ import annotations

import datetime as dt
import re
import time
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

import requests

NY = ZoneInfo("America/New_York")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 NYCRepertoryTracker/1.0 (personal, non-commercial)")

THEATERS = {  # id: (name, neighborhood, schedule page)
    "paris":       ("Paris Theater", "Midtown", "https://www.paristheaternyc.com"),
    "filmforum":   ("Film Forum", "West Village", "https://filmforum.org"),
    "metrograph":  ("Metrograph", "Lower East Side", "https://metrograph.com/nyc/"),
    "filmlinc":    ("Film at Lincoln Center", "Upper West Side", "https://www.filmlinc.org/now-playing/"),
    "ifc":         ("IFC Center", "West Village", "https://www.ifccenter.com"),
    "moma":        ("MoMA", "Midtown", "https://www.moma.org/calendar/?happening_filter=Films&locale=en&location=both"),
    "roxy":        ("Roxy Cinema", "Tribeca", "https://www.roxycinemanewyork.com/now-showing/"),
    "momi":        ("Museum of the Moving Image", "Astoria", "https://movingimage.org/events/list/"),
    "angelika":    ("Angelika New York", "SoHo", "https://angelikafilmcenter.com/nyc/now-playing"),
    "villageeast": ("Village East by Angelika", "East Village", "https://angelikafilmcenter.com/villageeast/now-playing"),
}


@dataclass
class Screening:
    theater: str
    date: dt.date
    title: str
    times: list = field(default_factory=list)   # [(minutes_after_midnight, tag)]
    fmt: str = ""
    note: str = ""
    url: str = ""


class Window:
    """The date range being collected: today (New York) through today + days - 1."""
    def __init__(self, days: int = 14, start: dt.date | None = None):
        self.start = start or dt.datetime.now(NY).date()
        self.end = self.start + dt.timedelta(days=days - 1)

    def __contains__(self, d: dt.date) -> bool:
        return self.start <= d <= self.end

    def dates(self):
        return [self.start + dt.timedelta(days=i) for i in range((self.end - self.start).days + 1)]


# ---------- times ----------
_TIME = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s*m?\.?", re.I)

def parse_time(s: str, assume_pm: bool = False) -> int | None:
    """'7:00 PM', '7:00PM', '7 p.m.', '12:01 AM' -> minutes. Bare '6:30' only if assume_pm."""
    s = s.strip()
    m = _TIME.search(s)
    if m:
        h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3).lower()
        if ap == "p" and h != 12:
            h += 12
        if ap == "a" and h == 12:
            h = 0
        return h * 60 + mi
    m = re.search(r"(\d{1,2}):(\d{2})", s)
    if m and assume_pm:
        # Film Forum-style bare times: 10:xx and 11:xx are mornings, everything else afternoon/evening
        h, mi = int(m.group(1)), int(m.group(2))
        if h == 12 or h in (10, 11):
            return h * 60 + mi
        return (h + 12) * 60 + mi
    return None


def time_label(mins: int) -> str:
    h, m = divmod(mins % 1440, 60)
    ap = "a" if h < 12 else "p"
    h12 = h % 12 or 12
    return f"{h12}{ap}" if m == 0 else f"{h12}:{m:02d}{ap}"


# ---------- dates ----------
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}

def infer_date(month: int, day: int, window: Window) -> dt.date:
    """Dates printed without a year: pick the year that lands nearest the window."""
    y = window.start.year
    d = dt.date(y, month, day)
    if (window.start - d).days > 180:
        d = dt.date(y + 1, month, day)
    return d

def month_num(name: str) -> int:
    return MONTHS[name.strip().lower()[:3]]


# ---------- titles ----------
SMALL = {"a", "an", "and", "as", "at", "but", "by", "for", "in", "of", "on", "or", "the", "to", "vs", "with", "from"}

def smart_title(s: str) -> str:
    """Title-case a title only if the site shouted it in ALL CAPS."""
    s = re.sub(r"\s+", " ", s).strip()
    letters = [c for c in s if c.isalpha()]
    if not letters or sum(c.isupper() for c in letters) / len(letters) < 0.8:
        return s
    words, out = s.lower().split(" "), []
    for i, w in enumerate(words):
        if i and w in SMALL and not out[-1].endswith(":"):
            out.append(w)
        elif w in {"ii", "iii", "iv", "vi", "vii", "xi", "nyc", "usa", "ffjr"}:
            out.append(w.upper())
        else:
            out.append(re.sub(r"(^|[-/(“\"'’])([a-zà-ÿ])", lambda m: m.group(1) + m.group(2).upper(), w))
    return " ".join(out)

def norm(s: str) -> str:
    s = s.lower().replace("’", "'")
    s = re.sub(r"\bin (35|70|16)mm\b", "", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


# ---------- formats ----------
FMT_RE = re.compile(r"\b(70mm|35mm|16mm|super ?8|4k dcp|4k restoration|dcp|4k|digital|video)\b", re.I)

def find_format(text: str) -> str:
    """First format named in a block of text, normalised for display."""
    m = FMT_RE.search(text or "")
    if not m:
        return ""
    f = m.group(1).lower()
    return {"4k dcp": "4K DCP", "4k restoration": "4K restoration", "dcp": "DCP", "4k": "4K",
            "digital": "Digital", "video": "Video"}.get(f, f.replace("super8", "Super 8"))

def format_category(fmt: str) -> str:
    f = fmt.lower()
    if "70mm" in f:
        return "70mm"
    if "35mm" in f and "-to-" not in f:
        return "35mm"
    if "16mm" in f and "-to-" not in f:
        return "16mm"
    return "digital" if f else "unlisted"


# ---------- HTTP ----------
_session = requests.Session()
_session.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})

def get(url: str, *, json: bool = False, tries: int = 3, pause: float = 1.0, **kw):
    """Polite GET with retries. Raises on final failure."""
    last = None
    for i in range(tries):
        try:
            r = _session.get(url, timeout=30, **kw)
            r.raise_for_status()
            time.sleep(pause)
            return r.json() if json else r.text
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"GET {url} failed: {last}")
