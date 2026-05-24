from pathlib import Path
import os
import sqlite3
from flask import Flask, jsonify, request, send_file, abort

BASE_DIR  = Path(__file__).parent.parent
DB_PATH   = BASE_DIR / "data" / "wishlist.db"
HTML_PATH = BASE_DIR / "scraper" / "artworks.html"

app = Flask(__name__)


def get_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.execute("CREATE TABLE IF NOT EXISTS wishlist (artwork_id TEXT PRIMARY KEY)")
    db.commit()
    return db


@app.route("/")
def index():
    if not HTML_PATH.exists():
        abort(503, "Gallery not yet generated — run the scraper first.")
    return send_file(HTML_PATH)


@app.route("/api/wishlist", methods=["GET"])
def get_wishlist():
    rows = get_db().execute("SELECT artwork_id FROM wishlist").fetchall()
    return jsonify([r[0] for r in rows])


@app.route("/api/wishlist", methods=["POST"])
def add_wishlist():
    body = request.get_json(silent=True) or {}
    aid  = body.get("artwork_id", "").strip()
    if not aid:
        abort(400, "artwork_id required")
    db = get_db()
    db.execute("INSERT OR IGNORE INTO wishlist VALUES (?)", (aid,))
    db.commit()
    return jsonify({"artwork_id": aid}), 201


@app.route("/api/wishlist/<artwork_id>", methods=["DELETE"])
def remove_wishlist(artwork_id):
    db = get_db()
    db.execute("DELETE FROM wishlist WHERE artwork_id = ?", (artwork_id,))
    db.commit()
    return "", 204


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
