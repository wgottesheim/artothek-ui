#!/usr/bin/env bash
# Run as root on a fresh Debian 12 LXC container.
set -euo pipefail

PROJECT_DIR=/opt/artothek-ui

apt-get update -qq
apt-get install -y --no-install-recommends python3 python3-venv python3-pip git

id artothek &>/dev/null || useradd -r -s /usr/sbin/nologin -d $PROJECT_DIR artothek

# Assumes repo already cloned/copied to $PROJECT_DIR
chown -R artothek:artothek $PROJECT_DIR

mkdir -p $PROJECT_DIR/data /var/log/artothek
chown artothek:artothek $PROJECT_DIR/data /var/log/artothek

# Scraper venv
[ -d $PROJECT_DIR/scraper/.venv ] || runuser -u artothek -- python3 -m venv $PROJECT_DIR/scraper/.venv
runuser -u artothek -- $PROJECT_DIR/scraper/.venv/bin/pip install -q -r $PROJECT_DIR/scraper/requirements.txt

# App venv
[ -d $PROJECT_DIR/app/.venv ] || runuser -u artothek -- python3 -m venv $PROJECT_DIR/app/.venv
runuser -u artothek -- $PROJECT_DIR/app/.venv/bin/pip install -q -r $PROJECT_DIR/app/requirements.txt

# First scrape
echo "Running first scrape…"
runuser -u artothek -- bash -c "cd $PROJECT_DIR/scraper && .venv/bin/python scraper.py"

# Systemd
cp $PROJECT_DIR/deploy/artothek-web.service     /etc/systemd/system/
cp $PROJECT_DIR/deploy/artothek-scraper.service /etc/systemd/system/
cp $PROJECT_DIR/deploy/artothek-scraper.timer   /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now artothek-web.service
systemctl enable --now artothek-scraper.timer

echo "Done. App running on :5000"
