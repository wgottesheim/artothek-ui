#!/usr/bin/env python3
"""
Artothek scraper — fetches artwork metadata from diekunstsammlung.at,
updates artworks.csv, and regenerates artworks.html gallery.
"""

import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.diekunstsammlung.at/InternetKunstsammlung/internetneu"
ENDPOINT = f"{BASE_URL}/App_Artothek.jsp"
THUMB_URL = f"{BASE_URL}/ImageCelumServlet?id={{id}}&thumb=J"
FULL_URL  = f"{BASE_URL}/ImageCelumServlet/xxx.jpg?id={{id}}"

CSV_PATH  = Path(__file__).parent / "artworks.csv"
HTML_PATH = Path(__file__).parent / "artworks.html"

CSV_FIELDS = [
    "artwork_id", "artist_id", "artist_name",
    "title", "year", "technique", "inventory_number",
    "image_width_cm", "image_height_cm",
    "frame_width_cm", "frame_height_cm",
    "is_available", "loaned_until",
    "thumbnail_url", "full_image_url",
    "first_seen_at", "last_seen_at", "last_updated_at",
]

# Fields that, when changed, trigger last_updated_at bump
TRACKED_FIELDS = {
    "artist_name", "title", "year", "technique", "inventory_number",
    "image_width_cm", "image_height_cm", "frame_width_cm", "frame_height_cm",
    "is_available", "loaned_until",
}

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0 (compatible; artothek-scraper/1.0)"})


# ── Fetching ──────────────────────────────────────────────────────────────────

def fetch_batch(loaded_ids: list[str]) -> str:
    data = {
        "ajaxReqMarker": "J",
        "loadedImageIds": ", ".join(loaded_ids),
        "grafik": "on",
        "Gemaelde": "on",
        "GemaeldeKlein": "on",
    }
    resp = SESSION.post(ENDPOINT, data=data, timeout=30)
    resp.raise_for_status()
    return resp.text


def fetch_all_artworks() -> list[BeautifulSoup]:
    """Paginate via AJAX until no new items are returned."""
    all_tags: list[BeautifulSoup] = []
    loaded_ids: list[str] = []

    while True:
        html = fetch_batch(loaded_ids)
        soup = BeautifulSoup(html, "html.parser")
        tags = soup.find_all("a", class_="fancybox-artothek")

        new_tags = [t for t in tags if t.get("id") not in loaded_ids]
        if not new_tags:
            break

        all_tags.extend(new_tags)
        loaded_ids.extend(t["id"] for t in new_tags)
        print(f"  fetched {len(new_tags)} new items (total so far: {len(all_tags)})", flush=True)

    return all_tags


# ── Parsing ───────────────────────────────────────────────────────────────────

def _parse_dims(text: str, label: str) -> tuple[str, str]:
    # Format on the website is "HxW" (height first), so group 2 is width.
    m = re.search(rf"{label}[:\s]+(\d+(?:[.,]\d+)?)\s*[xX×]\s*(\d+(?:[.,]\d+)?)", text)
    if m:
        return m.group(2).replace(",", "."), m.group(1).replace(",", ".")
    return "", ""


def parse_bildinfo(tag: BeautifulSoup) -> dict:
    artwork_id = tag.get("id", "")
    bildinfo_raw = tag.get("bildinfo", "")

    info_soup = BeautifulSoup(bildinfo_raw, "html.parser")
    text = info_soup.get_text(separator="\n")
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    # Artist name and ID
    artist_link = info_soup.find("a", href=re.compile(r"App_Kuenstler\.jsp"))
    artist_name = artist_link.get_text(strip=True) if artist_link else ""
    artist_id = ""
    if artist_link:
        m = re.search(r"kid=(\d+)", artist_link.get("href", ""))
        if m:
            artist_id = m.group(1)

    # Second line: "Title, Year, Technique, INV.:XXXX"
    # Artist name is line 0; the rest follows
    info_lines = [l for l in lines if l != artist_name]
    detail_line = info_lines[0] if info_lines else ""

    # Inventory number
    inv_m = re.search(r"INV\.?:?\s*(\S+)", detail_line)
    inventory_number = inv_m.group(1).rstrip(",") if inv_m else ""

    # Strip inventory from detail line before splitting title/year/technique
    clean_detail = re.sub(r",?\s*INV\.?:?\s*\S+", "", detail_line).strip().strip(",")
    parts = [p.strip() for p in clean_detail.split(",")]
    title = parts[0] if parts else ""
    year  = parts[1] if len(parts) > 1 else ""
    technique = ", ".join(parts[2:]) if len(parts) > 2 else ""

    # Dimensions
    full_text = "\n".join(lines)
    img_w, img_h = _parse_dims(full_text, r"Bildma[ßs]")
    frm_w, frm_h = _parse_dims(full_text, r"Rahmenma[ßs]")

    # Availability
    is_available = "True" if re.search(r"entlehnbar", full_text, re.IGNORECASE) else "False"
    loaned_m = re.search(r"entlehnt\s+bis\s+(.+)", full_text, re.IGNORECASE)
    loaned_until = loaned_m.group(1).strip() if loaned_m else ""

    return {
        "artwork_id":       artwork_id,
        "artist_id":        artist_id,
        "artist_name":      artist_name,
        "title":            title,
        "year":             year,
        "technique":        technique,
        "inventory_number": inventory_number,
        "image_width_cm":   img_w,
        "image_height_cm":  img_h,
        "frame_width_cm":   frm_w,
        "frame_height_cm":  frm_h,
        "is_available":     is_available,
        "loaned_until":     loaned_until,
        "thumbnail_url":    THUMB_URL.format(id=artwork_id),
        "full_image_url":   FULL_URL.format(id=artwork_id),
    }


# ── CSV upsert ────────────────────────────────────────────────────────────────

def load_existing_csv() -> dict[str, dict]:
    if not CSV_PATH.exists():
        return {}
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        return {row["artwork_id"]: row for row in csv.DictReader(f)}


def upsert(existing: dict[str, dict], scraped: list[dict], now: str) -> tuple[dict[str, dict], int, int]:
    new_count = changed_count = 0
    for item in scraped:
        aid = item["artwork_id"]
        if aid not in existing:
            item["first_seen_at"] = now
            item["last_seen_at"]  = now
            item["last_updated_at"] = now
            existing[aid] = item
            new_count += 1
        else:
            old = existing[aid]
            changed = any(item.get(f) != old.get(f) for f in TRACKED_FIELDS)
            for f in CSV_FIELDS:
                if f not in ("first_seen_at", "last_seen_at", "last_updated_at"):
                    old[f] = item.get(f, old.get(f, ""))
            old["last_seen_at"] = now
            if changed:
                old["last_updated_at"] = now
                changed_count += 1
    return existing, new_count, changed_count


def write_csv(records: dict[str, dict]) -> None:
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records.values())


# ── HTML gallery ──────────────────────────────────────────────────────────────

HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Artothek – Die Kunstsammlung OÖ</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: system-ui, sans-serif; background: #f5f5f0; color: #222; }

  header {
    position: sticky; top: 0; z-index: 10;
    background: #fff; border-bottom: 1px solid #ddd;
    padding: 12px 16px; display: flex; flex-wrap: wrap; gap: 12px; align-items: center;
  }
  header h1 { font-size: 1rem; font-weight: 600; white-space: nowrap; }
  #count { font-size: 0.85rem; color: #666; margin-left: auto; white-space: nowrap; }

  .filters { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
  .filters input[type=text] {
    padding: 5px 10px; border: 1px solid #ccc; border-radius: 6px;
    font-size: 0.85rem; min-width: 180px;
  }
  .filters label { font-size: 0.85rem; display: flex; align-items: center; gap: 5px; cursor: pointer; }
  .radio-group { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
  .filters .dim-group { display: flex; align-items: center; gap: 4px; font-size: 0.82rem; }
  .filters .dim-group input[type=number] {
    width: 56px; padding: 4px 6px; border: 1px solid #ccc; border-radius: 6px;
    font-size: 0.82rem; text-align: right;
  }
  .filters .sep { color: #aaa; }

  #gallery {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
    gap: 14px; padding: 16px;
  }

  .card {
    position: relative;
    background: #fff; border-radius: 8px; overflow: hidden;
    box-shadow: 0 1px 4px rgba(0,0,0,.08); transition: box-shadow .15s;
    display: flex; flex-direction: column;
  }
  .wish-btn {
    position: absolute; top: 6px; right: 6px;
    background: rgba(255,255,255,0.85); border: none; border-radius: 50%;
    width: 28px; height: 28px; font-size: 1rem; line-height: 28px;
    text-align: center; cursor: pointer; color: #bbb;
    box-shadow: 0 1px 3px rgba(0,0,0,.15); padding: 0; z-index: 1;
  }
  .wish-btn.active { color: #e33; }
  .card:hover { box-shadow: 0 4px 12px rgba(0,0,0,.14); }
  .card a.thumb { display: block; background: #eee; aspect-ratio: 1; overflow: hidden; }
  .card a.thumb img { width: 100%; height: 100%; object-fit: contain; loading: lazy; }
  .card .info { padding: 8px 10px 10px; flex: 1; display: flex; flex-direction: column; gap: 3px; }
  .card .artist { font-weight: 600; font-size: 0.82rem; }
  .card .title  { font-size: 0.78rem; color: #444; }
  .card .meta   { font-size: 0.74rem; color: #888; margin-top: 2px; }
  .card .badges { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 5px; }
  .badge {
    font-size: 0.7rem; padding: 2px 7px; border-radius: 10px;
    font-weight: 500; white-space: nowrap;
  }
  .badge-available { background: #d1fae5; color: #065f46; }
  .badge-loaned    { background: #fee2e2; color: #991b1b; }
  .badge-dim       { background: #e0e7ff; color: #3730a3; }
  .inv { font-size: 0.72rem; color: #999; margin-top: 4px; display: block; }

  #empty { display: none; text-align: center; padding: 60px 20px; color: #999; }
</style>
</head>
<body>
<header>
  <h1>Artothek – Die Kunstsammlung OÖ</h1>
  <div class="filters">
    <input type="text" id="search" placeholder="Künstler/in suchen…">
    <div class="radio-group">
      <label><input type="radio" name="availability" value="all" checked> Alle</label>
      <label><input type="radio" name="availability" value="available"> Nur entlehnbar</label>
      <label><input type="radio" name="availability" value="soon"> Entlehnbar oder zurück in 30 Tagen</label>
    </div>
    <label><input type="checkbox" id="onlyWishlist"> Merkliste <span id="wishCount"></span></label>
    <label><input type="checkbox" id="onlyLandscape"> Nur Querformat (B > H)</label>
    <div class="dim-group">
      Breite <input type="number" id="minW" min="0" placeholder="min">
      <span class="sep">–</span>
      <input type="number" id="maxW" min="0" placeholder="max"> cm
    </div>
    <div class="dim-group">
      Höhe <input type="number" id="minH" min="0" placeholder="min">
      <span class="sep">–</span>
      <input type="number" id="maxH" min="0" placeholder="max"> cm
    </div>
  </div>
  <span id="count"></span>
  <span id="scrape-ts" style="font-size:0.75rem;color:#aaa;white-space:nowrap">Stand: __SCRAPE_TIMESTAMP__</span>
</header>

<div id="gallery"></div>
<div id="empty">Keine Werke gefunden.</div>

<script>
const ARTWORKS = __ARTWORKS_JSON__;

let wishlist = new Set();

async function loadWishlist() {
  const ids = await fetch('/api/wishlist').then(r => r.json());
  wishlist = new Set(ids);
  updateWishCount();
  filter();
}
async function toggleWishlist(id) {
  if (wishlist.has(id)) {
    await fetch('/api/wishlist/' + id, { method: 'DELETE' });
    wishlist.delete(id);
  } else {
    await fetch('/api/wishlist', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ artwork_id: id })
    });
    wishlist.add(id);
  }
  updateWishCount();
  filter();
}
function updateWishCount() {
  document.getElementById('wishCount').textContent = wishlist.size ? '(' + wishlist.size + ')' : '';
}

const gallery        = document.getElementById('gallery');
const countEl        = document.getElementById('count');
const emptyEl        = document.getElementById('empty');
const searchEl       = document.getElementById('search');
const onlyLandEl     = document.getElementById('onlyLandscape');
const minWEl         = document.getElementById('minW');
const maxWEl         = document.getElementById('maxW');
const minHEl         = document.getElementById('minH');
const maxHEl         = document.getElementById('maxH');

function fmt(w, h) {
  if (!w && !h) return '';
  if (w && h) return w + '×' + h + ' cm';
  return (w || h) + ' cm';
}

function renderCard(a) {
  const avBadge = a.is_available === 'True'
    ? '<span class="badge badge-available">entlehnbar</span>'
    : (a.loaned_until
        ? '<span class="badge badge-loaned">entlehnt bis ' + a.loaned_until + '</span>'
        : '<span class="badge badge-loaned">entlehnt</span>');
  const dimBadge = a.image_width_cm
    ? '<span class="badge badge-dim">' + fmt(a.image_width_cm, a.image_height_cm) + '</span>'
    : '';
  const tech = [a.year, a.technique].filter(Boolean).join(', ');
  const inv  = a.inventory_number ? '<span class="inv">INV ' + a.inventory_number + '</span>' : '';
  const wished = wishlist.has(a.artwork_id);
  return `<div class="card">
    <button class="wish-btn${wished ? ' active' : ''}" data-id="${a.artwork_id}" title="Zur Merkliste">♥</button>
    <a class="thumb" href="${a.full_image_url}" target="_blank" rel="noopener">
      <img src="${a.thumbnail_url}" alt="${a.title}" loading="lazy">
    </a>
    <div class="info">
      <div class="artist">${a.artist_name}</div>
      <div class="title">${a.title}</div>
      ${tech ? '<div class="meta">' + tech + '</div>' : ''}
      <div class="badges">${avBadge}${dimBadge}</div>
      ${inv}
    </div>
  </div>`;
}

function numVal(el) {
  const v = el.value.trim();
  return v === '' ? null : parseFloat(v);
}

function parseDMY(s) {
  if (!s) return null;
  const [d, m, y] = s.split('.');
  return new Date(+y, +m - 1, +d);
}

function filter() {
  const q        = searchEl.value.trim().toLowerCase();
  const avMode   = document.querySelector('input[name="availability"]:checked').value;
  const onlyWish = document.getElementById('onlyWishlist').checked;
  const onlyLand = onlyLandEl.checked;
  const today    = new Date(); today.setHours(0, 0, 0, 0);
  const in30     = new Date(today); in30.setDate(in30.getDate() + 30);
  const minW = numVal(minWEl), maxW = numVal(maxWEl);
  const minH = numVal(minHEl), maxH = numVal(maxHEl);

  const visible = ARTWORKS.filter(a => {
    if (onlyWish && !wishlist.has(a.artwork_id)) return false;
    if (avMode === 'available' && a.is_available !== 'True') return false;
    if (avMode === 'soon') {
      const returnDate = parseDMY(a.loaned_until);
      if (a.is_available !== 'True' && (returnDate === null || returnDate > in30)) return false;
    }
    if (q && !a.artist_name.toLowerCase().includes(q)) return false;
    const w = a.image_width_cm  ? parseFloat(a.image_width_cm)  : null;
    const h = a.image_height_cm ? parseFloat(a.image_height_cm) : null;
    if (onlyLand && (w === null || h === null || w <= h)) return false;
    if (minW !== null && (w === null || w < minW)) return false;
    if (maxW !== null && (w === null || w > maxW)) return false;
    if (minH !== null && (h === null || h < minH)) return false;
    if (maxH !== null && (h === null || h > maxH)) return false;
    return true;
  });

  gallery.innerHTML = visible.map(renderCard).join('');
  countEl.textContent = visible.length + ' / ' + ARTWORKS.length + ' Werke';
  emptyEl.style.display = visible.length === 0 ? 'block' : 'none';
}

gallery.addEventListener('click', e => {
  const btn = e.target.closest('.wish-btn');
  if (btn) { e.preventDefault(); toggleWishlist(btn.dataset.id); }
});

document.querySelectorAll('input[name="availability"]').forEach(el => el.addEventListener('change', filter));
document.getElementById('onlyWishlist').addEventListener('change', filter);
[searchEl, onlyLandEl, minWEl, maxWEl, minHEl, maxHEl].forEach(el => el.addEventListener('input', filter));
loadWishlist();
</script>
</body>
</html>
"""


def generate_html(records: dict[str, dict], timestamp: str) -> None:
    artworks = list(records.values())
    json_data = json.dumps(artworks, ensure_ascii=False, indent=None)
    html = HTML_TEMPLATE.replace("__ARTWORKS_JSON__", json_data)
    html = html.replace("__SCRAPE_TIMESTAMP__", timestamp)
    HTML_PATH.write_text(html, encoding="utf-8")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print("Loading existing CSV…")
    existing = load_existing_csv()
    print(f"  {len(existing)} artworks in existing CSV")

    print("Fetching artworks from diekunstsammlung.at…")
    tags = fetch_all_artworks()
    print(f"  total fetched: {len(tags)}")

    print("Parsing metadata…")
    scraped = [parse_bildinfo(t) for t in tags]

    print("Upserting CSV…")
    records, new_count, changed_count = upsert(existing, scraped, now)
    write_csv(records)
    print(f"  {new_count} new, {changed_count} changed, {len(records)} total")

    # Artworks not seen this run
    seen_ids = {item["artwork_id"] for item in scraped}
    missing = [aid for aid in existing if aid not in seen_ids]
    if missing:
        print(f"  {len(missing)} artworks not seen this run (may have been removed)")

    print("Generating artworks.html…")
    generate_html(records, now)
    print(f"Done. Wrote {HTML_PATH}.")


if __name__ == "__main__":
    main()
