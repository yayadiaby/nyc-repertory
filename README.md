# NYC Repertory Screenings

A personal tracker for screenings at 10 New York theaters: Paris Theater, Film Forum, Metrograph,
Film at Lincoln Center, IFC Center, MoMA, Roxy Cinema, Museum of the Moving Image, Angelika New York
and Village East by Angelika.

## How it works
- `collector/` reads each theater's published schedule for the next 14 days (`collect.py` runs them all;
  `sources.py` has one function per theater).
- `.github/workflows/update.yml` runs the collector twice a day (about 7:20am and 3:20pm New York time),
  then publishes `site/` to GitHub Pages.
- `site/index.html` is the website: filters for theater, date, start time and format.

## When something breaks
If a theater fails or returns far fewer screenings than usual, the run opens (or comments on) an issue
labelled `collector-alert`, and GitHub emails you. The site keeps that theater's last good schedule and
marks it "couldn't be refreshed". The issue closes itself once every theater collects cleanly again.

## Run it by hand
Actions tab → "Update screenings" → "Run workflow".

## Run locally
    pip install -r requirements.txt && python -m playwright install chromium
    python collector/collect.py            # writes site/data.json
    python tests/test_offline.py           # parser checks, no network needed
