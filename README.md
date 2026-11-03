# Kite Desk

A local React + Vite dashboard with a FastAPI backend for Kite Connect login and profile details.

## What it does

- Accepts a Kite API key, API secret, and fresh request token on the login page.
- Exchanges those values with the Kite Connect Python SDK on the backend.
- Saves the API key and access token in `backend/data/kite_session.json` on this computer. The session file is ignored by Git.
- Never returns the access token to the browser.
- Reuses the saved session when the page reloads during local development. Kite access tokens expire, so a new login may be needed when Kite invalidates the session.
- Shows the connected user's name, ID, enabled products, and exchanges on the User tab.
- Scans the current official Nifty 100 constituents for the latest bullish or bearish crossover on the Signals tab.
- Provides an Algo Signals tab for high-volume breakout and midpoint-retest setups, with live prices for returned results.

## Requirements

- Python 3.10 or newer
- Node.js 18 or newer and npm
- A Kite Connect app with its API key and API secret

## Run on Windows

Open two PowerShell windows in the `kite-dashboard` folder.

### 1. Start the backend

```powershell
cd backend
py -m venv .venv
.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
py -m uvicorn main:app --reload --host 127.0.0.1 --port 8001
```

If PowerShell blocks activation, use the Python executable directly instead:

```powershell
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m uvicorn main:app --reload --host 127.0.0.1 --port 8001
```

### 2. Start the frontend

In a second PowerShell window:

```powershell
cd frontend
npm install
npm run dev
```

Open the local address printed by Vite (normally `http://127.0.0.1:5173`).

## Sign in

1. Enter the API key and API secret from your Kite Connect developer app.
2. Open the Kite Connect login flow using `https://kite.zerodha.com/connect/login?v=3&api_key=YOUR_API_KEY`, complete sign-in, and paste the fresh `request_token` from the redirect into the form.
3. Select **Connect account**. The backend completes the session exchange and loads the User tab.

The API key, secret, and request token are only held by the page while signing in and are sent to the local backend for the Kite exchange. The access token is stored only in `backend/data/kite_session.json`; it is not included in API responses or browser storage. Use this app only on your own computer. The backend listens on `127.0.0.1` and is not configured for public hosting.

Select **Disconnect account** in the dashboard to remove the saved local session.

## Signals tab

The Signals tab downloads the current Nifty 100 constituent CSV from NSE Indices, matches each ticker to Kite's NSE instruments, and requests daily candles from Kite. It calculates the chosen short and long SMAs, then ranks each stock's most recent bullish or bearish crossover in the selected calendar-day lookback. The latest completed daily candle is used so a still-forming market-day candle does not create a premature signal. **Max Stocks** selects how many constituents to scan, up to 100. Results are not investment advice.

## Algo Signals tab

The Algo Signals tab loads `/api/scanner-data`, which caches Nifty 100 instrument details and daily candles for five minutes. The browser normalizes candles and scans with a 20-candle volume and price lookback, a 3× volume threshold, a maximum age of 20 candles, and a 1% midpoint tolerance. Matching rows keep their scan ranking while live Kite ticks update prices; the backend provides an OHLC last-price snapshot as a fallback. A fresh scan can be started from **Run scanner again**.

## Customize the interface

The page layout and tab components are in `frontend/src/App.jsx`. Colors, spacing, and responsive styles are in `frontend/src/styles.css`. The local API routes are in `backend/main.py`.
