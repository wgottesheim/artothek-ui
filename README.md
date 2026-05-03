# Artothek Scraper

Scrapes artwork metadata from [Die Kunstsammlung OÖ – Artothek](https://www.diekunstsammlung.at/InternetKunstsammlung/internetneu/App_Artothek.jsp) and produces:

- **`artworks.csv`** — full metadata table, queryable with DuckDB
- **`artworks.html`** — self-contained browser gallery with filters and live thumbnails

## Usage

```bash
cd scraper
./scrape.sh
```

Then open `scraper/artworks.html` in a browser.

The script creates a Python virtualenv on first run and installs dependencies automatically. Subsequent runs reuse it.

## Re-running / updating

Run `./scrape.sh` any time to refresh the data. The scraper:

- Updates availability status and any changed fields
- Appends newly added artworks (`first_seen_at` is set once and never overwritten)
- Keeps removed artworks in the CSV — they're identifiable by a stale `last_seen_at`
- Regenerates `artworks.html` with the latest data

## HTML gallery filters

| Filter | Description |
|--------|-------------|
| Künstler/in suchen | Text search on artist name |
| Nur entlehnbare Werke | Show only artworks currently available to borrow |
| Nur Querformat (B > H) | Show only landscape-oriented artworks (width > height) |
| Breite min / max | Filter by image width in cm |
| Höhe min / max | Filter by image height in cm |

Clicking a thumbnail opens the full-size image in a new tab. The inventory number (INV) shown on each card is required when contacting the Artothek to rent an artwork.

## CSV columns

| Column | Description |
|--------|-------------|
| `artwork_id` | Numeric ID used by the website's image servlet |
| `artist_id` | Artist page ID (`kid=` parameter) |
| `artist_name` | Artist display name |
| `title` | Artwork title |
| `year` | Year of creation |
| `technique` | Medium / technique |
| `inventory_number` | Inventory number — required to rent the artwork |
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

## Scheduling (optional)

To refresh automatically every week, add a cron job:

```bash
crontab -e
# Add:
0 8 * * 1 /path/to/artothek-ui/scraper/scrape.sh >> /tmp/artothek-scrape.log 2>&1
```
