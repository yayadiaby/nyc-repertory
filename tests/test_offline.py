"""Offline checks of the parsers using page snippets captured from the real sites (Nov 2026).
Run:  python tests/test_offline.py
"""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "collector"))
import common  # noqa: E402
import sources  # noqa: E402
from common import Window, parse_time, smart_title, time_label  # noqa: E402

W = Window(14, start=dt.date(2026, 11, 24))
ok = 0


def check(cond, msg):
    global ok
    if not cond:
        raise AssertionError(msg)
    ok += 1


# ---- helpers
check(parse_time("7:00 PM") == 1140 and parse_time("9:50 PM ") == 1310 and parse_time("12:01 AM") == 1, "times")
check(parse_time("4:30 p.m.") == 990 and parse_time("12:20PM") == 740, "times 2")
check(parse_time("12:20", assume_pm=True) == 740 and parse_time("11:00", assume_pm=True) == 660
      and parse_time("8:15", assume_pm=True) == 1215, "film forum bare times")
check(time_label(1140) == "7p" and time_label(1215) == "8:15p" and time_label(1) == "12:01a", "labels")
check(smart_title("HANNAH AND HER SISTERS") == "Hannah and Her Sisters", smart_title("HANNAH AND HER SISTERS"))
check(smart_title("threeASFOUR: FULL CIRCLE") == "threeASFOUR: FULL CIRCLE", "mixed case kept")
check(sources._ff_clean_title("Zhang Yimou’s RAISE THE RED LANTERN") == "Raise the Red Lantern",
      sources._ff_clean_title("Zhang Yimou’s RAISE THE RED LANTERN"))
check(sources._ff_clean_title("Ingrid Bergman in JOAN OF ARC (1948) in 35mm") == "Joan of Arc (1948)",
      sources._ff_clean_title("Ingrid Bergman in JOAN OF ARC (1948) in 35mm"))

# ---- Film Forum ticketing calendar (month grid text, as rendered)
grid = """24
Tuesday 24 November 2026
GERMANY, YEAR ZERO
,
12:15PM
FATHERLAND - OPEN CAPTIONS
,
4:00PM
25
Wednesday 25 November 2026
ALL OF A SUDDEN
,
12:15PM"""
rows = sources._ff_parse_calendar(grid, W)
check(rows == [(dt.date(2026, 11, 24), "GERMANY, YEAR ZERO", 735, False),
               (dt.date(2026, 11, 24), "FATHERLAND", 960, True),
               (dt.date(2026, 11, 25), "ALL OF A SUDDEN", 735, False)], rows)
lst = """Wednesday, November 25, 2026
,
12:15PM
,
ALL OF A SUDDEN
,
Purchase"""
check(sources._ff_parse_calendar(lst, W) == [(dt.date(2026, 11, 25), "ALL OF A SUDDEN", 735, False)], "ff list view")

# ---- Metrograph (structure from metrograph.com/nyc/)
METRO = """<div class="calendar-list-day movies-grid" id="calendar-list-day-2026-11-24">
<div class="item film-thumbnail homepage-in-theater-movie"><a href="https://metrograph.com/film/9999001208-lost-highway/" class="image"></a>
<h4><a href="https://metrograph.com/film/9999001208-lost-highway/" class="title">Lost Highway</a></h4>
<div class="film-metadata">David Lynch / 1997 / 134min / 4K DCP</div>
<div class="showtimes"> <a href="https://t.metrograph.com/x?txtSessionId=31489">9:15pm</a> <a href="#">11:45pm</a></div></div>
<div class="item film-thumbnail"><h4><a href="https://metrograph.com/film/9999001925-asako-i-ii/" class="title">Asako I &amp; II</a></h4>
<div class="film-metadata">Ryusuke Hamaguchi / 2018 / 119min / DCP</div>
<div class="event">Introduction by filmmaker Kogonada on Saturday, November 7th</div>
<div class="showtimes"><a href="#">4:00pm</a></div></div></div>
<div class="calendar-list-day movies-grid" id="calendar-list-day-2026-11-26"><p>Metrograph will be closed for Thanksgiving.</p></div>
<div class="calendar-list-day movies-grid" id="calendar-list-day-2026-12-04">
<div class="item"><h4><a href="https://metrograph.com/film/9999005115-museum-hours/" class="title">Museum Hours</a></h4>
<div class="film-metadata">Jem Cohen / 2012 / 107min / DCP</div><div class="event">Q&amp;A with filmmaker Jem Cohen on Friday, December 4th</div>
<div class="showtimes"><a href="#">7:15pm</a></div></div></div>"""
common.get = sources.get = lambda url, **kw: METRO
res, closed = sources.metrograph(W, None)
by = {(s.title, s.date.day): s for s in res}
check(by[("Lost Highway", 24)].fmt == "4K DCP" and by[("Lost Highway", 24)].times == [(1275, ""), (1425, "")], "metro times")
check(by[("Asako I & II", 24)].note == "", "metro drops a note dated for another day")
check(by[("Museum Hours", 4)].note == "Q&A with filmmaker Jem Cohen", by[("Museum Hours", 4)].note)
check(closed and closed[0]["date"] == "2026-11-26", "metro closed day")

# ---- Roxy (structure from roxycinemanewyork.com/now-showing/)
ROXY = """<div class="detailed-screening__card" data-datetime="2026-11-28T18:45:00.000-0500">
<span class="detailed-screening__actions--time">6:45PM</span><h3 class="detailed-screening__title"> ANOTHER DAY IN PARADISE - 35MM WITH LARRY CLARK </h3>
<a class="detailed-screening__cta cta--text-link" href="https://www.roxycinemanewyork.com/screenings/another-day-in-paradise-35mm/">Read More</a></div>
<div class="detailed-screening__card" data-datetime="2026-11-27T19:00:00.000-0500"><h3 class="detailed-screening__title">THE FIN + Q&amp;A</h3>
<a class="cta--text-link" href="https://www.roxycinemanewyork.com/screenings/the-fin-qa/">Read More</a></div>
<div class="detailed-screening__card" data-datetime="2026-12-07T18:30:00.000-0500"><h3 class="detailed-screening__title">SONG TO SONG | SHOT BY...</h3>
<a class="cta--text-link" href="https://www.roxycinemanewyork.com/screenings/song-to-song/">Read More</a></div>"""
sources.get = lambda url, **kw: ROXY
res, _ = sources.roxy(W, None)
by = {s.title: s for s in res}
check(set(by) == {"Another Day in Paradise", "The Fin", "Song to Song"}, set(by))
check(by["Another Day in Paradise"].fmt == "35mm" and by["Another Day in Paradise"].note == "With Larry Clark", vars(by["Another Day in Paradise"]))
check(by["The Fin"].note == "Q&A" and by["Song to Song"].note == "Shot By... series", "roxy notes")
check(by["The Fin"].times == [(1140, "")], "roxy time")

# ---- IFC (homepage + film page structure)
IFC_HOME = """<a href="https://www.ifccenter.com/films/everytime/">Everytime</a>
<div class="daily-schedule tue"><h3>Tue Nov 24</h3><ul><li><div class="details"><h3><a href="https://www.ifccenter.com/films/the-host-2/">The Host</a></h3>
<ul class="times"><li><a href="#">12:55 PM </a></li><li><a href="#">3:35 PM </a></li></ul></div></li></ul></div>"""
IFC_EVERY = """<h1>Everytime</h1><p>Year 2026 Running Time 90 minutes Format DCP Distributor Kino</p>
<ul class="schedule-list"><li><div class="details"><span><strong>Tue Dec 1 :</strong></span>
<p><strong>Sneak preview + Q&amp;A w/ director Sandra Wollner after 6:30 show!</strong></p></div></li></ul>
<ul class="schedule-list"><li><div class="details"><p><strong>Tue Dec 01</strong></p><ul class="times"><li><span>6:30 pm</span></li></ul></div></li></ul>"""
IFC_HOST = """<h1>The Host</h1><p>Format DCP Distributor Magnolia</p>
<ul class="schedule-list"><li><div class="details"><p><strong>Tue Nov 24</strong></p><ul class="times"><li><span>12:55 pm</span></li><li><span>9:25 pm</span></li></ul></div></li></ul>"""
pages = {"https://www.ifccenter.com/": IFC_HOME, "https://www.ifccenter.com/films/everytime/": IFC_EVERY,
         "https://www.ifccenter.com/films/the-host-2/": IFC_HOST}
sources.get = lambda url, **kw: pages[url]
res, _ = sources.ifc(W, None)
by = {(s.title, s.date.day): s for s in res}
check(by[("Everytime", 1)].times == [(1110, "")] and by[("Everytime", 1)].fmt == "DCP", vars(by[("Everytime", 1)]))
check("Sandra Wollner" in by[("Everytime", 1)].note, "ifc guest note")
check(by[("The Host", 24)].times == [(775, ""), (935, ""), (1285, "")], by[("The Host", 24)].times)

# ---- MoMI iCalendar feed
ICS = ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nDTSTART;TZID=America/New_York:20261203T190000\r\n"
       "SUMMARY:Full Metal Jacket with Vincent D'Onofrio and Matthew Modine in person\r\n"
       "DESCRIPTION:Dir. Stanley Kubrick. 1987\\, 116 mins. 35mm. With Matthew Modine.\r\n"
       "URL:https://movingimage.org/event/fmj/\r\nCATEGORIES:Screening\r\nEND:VEVENT\r\n"
       "BEGIN:VEVENT\r\nDTSTART;TZID=America/New_York:20261128T180000\r\nSUMMARY:MoMI After Dark\r\n"
       "CATEGORIES:Event\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
sources.get = lambda url, **kw: ICS
res, _ = sources.momi(W, None)
check(len(res) == 1 and res[0].title == "Full Metal Jacket" and res[0].fmt == "35mm"
      and res[0].note == "Vincent D'Onofrio and Matthew Modine in person" and res[0].times == [(1140, "")], [vars(r) for r in res])

print(f"all {ok} offline checks passed")
