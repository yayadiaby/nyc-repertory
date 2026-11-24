"""One function per theater. Each returns (screenings, closed_days) for the collection window.

The methods mirror how each site actually publishes its schedule:
  paris       - the site's own showtime JSON (the one its schedule widget loads), read in a real browser
  filmforum   - ticketing calendar for times + each film page for 35mm/series info and later dates
  metrograph  - server-rendered calendar on /nyc/ (every day is in the page)
  filmlinc    - the showtimes JSON its Now Playing page loads
  ifc         - each film page's own schedule (now playing + coming soon), plus the homepage week
  moma        - films-only calendar, "Show more events" until the window is covered, then each event page for format
  roxy        - Now Showing page cards
  momi        - the public iCalendar feed for the screenings filter
  angelika    - Now Playing page, clicking through each date in the picker (NY and Village East)
"""
from __future__ import annotations

import datetime as dt
import difflib
import html as htmllib
import re
from collections import defaultdict

from bs4 import BeautifulSoup

from common import (NY, Screening, Window, find_format, get, infer_date, month_num, norm,
                    parse_time, smart_title)

WD = r"(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day"


def soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def browser_html(ctx, url: str, wait_ms: int = 2500) -> str:
    page = ctx.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(wait_ms)
        return page.content()
    finally:
        page.close()


# ======================================================================= Paris
def paris(w: Window, ctx):
    base = "https://www.paristheaternyc.com"
    page = ctx.new_page()
    page.goto(base, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2500)
    links = page.eval_on_selector_all('a[href*="/film/"]', "els => els.map(a => a.href)")
    by_slug = {}
    for href in links:
        slug = href.rstrip("/").split("/film/")[-1]
        by_slug[norm(re.sub(r"-paris$", "", slug).replace("-", " "))] = href
    out = []
    for d in w.dates():
        data = page.evaluate("async u => { const r = await fetch(u); return r.ok ? r.json() : null }",
                             f"/api/showtimes/by-date?date={d.isoformat()}")
        if not data:
            continue
        rel = data.get("relatedData") or {}
        films = {f["id"]: f["title"]["text"] for f in rel.get("films", [])}
        attrs = {}
        for a in rel.get("attributes", []):
            name = ((a.get("shortName") or {}).get("text") or (a.get("name") or {}).get("text") or "").strip()
            attrs[a["id"]] = name
        groups = defaultdict(list)
        for s in data.get("showtimes", []):
            if s["schedule"]["businessDate"] != d.isoformat():
                continue
            start = dt.datetime.fromisoformat(s["schedule"]["startsAt"]).astimezone(NY)
            names = tuple(attrs.get(i, "") for i in s.get("attributeIds", []))
            groups[(s["filmId"], names)].append((start.hour * 60 + start.minute, ""))
        for (fid, names), times in groups.items():
            title = films.get(fid, "Untitled")
            fmt = next((find_format(n) for n in names if find_format(n)), "")
            note = "With a short film" if any("short" in n.lower() for n in names) else ""
            key = norm(title)
            url = next((u for k, u in by_slug.items() if k == key or k.startswith(key) or key.startswith(k)), base)
            out.append(Screening("paris", d, title, sorted(times), fmt, note, url))
    page.close()
    return out, []


# ================================================================== Film Forum
def _ff_clean_title(t: str) -> str:
    """'Zhang Yimou’s RAISE THE RED LANTERN' -> 'Raise the Red Lantern'; keeps mixed-case titles as-is."""
    t = re.sub(r"\s+", " ", t).strip()
    toks = t.split(" ")
    caps = [bool(re.search(r"[A-Z]", x)) and not re.search(r"[a-z]", x) or not re.search(r"[A-Za-z]", x) for x in toks]
    best, cur, start = (0, 0, 0), 0, 0
    for i, c in enumerate(caps + [False]):
        if c:
            if cur == 0:
                start = i
            cur += 1
        else:
            if cur > best[0]:
                best = (cur, start, i)
            cur = 0
    if best[0] >= 1 and best[0] < len(toks):
        t = " ".join(toks[best[1]:best[2]])
    return smart_title(t)


def filmforum(w: Window, ctx):
    site = "https://filmforum.org"
    film_urls, series_of, series_urls = set(), {}, {}
    for p in ("/now_playing", "/coming_soon"):
        s = soup(get(site + p))
        for a in s.select('a[href*="/film/"]'):
            film_urls.add(a["href"].split("#")[0].split("?")[0])
        for a in s.select('a[href*="/series/"]'):
            name = a.get_text(" ", strip=True)
            if name:
                series_urls[a["href"].split("#")[0]] = smart_title(name)
    for surl, sname in series_urls.items():
        s = soup(get(surl if surl.startswith("http") else site + surl))
        for a in s.select('a[href*="/film/"]'):
            u = a["href"].split("#")[0].split("?")[0]
            film_urls.add(u)
            series_of.setdefault(u if u.startswith("http") else site + u, sname)

    # each film page: its title and its own dated list ("Saturday, November 28 / 12:20 – 35mm")
    pages = {}   # url -> dict(title, sched={(date, mins): fmt})
    for u in sorted(film_urls):
        u = u if u.startswith("http") else site + u
        try:
            s = soup(get(u, pause=0.5))
        except RuntimeError:
            continue
        h = s.select_one(".main-title, h2.title, h2")
        title = _ff_clean_title(h.get_text(" ", strip=True)) if h else u.rsplit("/", 1)[-1]
        sched, day = {}, None
        det = s.select_one(".details")
        if det:
            lines = [htmllib.unescape(x).replace("\xa0", " ").strip()
                     for x in re.split(r"<br\s*/?>|</p>", str(det))]
            for ln in (re.sub(r"<[^>]+>", "", x).strip() for x in lines):
                m = re.match(rf"{WD},\s+(\w+)\s+(\d{{1,2}})", ln)
                if m:
                    day = infer_date(month_num(m.group(1)), int(m.group(2)), w)
                    continue
                if day and re.match(r"^\d{1,2}:\d\d", ln):
                    fmt = "35mm" if "35mm" in ln.lower() else ""
                    for t in re.findall(r"\d{1,2}:\d\d", ln):
                        sched[(day, parse_time(t, assume_pm=True))] = fmt
        pages[u] = {"title": title, "sched": sched}

    # ticketing calendar: every showtime with AM/PM
    cal = []  # (date, TITLE, mins, oc)
    page = ctx.new_page()
    page.goto("https://my.filmforum.org/events", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)
    for _ in range(3):
        cal += _ff_parse_calendar(page.inner_text("body"), w)
        last = max((c[0] for c in cal), default=w.start)
        if last >= w.end:
            break
        nxt = page.locator('button:has-text("Next month"), a:has-text("Next month"), [aria-label*="Next month"]').first
        if not nxt.count():
            break
        nxt.click()
        page.wait_for_timeout(3000)
    page.close()

    # attach each calendar showtime to a film page: exact date+time first, then closest title
    page_titles = {norm(v["title"]): u for u, v in pages.items()}
    out_rows, covered = [], set()
    for d, raw, mins, oc in cal:
        if d not in w:
            continue
        url = next((u for u, v in pages.items() if (d, mins) in v["sched"]), None)
        if not url:
            key = norm(raw)
            hit = difflib.get_close_matches(key, list(page_titles), n=1, cutoff=0.55)
            url = page_titles[hit[0]] if hit else None
            if not url:
                url = next((u for k, u in page_titles.items() if key and (key in k or k in key)), None)
        info = pages.get(url, {})
        title = info.get("title") or smart_title(raw)
        fmt = info.get("sched", {}).get((d, mins)) or "DCP"
        out_rows.append((d, title, mins, "OC" if oc else "", fmt, series_of.get(url, ""), url or site))
        covered.add((d, mins, url))
    # showtimes printed on film pages but not yet in the ticketing calendar
    for u, v in pages.items():
        for (d, mins), fmt in v["sched"].items():
            if d in w and mins is not None and (d, mins, u) not in covered:
                out_rows.append((d, v["title"], mins, "", fmt or "DCP", series_of.get(u, ""), u))
    grouped = defaultdict(list)
    for d, title, mins, tag, fmt, note, url in out_rows:
        grouped[(d, title, fmt, note, url)].append((mins, tag))
    return [Screening("filmforum", d, t, sorted(set(ts)), f, n, u) for (d, t, f, n, u), ts in grouped.items()], []


def _ff_parse_calendar(text: str, w: Window):
    lines = [l.strip() for l in text.split("\n")]
    lines = [l for l in lines if l and l != ","]
    rows, day, mode = [], None, None
    for i, ln in enumerate(lines):
        m = re.match(rf"^{WD} (\d{{1,2}}) (\w+) (\d{{4}})$", ln)           # month grid: "Tuesday 24 November 2026"
        if m:
            day, mode = dt.date(int(m.group(3)), month_num(m.group(2)), int(m.group(1))), "grid"
            continue
        m = re.match(rf"^{WD}, (\w+) (\d{{1,2}}), (\d{{4}})$", ln)         # list view: "Wednesday, November 25, 2026"
        if m:
            day, mode = dt.date(int(m.group(3)), month_num(m.group(1)), int(m.group(2))), "list"
            continue
        if day and re.match(r"^\d{1,2}:\d\d\s?[AP]M$", ln, re.I):
            raw = lines[i - 1] if mode == "grid" else (lines[i + 1] if i + 1 < len(lines) else "")
            oc = bool(re.search(r"-\s*(open captions|oc)\s*$", raw, re.I))
            raw = re.sub(r"\s*-\s*(open captions|oc)\s*$", "", raw, flags=re.I)
            rows.append((day, raw, parse_time(ln), oc))
    return rows


# ================================================================== Metrograph
def metrograph(w: Window, ctx):
    url = "https://metrograph.com/nyc/"
    try:
        html = get(url)
        if "calendar-list-day" not in html:
            raise RuntimeError("calendar not in static HTML")
    except RuntimeError:
        html = browser_html(ctx, url)
    s = soup(html)
    out, closed = [], []
    for day in s.select(".calendar-list-day"):
        try:
            d = dt.date.fromisoformat(day.get("id", "").replace("calendar-list-day-", ""))
        except ValueError:
            continue
        if d not in w:
            continue
        items = day.select(".item")
        if not items:
            txt = day.get_text(" ", strip=True)
            if re.search(r"closed", txt, re.I):
                closed.append({"theater": "metrograph", "date": d.isoformat(), "note": txt[:90]})
            continue
        for it in items:
            a = it.select_one("a.title")
            if not a:
                continue
            meta = it.select_one(".film-metadata")
            fmt = meta.get_text(strip=True).split("/")[-1].strip() if meta else ""
            note = ""
            for extra in it.find_all("div", recursive=False):
                cls = " ".join(extra.get("class", []))
                if any(c in cls for c in ("film-metadata", "showtimes")):
                    continue
                t = extra.get_text(" ", strip=True)
                if not t:
                    continue
                m = re.search(r"on \w+day, (\w+) (\d{1,2})", t)
                if m and not (month_num(m.group(1)) == d.month and int(m.group(2)) == d.day):
                    continue          # this note belongs to a different date of the run
                note = re.sub(r"\s*on \w+day, \w+ \d{1,2}(st|nd|rd|th)?", "", t)
            times = [(parse_time(x.get_text()), "") for x in it.select(".showtimes a")]
            title = re.sub(r"\s*\[(4K DCP|35mm)\]\s*$", "", a.get_text(strip=True))
            out.append(Screening("metrograph", d, title, [t for t in times if t[0] is not None], fmt, note, a["href"]))
    return out, closed


# ============================================================ Lincoln Center
def filmlinc(w: Window, ctx):
    api = "https://api.filmlinc.org/showtimes"
    try:
        data = get(api, json=True, headers={"Origin": "https://www.filmlinc.org", "Referer": "https://www.filmlinc.org/"})
    except RuntimeError:
        page = ctx.new_page()
        page.goto("https://www.filmlinc.org/now-playing/", wait_until="domcontentloaded", timeout=60000)
        data = page.evaluate("async u => (await fetch(u)).json()", api)
        page.close()
    groups = defaultdict(list)
    for f in data.get("films", []):
        for s in f.get("showtimes") or []:
            d = dt.date.fromisoformat(s["date"])
            if d not in w:
                continue
            venue = (s.get("venue") or "").replace(" Theater", "")
            tag = "OC" if s.get("openCaptions") else ""
            groups[(d, f["title"], venue, f.get("slug", ""))].append((parse_time(s["time"]), tag))
    return [Screening("filmlinc", d, t, sorted(ts), "", v, f"https://www.filmlinc.org/films/{slug}/" if slug else
                      "https://www.filmlinc.org/now-playing/") for (d, t, v, slug), ts in groups.items()], []


# ================================================================= IFC Center
def ifc(w: Window, ctx):
    site = "https://www.ifccenter.com"
    home = soup(get(site + "/"))
    slugs = sorted({a["href"].split("/films/")[1].split("/")[0] for a in home.select('a[href*="/films/"]')
                    if a["href"].split("/films/")[1].split("/")[0]})
    seen = set()
    out = defaultdict(list)
    meta = {}
    for slug in slugs:
        url = f"{site}/films/{slug}/"
        try:
            s = soup(get(url, pause=0.5))
        except RuntimeError:
            continue
        h1 = s.select_one("h1")
        title = h1.get_text(" ", strip=True) if h1 else slug.replace("-", " ").title()
        text = re.sub(r"\s+", " ", s.get_text(" "))
        m = re.search(r"Format (.+?) (Distributor|Director|Country|Language|Cast|Year|Running)", text)
        fmt = m.group(1).strip() if m else ""
        notes = {}
        for li in s.select(".schedule-list > li"):
            strong = li.select_one(".details strong")
            dm = re.search(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w* (\d{1,2})", strong.get_text() if strong else "")
            if not dm:
                continue
            d = infer_date(month_num(dm.group(1)), int(dm.group(2)), w)
            spans = li.select(".times span")
            if not spans:   # "Special guests" list: a note for that date
                p = li.select_one(".details p")
                if p:
                    notes[d] = p.get_text(" ", strip=True).rstrip("! ")
                continue
            for sp in spans:
                t = parse_time(sp.get_text())
                if t is not None and d in w:
                    out[(slug, d)].append((t, ""))
                    seen.add((slug, d, t))
        meta[slug] = (title, fmt, url, notes)
    # this week's grid on the homepage (covers films whose pages omit the current week)
    for day in home.select(".daily-schedule"):
        h3 = day.select_one("h3")
        dm = re.search(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w* (\d{1,2})", h3.get_text() if h3 else "")
        if not dm:
            continue
        d = infer_date(month_num(dm.group(1)), int(dm.group(2)), w)
        if d not in w:
            continue
        for li in day.select(":scope > ul > li"):
            a = li.select_one(".details h3 a")
            if not a:
                continue
            slug = a["href"].split("/films/")[1].split("/")[0]
            meta.setdefault(slug, (a.get_text(strip=True), "", a["href"], {}))
            for t_el in li.select(".times a"):
                t = parse_time(t_el.get_text())
                if t is not None and (slug, d, t) not in seen:
                    out[(slug, d)].append((t, ""))
                    seen.add((slug, d, t))
    res = []
    for (slug, d), times in out.items():
        title, fmt, url, notes = meta[slug]
        res.append(Screening("ifc", d, title, sorted(set(times)), fmt, notes.get(d, ""), url))
    return res, []


# ======================================================================= MoMA
MOMA_JS = r"""
async (endIso) => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const months = {Jan:0,Feb:1,Mar:2,Apr:3,May:4,Jun:5,Jul:6,Aug:7,Sep:8,Oct:9,Nov:10,Dec:11};
  const lastDay = () => { const hs = [...document.querySelectorAll('a[href*="/calendar/events/"]')]
      .map(e => (e.closest('ul')?.parentElement?.previousElementSibling?.innerText || '').replace(/\s+/g,' ').trim());
      return hs.length ? hs[hs.length-1] : '' };
  for (let i = 0; i < 20; i++) {
    const b = [...document.querySelectorAll('button')].find(b => /show more events/i.test(b.innerText));
    if (!b) break;
    const m = lastDay().match(/(\w{3}) (\d+)$/);
    if (m) { const now = new Date(); let d = new Date(now.getFullYear(), months[m[1]], +m[2]);
             if (d < new Date(now.getFullYear(), now.getMonth()-6, 1)) d.setFullYear(d.getFullYear()+1);
             if (d.toISOString().slice(0,10) > endIso) break; }
    b.click(); await sleep(2500);
  }
  return [...document.querySelectorAll('a[href*="/calendar/events/"]')].map(e => ({
    day: (e.closest('ul')?.parentElement?.previousElementSibling?.innerText || '').replace(/\s+/g,' ').trim(),
    ps: [...e.querySelectorAll('p')].map(p => p.innerText.replace(/\s+/g,' ').trim()),
    href: e.getAttribute('href') }));
}
"""

MOMA_FMT_JS = r"""
async (ids) => Object.fromEntries(await Promise.all(ids.map(async id => {
  try { const t = (await (await fetch('/calendar/events/' + id)).text())
          .replace(/<script[\s\S]*?<\/script>/g,'').replace(/<[^>]+>/g,' ');
        return [id, [...new Set((t.match(/\b(35mm|16mm|70mm|DCP|4K|digital)\b/gi) || []).map(x => x.toLowerCase()))]]; }
  catch (e) { return [id, []]; } })))
"""


def _moma_format(hits):
    films = [f for f in ("70mm", "35mm", "16mm") if f in hits]
    if films:
        return "/".join(films)
    if "4k" in hits and "dcp" in hits:
        return "4K DCP"
    if "dcp" in hits:
        return "DCP"
    if "4k" in hits:
        return "4K"
    return "Digital" if "digital" in hits else ""


def moma(w: Window, ctx):
    page = ctx.new_page()
    page.goto("https://www.moma.org/calendar/?happening_filter=Films&locale=en&location=both",
              wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)
    rows = page.evaluate(MOMA_JS, w.end.isoformat())
    parsed, ids = [], set()
    for r in rows:
        m = re.search(r"(\w{3}) (\d{1,2})$", r["day"])
        if not m or not r["ps"]:
            continue
        d = infer_date(month_num(m.group(1)), int(m.group(2)), w)
        if d not in w:
            continue
        ps = r["ps"]
        title = ps[0]
        tm = re.match(r"^(.*?)\. ((?:19|20)\d\d)\. (?:Written and directed|Directed) by (.*)$", title)
        if tm:
            title = f"{tm.group(1)} ({tm.group(2)}, {tm.group(3)})"
        t = parse_time(ps[1]) if len(ps) > 1 else None
        venue = next((p for p in ps if p.startswith("MoMA") or p.startswith("Education")), "").replace("MoMA, ", "")
        extra = [p for p in ps[2:] if p != venue and not p.startswith("Film") and not p.startswith("MoMA")]
        note = " · ".join([venue] + extra) if venue else " · ".join(extra)
        eid = r["href"].rstrip("/").split("/")[-1]
        ids.add(eid)
        parsed.append((d, title, t, note, eid))
    fmts = page.evaluate(MOMA_FMT_JS, sorted(ids)) if ids else {}
    page.close()
    groups = defaultdict(list)
    for d, title, t, note, eid in parsed:
        if t is not None:
            groups[(d, title, note, eid)].append((t, ""))
    return [Screening("moma", d, title, sorted(set(ts)), _moma_format(fmts.get(eid, [])), note,
                      f"https://www.moma.org/calendar/events/{eid}") for (d, title, note, eid), ts in groups.items()], []


# ======================================================================= Roxy
def roxy(w: Window, ctx):
    url = "https://www.roxycinemanewyork.com/now-showing/"
    try:
        html = get(url)
        if "detailed-screening__card" not in html:
            raise RuntimeError("cards not in static HTML")
    except RuntimeError:
        html = browser_html(ctx, url, 4000)
    s = soup(html)
    groups = defaultdict(list)
    for c in s.select(".detailed-screening__card"):
        when = c.get("data-datetime", "")
        try:
            start = dt.datetime.fromisoformat(re.sub(r"\.\d+", "", when).replace("-0500", "-05:00").replace("-0400", "-04:00"))
        except ValueError:
            continue
        start = start.astimezone(NY)
        d = start.date()
        if d not in w:
            continue
        raw = c.select_one(".detailed-screening__title").get_text(" ", strip=True)
        link = c.select_one("a.cta--text-link")
        fmt, notes = "", []
        mm = re.search(r"-\s*(35|70|16)\s*MM", raw, re.I)
        if mm:
            fmt = f"{mm.group(1)}mm"
        title = raw
        if re.search(r"\|\s*shot by", title, re.I):
            notes.append("Shot By... series")
        title = re.sub(r"\s*\|\s*shot by\.*\s*", " ", title, flags=re.I)
        wm = re.search(r"\bwith ([A-Z][\w .'-]+)$", re.sub(r"\s*-\s*(35|70|16)\s*MM", "", title, flags=re.I), re.I)
        if wm:
            notes.append("With " + smart_title(wm.group(1)))
            title = title[:title.lower().rfind(" with ")]
        if re.search(r"\+\s*Q&A", title, re.I):
            notes.append("Q&A")
        title = re.sub(r"\s*\+\s*Q&A", "", title, flags=re.I)
        title = re.sub(r"\s*-\s*(35|70|16)\s*MM", "", title, flags=re.I)
        title = re.sub(r"\s+by abbas kiarostami$", "", title, flags=re.I).strip(" -|")
        groups[(d, smart_title(title), fmt, " · ".join(notes), link["href"] if link else url)].append(
            (start.hour * 60 + start.minute, ""))
    return [Screening("roxy", d, t, sorted(ts), f, n, u) for (d, t, f, n, u), ts in groups.items()], []


# ======================================================================= MoMI
def momi(w: Window, ctx):
    base = ("https://movingimage.org/events/list/?tribe_filterbar_category_custom%5B0%5D=253"
            "&tribe_filterbar_category_custom%5B1%5D=230&ical=1")
    feeds = [base, base + f"&tribe-bar-date={(w.start + dt.timedelta(days=7)).isoformat()}"]
    texts = []
    for u in feeds:
        try:
            texts.append(get(u))
        except RuntimeError:
            page = ctx.new_page()
            page.goto("https://movingimage.org/events/list/", wait_until="domcontentloaded", timeout=60000)
            texts.append(page.evaluate("async u => (await fetch(u)).text()", u))
            page.close()
    seen, groups = set(), defaultdict(list)
    for t in texts:
        t = re.sub(r"\r?\n[ \t]", "", t)
        for ev in t.split("BEGIN:VEVENT")[1:]:
            def g(k):
                m = re.search(rf"\n{k}[^:\n]*:(.*)", ev)
                return m.group(1).strip() if m else ""
            if "screening" not in g("CATEGORIES").lower():
                continue
            ds = g("DTSTART")
            m = re.match(r"(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})", ds)
            if not m:
                continue
            d = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            mins = int(m.group(4)) * 60 + int(m.group(5))
            title = g("SUMMARY").replace("\\,", ",").replace("\\;", ";")
            if d not in w or (d, mins, title) in seen:
                continue
            seen.add((d, mins, title))
            desc = g("DESCRIPTION").replace("\\,", ",").replace("\\n", " ")
            note = ""
            pm = re.search(r"\s*\(?\bwith (.+?) in person\)?$", title, re.I)
            if pm:
                note = f"{pm.group(1)} in person"
                title = title[:pm.start()].strip()
            im = re.search(r"\(?(introduced by [^)]+)\)?$", title, re.I)
            if im:
                note = im.group(1)[0].upper() + im.group(1)[1:]
                title = title[:im.start()].strip()
            if not note:
                qm = re.search(r"(Q&A with [^.]{3,60}|introduced by [^.]{3,60})", desc, re.I)
                note = qm.group(1).strip() if qm else ""
            groups[(d, title, find_format(desc), note, g("URL"))].append((mins, ""))
    return [Screening("momi", d, t, sorted(ts), f, n, u) for (d, t, f, n, u), ts in groups.items()], []


# =================================================================== Angelika
ANGELIKA_JS = r"""
async (args) => {
  const [lastMd, maxDays] = args;
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const re = /^(TODAY|TOMORROW|[A-Z]+DAY)\s*,?\s*(\d+\/\d+)$/;
  const dates = () => { const m = new Map(); document.querySelectorAll('.slick-slide:not(.slick-cloned) .wrapper').forEach(w => {
      const t = (w.innerText || '').trim().replace(/\s+/g, ' '); const x = t.match(re); if (x && !m.has(x[2])) m.set(x[2], w) }); return m };
  const out = [];
  for (const md of [...dates().keys()].slice(0, maxDays)) {
    dates().get(md)?.click(); await sleep(2800);
    document.querySelectorAll('.movie-details-section-new').forEach(b => {
      const a = b.querySelector('h3 a'); const sw = b.querySelector('.session-wrapper'); if (!a || !sw) return;
      let label = ''; const times = [];
      sw.innerText.split('\n').map(s => s.trim()).filter(Boolean).forEach(l => {
        if (/^\d{1,2}:\d\d [AP]M$/.test(l)) times.push([l, label]); else label = l; });
      out.push({md, title: a.innerText.trim(), href: a.href, times});
    });
    if (md === lastMd) break;
  }
  return out;
}
"""


def _angelika(theater: str, url: str, w: Window, ctx):
    page = ctx.new_page()
    page.set_default_timeout(180000)
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_selector(".slick-slide .wrapper", timeout=30000)
    page.wait_for_timeout(2000)
    rows = page.evaluate(ANGELIKA_JS, [f"{w.end.month}/{w.end.day}", 20])
    page.close()
    groups = defaultdict(list)
    for r in rows:
        mo, da = map(int, r["md"].split("/"))
        d = infer_date(mo, da, w)
        if d not in w:
            continue
        title = re.sub(r"\s+IN (35|70)MM$", "", r["title"], flags=re.I)
        title_fmt = (re.search(r"IN (35|70)MM$", r["title"], re.I) or [None, None])[1]
        labelled = any(re.search(r"(35|70)MM", lab, re.I) for _, lab in r["times"])
        for t, label in r["times"]:
            L = label.upper()
            fm = re.search(r"(35|70)MM", L)
            fmt = f"{fm.group(1)}mm" if fm else (f"{title_fmt}mm" if title_fmt and not labelled else "")
            tag, note = "", ""
            if "OPEN CAPTION" in L:
                tag = "OC"
            elif "SUBTITLE" in L:
                note = "English subtitles"
            elif "Q&A" in L:
                tag = "Q&A"
            elif L and not fm:
                note = smart_title(label)
            groups[(d, smart_title(title), fmt, note, r["href"])].append((parse_time(t), tag))
    return [Screening(theater, d, t, sorted(set(ts)), f, n, u) for (d, t, f, n, u), ts in groups.items()], []


def angelika(w: Window, ctx):
    return _angelika("angelika", "https://angelikafilmcenter.com/nyc/now-playing", w, ctx)


def villageeast(w: Window, ctx):
    return _angelika("villageeast", "https://angelikafilmcenter.com/villageeast/now-playing", w, ctx)


SOURCES = {"paris": paris, "filmforum": filmforum, "metrograph": metrograph, "filmlinc": filmlinc, "ifc": ifc,
           "moma": moma, "roxy": roxy, "momi": momi, "angelika": angelika, "villageeast": villageeast}
