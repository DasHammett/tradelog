# Tradelog

A self-hosted trading journal for IBKR traders, running on Raspberry Pi 3B+.

## Features

- **Dashboard** — equity curve, daily P&L, win rate, profit factor, expectancy, drawdown
- **Trades** — filterable trade list with full detail view
- **Charts** — 1-minute TradingView candlestick chart with entry/exit markers per trade
- **Journal** — daily markdown journal with mood tracking and day stats
- **Import** — IBKR Flex Query auto-sync (daily) + manual CSV activity statement upload

## Stack

- Python / Flask (Jinja2 templates, no separate frontend build)
- SQLite via SQLAlchemy
- Tailwind CSS + Chart.js + TradingView Lightweight Charts (all via CDN)
- Nginx reverse proxy (shares server with Pi-hole)
- Gunicorn via Unix socket
- systemd service

---

## RPi Installation

### 1. Install dependencies

```bash
sudo apt update
sudo apt install -y nginx python3-venv python3-pip git
```

### 2. Clone the repo

```bash
sudo mkdir -p /opt/tradelog
sudo chown pi:pi /opt/tradelog
git clone https://github.com/DasHammett/tradelog.git /opt/tradelog
cd /opt/tradelog
```

### 3. Python virtual environment

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 4. Environment config

```bash
cp .env.example .env
nano .env   # set SECRET_KEY, and optionally FLEX_TOKEN + FLEX_QUERY_ID
```

### 5. Database init

```bash
source venv/bin/activate
flask --app wsgi db init
flask --app wsgi db migrate -m "initial"
flask --app wsgi db upgrade
```

### 6. Nginx config

```bash
sudo cp deploy/nginx.conf /etc/nginx/sites-available/tradelog
sudo ln -s /etc/nginx/sites-available/tradelog /etc/nginx/sites-enabled/tradelog
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

### 7. systemd service

```bash
sudo cp deploy/tradelog.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable tradelog
sudo systemctl start tradelog
```

### 8. Check it's running

```bash
sudo systemctl status tradelog
# Then visit http://<rpi-ip>/tradelog/
```

---

## Updating

```bash
cd /opt/tradelog
git pull origin main
source venv/bin/activate
pip install -r requirements.txt   # if dependencies changed
flask --app wsgi db upgrade        # if models changed
sudo systemctl restart tradelog
```

---

## IBKR Flex Query Setup

1. Log in to IBKR Account Management → Reports → Flex Queries
2. Create a new Activity Flex Query — include **Trades** section, XML format, Last 7 Days
3. Note the **Query ID**
4. Go to Flex Web Service → Generate Token — note the **Token**
5. Add to `.env`:
   ```
   FLEX_TOKEN=your_token
   FLEX_QUERY_ID=your_query_id
   ```
6. Restart the service: `sudo systemctl restart tradelog`

Auto-sync runs daily at 18:00 (configurable via `FLEX_SYNC_HOUR` in `.env`).

---

## URL Layout

```
http://<rpi-ip>/           → redirects to /tradelog/
http://<rpi-ip>/tradelog/  → Dashboard
http://<rpi-ip>/trades     → Trades list
http://<rpi-ip>/journal    → Journal
http://<rpi-ip>/import     → Import trades
http://<rpi-ip>/admin      → Pi-hole (proxied to :8090)
```
