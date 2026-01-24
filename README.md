# nootifications

Price monitoring bot that sends Telegram alerts when assets move beyond a configured threshold.

## Features

- Real-time crypto price monitoring via Kraken WebSocket v2
- Configurable delta thresholds (percentage or absolute dollar amount)
- Persistent price state for crash recovery
- Extensible client architecture for future asset types (stocks, etc.)

## Configuration

**`settings.yaml`** — Define assets to monitor:

```yaml
bot_name: nootifications-bot

clients:
  kraken:
    throttle_seconds: 10.0      # min seconds between updates per ticker
    reconnect_delay: 5          # seconds between reconnection attempts
    max_reconnect_attempts: 10  # give up after N failures

assets:
  - name: ETH         # display name for notifications
    client: kraken    # which client to use for price data
    ticker: ETH/USD   # ticker symbol in client's format
    delta: 0.01       # alert when price changes 1%

  - name: BTC
    client: kraken
    ticker: BTC/USD
    delta: 1000       # alert when price crosses $1k intervals (85k, 86k, etc.)
```

Delta interpretation:
- `< 1` → percentage (e.g. `0.05 = 5%`) -- alerts when price changes by `X%` from reference
- `> 1` → interval mode -- alerts when price crosses a multiple of the delta (e.g., `1000` alerts at `$85k`, `$86k`, `$87k` boundaries)

**`.env`** — Required secrets:

```
TELEGRAM_BOT_TOKEN=YOUR_BOT_TOKEN_HERE
TELEGRAM_USER_ID=YOUR_USER_ID_HERE
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
