# Artothek UI

Scrapes artwork metadata from [Die Kunstsammlung OÖ – Artothek](https://www.diekunstsammlung.at/InternetKunstsammlung/internetneu/App_Artothek.jsp) and serves a filterable browser gallery with a server-side wishlist.

## Architecture

```
scraper/scraper.py   — fetches artworks via AJAX, writes artworks.csv + artworks.html
app/app.py           — Flask app: serves the gallery, exposes /api/wishlist (SQLite)
data/wishlist.db     — SQLite wishlist, created at runtime
deploy/              — systemd units + LXC bootstrap script
```

The scraper and web app are decoupled: the scraper regenerates `scraper/artworks.html` (a self-contained file with all artwork data embedded as JSON) and Flask serves it as a static file. The only dynamic piece is `/api/wishlist`.

The wishlist is global — one shared list for all devices, no auth required. Suitable for a single-user home server.

## Local development

```bash
# 1. Generate the gallery HTML (first time, or to refresh data)
cd scraper && ./scrape.sh

# 2. Run the Flask dev server
cd app
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python app.py        # http://localhost:8080
```

Port defaults to 5000, overridable via `PORT` env var.

## Deployment (Proxmox LXC, Debian 12)

```bash
# On the container, as root:
git clone <repo> /opt/artothek-ui
bash /opt/artothek-ui/deploy/setup.sh
```

`setup.sh` creates an `artothek` system user, sets up two separate venvs (scraper + app), runs the first scrape, and installs three systemd units:

- `artothek-web.service` — gunicorn on `0.0.0.0:8080`
- `artothek-scraper.service` — oneshot scraper
- `artothek-scraper.timer` — fires daily at 06:00 (`Persistent=true`)

To change the port: edit `Environment=PORT=5000` in `deploy/artothek-web.service`.

## Gallery filters

| Filter | Description |
|--------|-------------|
| Künstler/in suchen | Text search on artist name |
| Nur entlehnbare Werke | Show only artworks currently available to borrow |
| Nur Querformat (B > H) | Show only landscape-oriented artworks (width > height) |
| Breite min / max | Filter by image width in cm |
| Höhe min / max | Filter by image height in cm |

Clicking a thumbnail opens the full-size image in a new tab. The inventory number (INV) shown on each card is required when contacting the Artothek to borrow an artwork.

## Wishlist API

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/wishlist` | Returns list of wishlisted `artwork_id`s |
| `POST` | `/api/wishlist` | Add `{"artwork_id": "..."}` to wishlist |
| `DELETE` | `/api/wishlist/<id>` | Remove an artwork from wishlist |

## CSV columns

| Column | Description |
|--------|-------------|
| `artwork_id` | Numeric ID used by the website's image servlet |
| `artist_id` | Artist page ID (`kid=` parameter) |
| `artist_name` | Artist display name |
| `title` | Artwork title |
| `year` | Year of creation |
| `technique` | Medium / technique |
| `inventory_number` | Inventory number — required to borrow the artwork |
| `image_width_cm` | Image width in cm |
| `image_height_cm` | Image height in cm |
| `frame_width_cm` | Frame width in cm (not always present) |
| `frame_height_cm` | Frame height in cm (not always present) |
| `is_available` | `True` if currently available to borrow |
| `loaned_until` | Return date if currently on loan (e.g. `27.09.2026`) |
| `thumbnail_url` | URL of the thumbnail image |
| `full_image_url` | URL of the full-size image |
| `first_seen_at` | ISO 8601 UTC timestamp of first scrape |
| `last_seen_at` | ISO 8601 UTC timestamp of most recent scrape |
| `last_updated_at` | ISO 8601 UTC timestamp of last metadata change |

> **Note on dimensions:** The website lists dimensions as `Bildmaß: HxW` (height × width). The scraper parses them correctly into separate `_width_cm` and `_height_cm` columns.

## Example DuckDB queries

```sql
-- Available artworks, small enough to fit on a 100cm wall
SELECT artist_name, title, image_width_cm, image_height_cm, inventory_number
FROM 'scraper/artworks.csv'
WHERE is_available = 'True'
  AND image_width_cm::FLOAT <= 100
ORDER BY artist_name;

-- Landscape artworks currently on loan with known return dates
SELECT artist_name, title, loaned_until, inventory_number
FROM 'scraper/artworks.csv'
WHERE is_available = 'False'
  AND loaned_until != ''
  AND image_width_cm::FLOAT > image_height_cm::FLOAT
ORDER BY loaned_until;

-- Artworks added since a specific date
SELECT artist_name, title, first_seen_at
FROM 'scraper/artworks.csv'
WHERE first_seen_at >= '2026-01-01'
ORDER BY first_seen_at DESC;
```
