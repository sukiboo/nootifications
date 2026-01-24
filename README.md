# nootifications 🔎🐙🦙

Price monitoring bot that sends Telegram alerts when the asset's price moves beyond a set threshold.

## Features

- Real-time crypto price monitoring via Kraken WebSocket v2
- US stock price monitoring via Alpaca WebSocket (IEX feed)
- Configurable delta thresholds (percentage or absolute dollar amount)
- Persistent price state for crash recovery
- Extensible client architecture for future asset types (stocks, etc.)

## Configuration

#### Set assets to monitor in `settings.yaml`

```yaml
bot_name: nootifications-bot

clients:
  kraken:
    smoothing: 0.0              # EMA smoothing (0=off, 0.9=heavy)
    throttle_seconds: 10.0      # min seconds between updates per ticker
    reconnect_delay: 5          # seconds between reconnection attempts
    max_reconnect_attempts: 10  # give up after N failures
  alpaca:                      # US stocks; use your Alpaca account
    feed: iex                  # iex (free) or sip
    smoothing: 0.0
    throttle_seconds: 1.0
    reconnect_delay: 5
    max_reconnect_attempts: 10

assets:
  # monitor crypto (no API key is necessary!)
  - name: ETH         # display name for notifications
    client: kraken    # which client to use for price data
    ticker: ETH/USD   # ticker symbol in client's format
    delta: 0.01       # alert when price changes 1%

  - name: BTC
    client: kraken
    ticker: BTC/USD
    delta: 1000       # alert when price crosses $1k intervals (85k, 86k, etc.)

  # monitor US stocks (requires free ALPACA_API_KEY and ALPACA_API_SECRET in `.env`)
  - name: Apple
    client: alpaca
    ticker: AAPL
    delta: 0.02
```

**Delta (threshold)** -- set per asset in `assets`:

- **`delta < 1`** → percentage: `0.05` = alert when price moves 5% from reference.
- **`delta >= 1`** → dollar intervals: `1000` = alert when price crosses each $1k ($85k, $86k, ...).

#### Supported clients

| Client   | Use for   | Ticker format   | API keys needed?      |
| -------- | --------- | --------------- | --------------------- |
| `kraken` | Crypto    | `BTC/USD`, etc. | No (spot WebSocket)   |
| `alpaca` | US stocks | `AAPL`, `MSFT`  | Yes (free, see below) |

#### Required secrets in **`.env`**

Set your Telegram bot credentials for the alerts:
```
TELEGRAM_BOT_TOKEN=...
TELEGRAM_USER_ID=...
```

Kraken crypto streaming does not require API keys.

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
REPO_URL=git@github.com:you/nootifications.git
IMAGE_NAME=nootifications
```

Run:
```bash
./deploy.sh
```

This will:
1. Pull/clone the repo on the server
2. Copy `.env` to the server
3. Build and run the Docker container with auto-restart
