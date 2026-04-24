# nootifications-bot 🔎🐙🦙

Price monitoring bot that sends Telegram alerts when asset prices move beyond set thresholds.

## Features

- Real-time crypto price monitoring via [Kraken WebSocket v2](https://support.kraken.com/articles/360022326871-kraken-websocket-api-frequently-asked-questions)
- US stock price monitoring via [Alpaca WebSocket](https://docs.alpaca.markets/docs/streaming-market-data) (IEX feed)
- Three alert types: percentage change, interval crossing, and one-time price targets
- Persistent price state for crash recovery (not really)

<img width="848" alt="image" src="https://github.com/user-attachments/assets/1e527234-7e0b-4e27-b30d-a604783d8d4c" />


## Configuration

#### Set assets to monitor in `settings.yaml`

Copy the example files and customize them:
```bash
cp .env.example .env
cp settings.example.yaml settings.yaml
```

Both files are gitignored and deployed separately, so you can update credentials and thresholds without pushing to git.

```yaml
bot_name: nootifications-bot

clients:
  # crypto monitoring; no account is required
  kraken:
    smoothing: 0.0              # EMA smoothing (0=off, 0.9=heavy)
    throttle_seconds: 10.0      # min seconds between updates per ticker
    reconnect_delay: 5          # seconds between reconnection attempts
    max_reconnect_attempts: 10  # give up after N failures
    stale_timeout_seconds: 600  # force reconnect if no updates for N seconds

  # US stocks monitoring; free Alpaca account is required
  alpaca:
    feed: iex                   # iex (free) or sip
    smoothing: 0.0
    throttle_seconds: 1.0
    reconnect_delay: 5
    max_reconnect_attempts: 10
    stale_timeout_seconds: 600  # only armed during regular market hours

assets:
  # monitor crypto (no API key is necessary!)
  - name: ETH         # display name for notifications
    client: kraken    # which client to use for price data
    ticker: ETH/USD   # ticker symbol in client's format
    percent: 0.01     # alert when price changes 1%

  - name: BTC
    client: kraken
    ticker: BTC/USD
    interval: 1000    # alert when price crosses $1k intervals (85k, 86k, etc.)

  # monitor US stocks (requires free ALPACA_API_KEY and ALPACA_API_SECRET in `.env`)
  - name: Apple
    client: alpaca
    ticker: AAPL
    target: 250       # alert once when price reaches $250
```

#### Alert types

Each asset must have at least one alert type. You can combine multiple types on a single asset.

| Parameter  | Description | Example |
| ---------- | ----------- | ------- |
| `percent`  | Alert when price changes by X% from reference | `0.05` = 5% change |
| `interval` | Alert when price crosses interval boundaries | `1000` = $85k, $86k, ... |
| `target`   | One-time alert when price reaches target | `250` = alert at $250 |

**Target alerts** fire once, then the bot adds `fired: true` to `settings.yaml`. To reset, remove that line, or set `fired: false`, or upload a new settings file.

#### Supported clients

| Client   | Use for   | Ticker format   | API keys needed?      |
| -------- | --------- | --------------- | --------------------- |
| `kraken` | Crypto    | `BTC/USD`, etc. | No (spot WebSocket)   |
| `alpaca` | US stocks | `AAPL`, `MSFT`  | Yes (free, see below) |

**Alpaca feed hours.** The free `iex` feed only streams trades that print on the IEX exchange (~2% of US volume) and is live roughly 8:00–17:00 ET (pre-market, regular, and early post-market). Overnight and the 17:00–20:00 post-market window are not covered. The paid `sip` feed ($99/mo) streams all US exchanges from ~4:00–20:00 ET. Regardless of feed, the stale-connection watchdog is only armed during regular hours (9:30–16:00 ET) — so a silent WS drop during pre/post won't auto-reconnect until the market opens.

#### Required secrets in **`.env`**

Set your Telegram bot credentials for the alerts:
```
TELEGRAM_BOT_TOKEN=...
TELEGRAM_USER_ID=...
```

**Kraken** crypto streaming does not require API keys.

If you use **Alpaca** (US stocks), create a free API key and add it to `.env`:

```
ALPACA_API_KEY=...
ALPACA_API_SECRET=...
```

## Run Locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

## Deploy

The `deploy.sh` script handles deployment to a remote server via SSH + Docker.

Required `.env` variables for deployment:
```
SERVER_USER=YOUR_USERNAME_HERE
SERVER_HOST=YOUR_HOSTNAME_HERE
SERVER_PATH=YOUR_APP_PATH_HERE
REPO_URL=git@github.com:sukiboo/nootifications-bot.git
IMAGE_NAME=nootifications-bot
```

Run:
```bash
./deploy.sh
```

This will:
1. Pull/clone the repo on the server
2. Copy `.env` and `settings.yaml` to the server
3. Build and run the Docker container with auto-restart
