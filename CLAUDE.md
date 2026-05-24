# Artothek UI — Claude Context

## What this project does

Scrapes artwork metadata from [Die Kunstsammlung OÖ – Artothek](https://www.diekunstsammlung.at/InternetKunstsammlung/internetneu/App_Artothek.jsp) and serves a filterable browser gallery with a server-side wishlist.

## Architecture

```
scraper/scraper.py   — fetches artworks via AJAX, writes artworks.csv + artworks.html
app/app.py           — Flask app: serves artworks.html, exposes /api/wishlist (SQLite)
data/wishlist.db     — SQLite, created at runtime, one global wishlist (no auth)
deploy/              — systemd units + LXC bootstrap script
```

The scraper and the web app are decoupled: the scraper regenerates `scraper/artworks.html` (a self-contained file with all artwork data embedded as JSON) and Flask just serves it as a static file. The only dynamic piece is `/api/wishlist`.

## Local development

```bash
# 1. Generate gallery HTML (first time or to refresh data)
cd scraper && ./scrape.sh

# 2. Run Flask dev server
cd app
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python app.py        # http://localhost:5000
```

Port defaults to 5000, overridable via `PORT` env var.

## Deployment (Proxmox LXC, Debian 12)

```bash
# On the container, as root:
git clone <repo> /opt/artothek-ui
bash /opt/artothek-ui/deploy/setup.sh
```

`setup.sh` creates an `artothek` system user, sets up two separate venvs (scraper + app), runs the first scrape, and installs three systemd units:
- `artothek-web.service` — gunicorn on `0.0.0.0:$PORT`
- `artothek-scraper.service` — oneshot scraper
- `artothek-scraper.timer` — fires daily at 06:00 (`Persistent=true`)

To change the port: edit `Environment=PORT=5000` in `deploy/artothek-web.service`.

## Key design decisions

- **Two venvs**: `scraper/.venv` (requests, beautifulsoup4) and `app/.venv` (flask, gunicorn) are kept separate so scraper deps don't pollute the web app.
- **`data/` directory**: `wishlist.db` lives here, not inside `app/`, so it survives `git pull` / app code updates.
- **Global wishlist**: no sessions, no auth — one shared wishlist for all devices. Suitable for a single-user home server.
- **Static HTML gallery**: the scraper embeds all artwork data as a JSON blob in `artworks.html`. Flask serves this file unchanged; no DB reads on page load.

## scraper/scraper.py internals

- `HTML_TEMPLATE` (line ~195) is a Python string containing the full HTML+JS gallery. `__ARTWORKS_JSON__` is replaced at generation time.
- Wishlist JS uses `async fetch()` calls to `/api/wishlist` — not localStorage.
- Dimensions on the source website are listed as `Bildmaß: HxW` (height first); `_parse_dims()` swaps them into `width, height` columns.
