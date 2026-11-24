"""Collect two weeks of screenings from every theater, check each theater's health, write site/data.json.

Usage:  python collector/collect.py [--days 14] [--only paris,ifc] [--previous URL_OR_PATH]

Health rules (per theater):
  * error or zero screenings          -> FAILED: keep the last good data for that theater, flag it on the site, alert
  * under 40% of the last good count  -> WARNING: use the new data, alert so a human can look
Alerts are written to alerts.md; the GitHub workflow turns that into an issue (which emails you).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
import traceback
from collections import defaultdict
from pathlib import Path

from common import NY, THEATERS, UA, Window, format_category, get, time_label
from sources import SOURCES

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"


def load_previous(where: str | None) -> dict:
    if not where:
        return {}
    try:
        if where.startswith("http"):
            return get(where.rstrip("/") + "/data.json" if not where.endswith(".json") else where, json=True, tries=2)
        p = Path(where)
        return json.loads(p.read_text()) if p.exists() else {}
    except Exception as e:  # noqa: BLE001
        print(f"(no previous data: {e})")
        return {}


def to_rows(screenings):
    merged = {}
    for s in screenings:
        k = (s.theater, s.date, s.title, s.fmt, s.note, s.url)
        if k in merged:
            merged[k].times = sorted(set(merged[k].times + s.times))
        else:
            merged[k] = s
    rows = []
    for s in merged.values():
        times = sorted({(m, tag) for m, tag in s.times if m is not None}, key=lambda x: (x[0] + 1440 if x[0] < 300 else x[0]))
        if not times:
            continue
        rows.append({"t": s.theater, "d": s.date.isoformat(), "title": s.title, "fmt": s.fmt,
                     "fc": format_category(s.fmt), "note": s.note, "url": s.url,
                     "times": [[m, time_label(m), tag] for m, tag in times]})
    return rows


def count(rows):
    return sum(len(r["times"]) for r in rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--only", default="")
    ap.add_argument("--previous", default="")
    args = ap.parse_args()

    window = Window(args.days)
    now = dt.datetime.now(NY)
    prev = load_previous(args.previous)
    prev_rows = defaultdict(list)
    for r in prev.get("screenings", []):
        if r["d"] >= window.start.isoformat():
            prev_rows[r["t"]].append(r)
    prev_counts = prev.get("counts", {})
    last_success = dict(prev.get("last_success", {}))

    only = [x for x in args.only.split(",") if x] or list(SOURCES)
    rows, closed, stale, alerts, counts = [], [], {}, [], {}

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(user_agent=UA, locale="en-US", timezone_id="America/New_York",
                                  viewport={"width": 1280, "height": 1000})
        for tid in only:
            name = THEATERS[tid][0]
            t0 = time.time()
            try:
                got, shut = SOURCES[tid](window, ctx)
                new = to_rows(got)
                err = None
            except Exception as e:  # noqa: BLE001
                new, shut, err = [], [], f"{type(e).__name__}: {e}"
                traceback.print_exc()
            n, before = count(new), prev_counts.get(tid, 0)
            print(f"{name:28s} {n:4d} screenings  (last good {before:4d})  {time.time() - t0:5.1f}s"
                  + (f"  ERROR {err}" if err else ""))
            if err or n == 0:
                reason = err or "returned no screenings"
                rows += prev_rows.get(tid, [])
                counts[tid] = before
                stale[tid] = {"since": last_success.get(tid, ""), "reason": reason[:200]}
                since = last_success.get(tid)
                since = dt.datetime.fromisoformat(since).strftime("%b %-d, %-I:%M %p") if since else "an earlier run"
                alerts.append(f"- **{name}** failed: {reason[:300]}. Showing its last good data, from {since}.")
            else:
                rows += new
                closed += shut
                counts[tid] = n
                last_success[tid] = now.isoformat(timespec="minutes")
                if before >= 20 and n < 0.4 * before:
                    counts[tid] = before      # keep the normal level so the alert repeats until it recovers
                    alerts.append(f"- **{name}** returned {n} screenings, down from {before} last time. "
                                  f"The site may have changed; worth a look at {THEATERS[tid][2]}")
        browser.close()

    data = {
        "updated": now.isoformat(timespec="minutes"),
        "window": [window.start.isoformat(), window.end.isoformat()],
        "theaters": [{"id": k, "name": v[0], "area": v[1], "url": v[2]} for k, v in THEATERS.items()],
        "closed": closed,
        "stale": stale,
        "counts": counts,
        "last_success": last_success,
        "screenings": sorted(rows, key=lambda r: (r["d"], r["t"], r["times"][0][0] if r["times"] else 0)),
    }
    SITE.mkdir(exist_ok=True)
    (SITE / "data.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    print(f"\nwrote site/data.json: {count(rows)} screenings, {len(rows)} rows")

    alert_file = ROOT / "alerts.md"
    if alerts:
        alert_file.write_text("The screenings collector hit a problem on its "
                              f"{now.strftime('%a %b %-d, %-I:%M %p')} run:\n\n" + "\n".join(alerts) +
                              "\n\nThe website is still up; affected theaters show a \"may be out of date\" note.\n")
        print("\nALERTS:\n" + "\n".join(alerts))
    elif alert_file.exists():
        alert_file.unlink()
    return 0


if __name__ == "__main__":
    sys.exit(main())
