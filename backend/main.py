"""Local FastAPI service for the Kite Connect dashboard."""

import json
import asyncio
import os
import time
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from threading import Lock, RLock
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi import WebSocket, WebSocketDisconnect
from kiteconnect import KiteConnect, KiteTicker
import pandas as pd
import requests
from pydantic import BaseModel, Field


APP_FOLDER = Path(__file__).resolve().parent
SESSION_FILE = APP_FOLDER / "data" / "kite_session.json"
NIFTY_100_CSV_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty100list.csv"
NIFTY_50_CSV_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv"
SCANNER_CACHE_SECONDS = 300
SCANNER_HISTORY_DAYS = 180
SCANNER_DATA_CACHE: dict[str, Any] | None = None
SCANNER_DATA_CACHE_TIME = 0.0
SCANNER_DATA_CACHE_LOCK = Lock()
DUMMY_GTT_FILE = APP_FOLDER / "data" / "dummy_gtts.json"
DUMMY_GTT_LOCK_FILE = APP_FOLDER / "data" / "dummy_gtts.lock"
DUMMY_GTT_THREAD_LOCK = RLock()
INSTRUMENT_MASTER_CACHE: dict[str, dict[str, Any]] | None = None
INSTRUMENT_MASTER_CACHE_TIME = 0.0
INSTRUMENT_MASTER_CACHE_LOCK = Lock()

app = FastAPI(title="Kite Connect Local API", version="1.0.0")

# The Vite development server runs at this address. The API is intended for
# local development and should not be exposed to the public internet.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
)


class LoginDetails(BaseModel):
    api_key: str = Field(min_length=1, max_length=200)
    api_secret: str = Field(min_length=1, max_length=200)
    request_token: str = Field(min_length=1, max_length=500)


class SignalRequest(BaseModel):
    short_sma: int = Field(default=6, ge=1, le=250)
    long_sma: int = Field(default=30, ge=2, le=500)
    lookback_days: int = Field(default=365, ge=1, le=3650)
    max_stocks: int = Field(default=100, ge=1, le=100)


class GttCandidate(BaseModel):
    symbol: str = Field(min_length=1, max_length=40)
    quantity: int = Field(default=1)
    buy_price: Decimal | None = None
    stoploss: Decimal | None = None
    last_price: Decimal | None = None


class GttStateRequest(BaseModel):
    orders: list[GttCandidate] = Field(default_factory=list)


class LiveGttRequest(GttStateRequest):
    confirm_live: bool = False


def read_saved_session() -> dict[str, str] | None:
    """Read the local session file without ever returning its token to the UI."""
    if not SESSION_FILE.exists():
        return None
    try:
        saved = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        api_key = saved.get("api_key")
        access_token = saved.get("access_token")
        if api_key and access_token:
            return {"api_key": api_key, "access_token": access_token}
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
    return None


def save_session(api_key: str, access_token: str) -> None:
    """Persist only the API key and access token on the backend."""
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = SESSION_FILE.with_suffix(".tmp")
    temporary_file.write_text(
        json.dumps({"api_key": api_key, "access_token": access_token}, indent=2),
        encoding="utf-8",
    )
    temporary_file.replace(SESSION_FILE)


def make_kite_client() -> KiteConnect:
    """Create an authenticated Kite client from the backend session file."""
    saved = read_saved_session()
    if not saved:
        raise HTTPException(status_code=401, detail="Log in to Kite to continue.")
    kite = KiteConnect(api_key=saved["api_key"])
    kite.set_access_token(saved["access_token"])
    return kite


@app.get("/api/session")
def session_status() -> dict[str, bool]:
    """Tell the frontend whether a local session file is available."""
    return {"authenticated": read_saved_session() is not None}


@app.post("/api/login")
def login(details: LoginDetails) -> dict[str, bool | str]:
    """Exchange the submitted request token and save the access token locally."""
    try:
        kite = KiteConnect(api_key=details.api_key.strip())
        session_data = kite.generate_session(
            details.request_token.strip(), api_secret=details.api_secret.strip()
        )
        access_token = session_data["access_token"]
        save_session(details.api_key.strip(), access_token)
    except Exception as error:
        # Do not return provider errors or submitted credentials to the browser.
        raise HTTPException(
            status_code=401,
            detail="Kite login failed. Check the API key, API secret, and fresh request token.",
        ) from error

    # Deliberately do not include access_token or session_data in this response.
    return {"success": True, "message": "Kite account connected."}


@app.get("/api/profile")
def profile() -> dict[str, Any]:
    """Fetch the signed-in user's profile through the backend Kite SDK."""
    kite = make_kite_client()
    try:
        user = kite.profile()
    except Exception as error:
        # Keep the saved file on transient failures. A new login replaces it if
        # Kite reports that the session is no longer usable.
        raise HTTPException(
            status_code=401,
            detail="The saved Kite session could not be used. It may have expired; please log in again.",
        ) from error

    return {
        "user_name": user.get("user_name") or user.get("user_shortname") or "—",
        "user_id": user.get("user_id") or "—",
        "products": user.get("products") or [],
        "exchanges": user.get("exchanges") or [],
    }


@app.delete("/api/session")
def logout() -> dict[str, bool]:
    """Forget the local access token on this computer."""
    try:
        SESSION_FILE.unlink(missing_ok=True)
    except OSError as error:
        raise HTTPException(status_code=500, detail="Could not clear the saved session.") from error
    return {"success": True}


def download_nifty100_constituents() -> pd.DataFrame:
    """Download the current Nifty 100 constituents from NSE Indices."""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; KiteDeskLocal/1.0)"}
    try:
        response = requests.get(NIFTY_100_CSV_URL, headers=headers, timeout=30)
        response.raise_for_status()
        stocks = pd.read_csv(BytesIO(response.content), encoding="utf-8-sig")
    except requests.RequestException as error:
        raise HTTPException(
            status_code=502,
            detail="Could not download the official Nifty 100 constituents from NSE Indices.",
        ) from error
    except (pd.errors.ParserError, pd.errors.EmptyDataError, UnicodeError) as error:
        raise HTTPException(
            status_code=502,
            detail="NSE Indices returned a CSV file that could not be read.",
        ) from error

    stocks.columns = [str(column).strip() for column in stocks.columns]
    symbol_column = next(
        (column for column in stocks.columns if column.lower() == "symbol"), None
    )
    company_column = next(
        (
            column
            for column in stocks.columns
            if column.lower() in {"company name", "company"}
        ),
        None,
    )
    if not symbol_column:
        raise HTTPException(status_code=502, detail="The Nifty 100 CSV has no Symbol column.")
    if not company_column:
        stocks["Company"] = stocks[symbol_column]
        company_column = "Company"

    stocks = stocks[[symbol_column, company_column]].copy()
    stocks.columns = ["ticker", "company"]
    stocks["ticker"] = stocks["ticker"].astype(str).str.strip().str.upper()
    stocks["company"] = stocks["company"].astype(str).str.strip()
    return stocks.dropna(subset=["ticker"]).drop_duplicates(subset=["ticker"])


@app.post("/api/signals")
def generate_signals(settings: SignalRequest) -> dict[str, Any]:
    """Find each scanned stock's latest SMA crossover within the lookback."""
    if settings.long_sma <= settings.short_sma:
        raise HTTPException(status_code=422, detail="Long SMA must be greater than Short SMA.")

    kite = make_kite_client()
    constituents = download_nifty100_constituents().head(settings.max_stocks).copy()

    try:
        instruments = pd.DataFrame(kite.instruments("NSE"))
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail="Kite could not provide the NSE instrument list. Check your saved session and try again.",
        ) from error

    if instruments.empty or not {"tradingsymbol", "instrument_token"}.issubset(instruments.columns):
        raise HTTPException(status_code=502, detail="Kite returned an invalid NSE instrument list.")

    instruments = instruments[["tradingsymbol", "instrument_token"]].copy()
    instruments["tradingsymbol"] = instruments["tradingsymbol"].astype(str).str.upper()
    instruments = instruments.drop_duplicates(subset=["tradingsymbol"])
    stocks = constituents.merge(
        instruments,
        left_on="ticker",
        right_on="tradingsymbol",
        how="left",
    )

    india_today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    # Ignore today's still-forming daily candle; signals use completed sessions.
    last_completed_day = india_today - timedelta(days=1)
    first_signal_day = india_today - timedelta(days=settings.lookback_days)
    history_start = first_signal_day - timedelta(days=settings.long_sma * 3 + 14)
    results: list[dict[str, Any]] = []
    warnings: list[str] = []
    scanned = 0

    for stock in stocks.itertuples(index=False):
        if pd.isna(stock.instrument_token):
            warnings.append(f"No Kite NSE instrument token found for {stock.ticker}.")
            continue

        scanned += 1
        try:
            candles = kite.historical_data(
                instrument_token=int(stock.instrument_token),
                from_date=history_start,
                to_date=india_today,
                interval="day",
            )
        except Exception:
            warnings.append(f"Kite daily history was unavailable for {stock.ticker}.")
            time.sleep(0.36)
            continue

        if not candles:
            warnings.append(f"No daily candles were available for {stock.ticker}.")
            time.sleep(0.36)
            continue

        prices = pd.DataFrame(candles)
        prices["date"] = (
            pd.to_datetime(prices["date"], utc=True)
            .dt.tz_convert("Asia/Kolkata")
            .dt.tz_localize(None)
        )
        prices = prices.sort_values("date").drop_duplicates(subset=["date"]).reset_index(drop=True)
        prices = prices[prices["date"].dt.date <= last_completed_day].copy()
        prices["short_average"] = prices["close"].rolling(settings.short_sma).mean()
        prices["long_average"] = prices["close"].rolling(settings.long_sma).mean()

        difference = prices["short_average"] - prices["long_average"]
        bullish = (difference.shift(1) <= 0) & (difference > 0)
        bearish = (difference.shift(1) >= 0) & (difference < 0)
        prices["crossover_type"] = ""
        prices.loc[bullish, "crossover_type"] = "Bullish"
        prices.loc[bearish, "crossover_type"] = "Bearish"

        in_lookback = prices["date"].dt.date >= first_signal_day
        signals = prices[(prices["crossover_type"] != "") & in_lookback].dropna(
            subset=["short_average", "long_average"]
        )
        if not signals.empty:
            latest = signals.iloc[-1]
            results.append(
                {
                    "ticker": stock.ticker,
                    "company": stock.company,
                    "crossover_type": latest["crossover_type"],
                    "crossover_date": latest["date"].strftime("%Y-%m-%d"),
                    "close": round(float(latest["close"]), 2),
                    "short_sma": round(float(latest["short_average"]), 2),
                    "long_sma": round(float(latest["long_average"]), 2),
                }
            )

        # Stay within Kite's typical request rate while scanning the list.
        time.sleep(0.36)

    results.sort(key=lambda row: row["crossover_date"], reverse=True)
    for rank, signal in enumerate(results, start=1):
        signal["rank"] = rank

    return {
        "signals": results,
        "scanned": scanned,
        "constituents_selected": len(constituents),
        "lookback_days": settings.lookback_days,
        "short_sma": settings.short_sma,
        "long_sma": settings.long_sma,
        "warnings": warnings,
    }


def download_symbol_list(url: str) -> list[str]:
    """Read an official NSE Indices CSV and return its symbol column."""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; KiteDeskLocal/1.0)"}
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    frame = pd.read_csv(BytesIO(response.content), encoding="utf-8-sig")
    symbol_column = next(
        (column for column in frame.columns if str(column).strip().lower() == "symbol"),
        None,
    )
    if not symbol_column:
        raise ValueError("NSE Indices CSV has no Symbol column.")
    return frame[symbol_column].dropna().astype(str).str.strip().str.upper().tolist()


def build_scanner_data() -> dict[str, Any]:
    """Build a short daily-candle cache for the current Nifty 100 stocks."""
    kite = make_kite_client()
    constituents = download_nifty100_constituents()
    try:
        nifty50_symbols = set(download_symbol_list(NIFTY_50_CSV_URL))
    except (requests.RequestException, ValueError, pd.errors.ParserError, pd.errors.EmptyDataError):
        nifty50_symbols = set()

    try:
        instruments = pd.DataFrame(kite.instruments("NSE"))
    except Exception as error:
        raise HTTPException(status_code=502, detail="Could not load Kite NSE instruments.") from error
    if instruments.empty or not {"tradingsymbol", "instrument_token"}.issubset(instruments.columns):
        raise HTTPException(status_code=502, detail="Kite returned an invalid NSE instrument list.")

    instruments = instruments[["tradingsymbol", "instrument_token"]].copy()
    instruments["tradingsymbol"] = instruments["tradingsymbol"].astype(str).str.upper()
    instruments = instruments.drop_duplicates(subset=["tradingsymbol"])
    stocks = constituents.merge(
        instruments,
        left_on="ticker",
        right_on="tradingsymbol",
        how="left",
    )

    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    start_date = today - timedelta(days=SCANNER_HISTORY_DAYS)
    stock_rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    for stock in stocks.itertuples(index=False):
        candles: list[dict[str, Any]] = []
        token = None if pd.isna(stock.instrument_token) else int(stock.instrument_token)
        if token is not None:
            try:
                raw_candles = kite.historical_data(
                    instrument_token=token,
                    from_date=start_date,
                    to_date=today,
                    interval="day",
                )
                candles = [
                    {
                        "date": pd.Timestamp(row["date"]).strftime("%Y-%m-%d"),
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "volume": float(row["volume"]),
                    }
                    for row in raw_candles
                ]
            except Exception:
                warnings.append(f"Daily candles were unavailable for {stock.ticker}.")
        else:
            warnings.append(f"No NSE instrument token was found for {stock.ticker}.")

        stock_rows.append(
            {
                "symbol": stock.ticker,
                "companyName": stock.company,
                "instrument_token": token,
                "isNifty50": stock.ticker in nifty50_symbols,
                "isNifty100": True,
                "candles": candles,
            }
        )
        if token is not None:
            time.sleep(0.36)

    # Quote all symbols together for a lightweight fallback while the tick
    # websocket connects. Quote failure does not prevent historical scanning.
    quote_keys = [f"NSE:{row['symbol']}" for row in stock_rows if row["instrument_token"]]
    quote_snapshot: dict[str, Any] = {}
    if quote_keys:
        try:
            quote_snapshot = kite.ohlc(*quote_keys)
        except Exception:
            warnings.append("The latest OHLC price snapshot is unavailable.")
    for row in stock_rows:
        quote = quote_snapshot.get(f"NSE:{row['symbol']}", {})
        row["ohlc"] = quote if isinstance(quote, dict) else {}

    return {
        "metadata": {
            "source": "NSE Indices Nifty 100 constituent list and Kite Connect daily candles",
            "loadedAt": datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(timespec="seconds"),
            "stockCount": len(stock_rows),
            "historyDays": SCANNER_HISTORY_DAYS,
            "warnings": warnings,
        },
        "stocks": stock_rows,
    }


@app.get("/api/scanner-data")
def scanner_data(refresh: bool = Query(default=False)) -> dict[str, Any]:
    """Return the cached daily data used by the Algo Signals scanner."""
    global SCANNER_DATA_CACHE, SCANNER_DATA_CACHE_TIME
    with SCANNER_DATA_CACHE_LOCK:
        now = time.monotonic()
        if (
            not refresh
            and SCANNER_DATA_CACHE is not None
            and now - SCANNER_DATA_CACHE_TIME < SCANNER_CACHE_SECONDS
        ):
            return SCANNER_DATA_CACHE
        SCANNER_DATA_CACHE = build_scanner_data()
        SCANNER_DATA_CACHE_TIME = time.monotonic()
        return SCANNER_DATA_CACHE


@app.websocket("/ws/ticks")
async def live_ticks(websocket: WebSocket, tokens: str = "", mode: str = "full") -> None:
    """Proxy Kite full-mode ticks for only the requested instrument tokens."""
    saved = read_saved_session()
    if not saved:
        await websocket.close(code=4401, reason="Log in to Kite to receive live ticks.")
        return

    try:
        instrument_tokens = list(dict.fromkeys(int(value) for value in tokens.split(",") if value.strip()))
    except ValueError:
        await websocket.close(code=4400, reason="Instrument tokens must be comma-separated integers.")
        return
    if not instrument_tokens or len(instrument_tokens) > 100:
        await websocket.close(code=4400, reason="Provide between 1 and 100 instrument tokens.")
        return

    await websocket.accept()
    event_loop = asyncio.get_running_loop()
    messages: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    ticker = KiteTicker(saved["api_key"], saved["access_token"])

    def queue_message(message: dict[str, Any]) -> None:
        event_loop.call_soon_threadsafe(messages.put_nowait, message)

    def on_connect(client: KiteTicker, response: dict[str, Any]) -> None:
        client.subscribe(instrument_tokens)
        selected_mode = KiteTicker.MODE_FULL if mode == "full" else KiteTicker.MODE_QUOTE
        client.set_mode(selected_mode, instrument_tokens)
        queue_message({"type": "status", "status": "connected"})

    def on_ticks(client: KiteTicker, ticks: list[dict[str, Any]]) -> None:
        live = [
            {"instrument_token": tick.get("instrument_token"), "last_price": tick.get("last_price")}
            for tick in ticks
            if tick.get("instrument_token") in instrument_tokens and tick.get("last_price") is not None
        ]
        if live:
            queue_message({"type": "ticks", "ticks": live})

    def on_error(client: KiteTicker, code: int, reason: str) -> None:
        queue_message({"type": "status", "status": "error", "message": "Kite live ticks are temporarily unavailable."})

    def on_close(client: KiteTicker, code: int, reason: str) -> None:
        queue_message({"type": "status", "status": "disconnected"})

    ticker.on_connect = on_connect
    ticker.on_ticks = on_ticks
    ticker.on_error = on_error
    ticker.on_close = on_close
    await websocket.send_json({"type": "status", "status": "connecting"})

    try:
        ticker.connect(threaded=True)
        while True:
            message_task = asyncio.create_task(messages.get())
            receive_task = asyncio.create_task(websocket.receive())
            done, pending = await asyncio.wait(
                {message_task, receive_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()

            if receive_task in done:
                event = receive_task.result()
                if event.get("type") == "websocket.disconnect":
                    break
            if message_task in done:
                await websocket.send_json(message_task.result())
    except WebSocketDisconnect:
        pass
    except Exception:
        try:
            await websocket.send_json({"type": "status", "status": "error", "message": "Kite live ticks could not be started."})
        except Exception:
            pass
    finally:
        try:
            ticker.close()
        except Exception:
            pass


@contextmanager
def dummy_gtt_store_lock():
    """Lock the local dummy store across threads and processes."""
    DUMMY_GTT_THREAD_LOCK.acquire()
    lock_handle = None
    lock_acquired = False
    try:
        DUMMY_GTT_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        lock_handle = DUMMY_GTT_LOCK_FILE.open("a+b")
        lock_handle.seek(0, os.SEEK_END)
        if lock_handle.tell() == 0:
            lock_handle.write(b"0")
            lock_handle.flush()
        lock_handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock_handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        lock_acquired = True
        yield
    finally:
        if lock_handle is not None:
            if lock_acquired:
                if os.name == "nt":
                    import msvcrt

                    lock_handle.seek(0)
                    msvcrt.locking(lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            lock_handle.close()
        DUMMY_GTT_THREAD_LOCK.release()


def read_dummy_gtt_store() -> dict[str, Any]:
    """Read locally simulated GTT records; never read or alter broker orders."""
    if not DUMMY_GTT_FILE.exists():
        return {"sequence": 0, "records": []}
    try:
        store = json.loads(DUMMY_GTT_FILE.read_text(encoding="utf-8"))
        if not isinstance(store, dict) or not isinstance(store.get("records"), list):
            raise ValueError("Unexpected dummy GTT store structure.")
        store["sequence"] = int(store.get("sequence", 0))
        return store
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise RuntimeError("The local dummy GTT store could not be read safely.") from error


def write_dummy_gtt_store(store: dict[str, Any]) -> None:
    """Atomically persist simulated records to the backend-local data folder."""
    DUMMY_GTT_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = DUMMY_GTT_FILE.with_name(f"{DUMMY_GTT_FILE.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(store, indent=2), encoding="utf-8")
    os.replace(temporary, DUMMY_GTT_FILE)


def decimal_value(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return result if result.is_finite() else None


def round_price_to_tick(price: Decimal, tick_size: Decimal) -> Decimal:
    if tick_size <= 0:
        raise ValueError("Tick size must be greater than zero.")
    ticks = (price / tick_size).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return ticks * tick_size


def response_number(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def get_nse_instrument_master(kite: KiteConnect) -> dict[str, dict[str, Any]]:
    """Return a short-lived symbol-to-tick-size cache from Kite's read-only master."""
    global INSTRUMENT_MASTER_CACHE, INSTRUMENT_MASTER_CACHE_TIME
    with INSTRUMENT_MASTER_CACHE_LOCK:
        now = time.monotonic()
        if (
            INSTRUMENT_MASTER_CACHE is not None
            and now - INSTRUMENT_MASTER_CACHE_TIME < SCANNER_CACHE_SECONDS
        ):
            return INSTRUMENT_MASTER_CACHE
        instruments = pd.DataFrame(kite.instruments("NSE"))
        if instruments.empty or not {"tradingsymbol", "instrument_token", "tick_size"}.issubset(instruments.columns):
            raise RuntimeError("Kite did not return NSE instrument tick sizes.")
        master: dict[str, dict[str, Any]] = {}
        for instrument in instruments.to_dict(orient="records"):
            symbol = str(instrument.get("tradingsymbol", "")).strip().upper()
            if symbol:
                master[symbol] = instrument
        INSTRUMENT_MASTER_CACHE = master
        INSTRUMENT_MASTER_CACHE_TIME = time.monotonic()
        return master


def read_kite_gtt_state(kite: KiteConnect) -> dict[str, Any]:
    """Fetch only read-only Kite account state for duplicate checks."""
    gtts = kite.get_gtts() or []
    orders = kite.orders() or []
    positions = kite.positions() or {}
    holdings = kite.holdings() or []
    if isinstance(positions, list):
        positions = {"net": positions}
    return {
        "gtts": gtts,
        "orders": orders,
        "positions": positions.get("net", []) if isinstance(positions, dict) else [],
        "holdings": holdings,
    }


def active_gtt_items(state: dict[str, Any], symbol: str) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for gtt in state.get("gtts", []):
        condition = gtt.get("condition") or {}
        gtt_symbol = str(
            condition.get("tradingsymbol") or gtt.get("tradingsymbol") or ""
        ).strip().upper()
        status = str(gtt.get("status", "")).strip().lower().replace("_", " ")
        if gtt_symbol == symbol and status in {"active", "trigger pending", "pending"}:
            matches.append(gtt)
    return matches


def gtt_orders(gtt: dict[str, Any]) -> list[dict[str, Any]]:
    orders = gtt.get("orders") or []
    if isinstance(orders, dict):
        return [orders]
    return [order for order in orders if isinstance(order, dict)] if isinstance(orders, list) else []


def gtt_trigger_values(gtt: dict[str, Any]) -> list[Decimal]:
    values = (gtt.get("condition") or {}).get("trigger_values") or []
    if not isinstance(values, list):
        values = [values]
    return [number for value in values if (number := decimal_value(value)) is not None]


def same_tick_price(left: Decimal, right: Decimal, tick_size: Decimal) -> bool:
    return round_price_to_tick(left, tick_size) == round_price_to_tick(right, tick_size)


def current_day_order(orders: list[dict[str, Any]], symbol: str) -> dict[str, Any] | None:
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    for order in orders:
        if str(order.get("tradingsymbol", "")).strip().upper() != symbol:
            continue
        if str(order.get("exchange", "")).strip().upper() != "NSE":
            continue
        if str(order.get("transaction_type", "")).strip().upper() != "BUY":
            continue
        if str(order.get("product", "")).strip().upper() not in {"CNC", "DELIVERY"}:
            continue
        if str(order.get("status", "")).strip().upper() in {"CANCELLED", "CANCELED", "REJECTED"}:
            continue
        timestamp = order.get("order_timestamp") or order.get("exchange_timestamp")
        if not timestamp:
            continue
        parsed = pd.to_datetime(timestamp, errors="coerce")
        if pd.isna(parsed):
            continue
        if parsed.tzinfo is None:
            parsed = parsed.tz_localize("Asia/Kolkata")
        else:
            parsed = parsed.tz_convert("Asia/Kolkata")
        if parsed.date() == today:
            return order
    return None


def matching_quantity(rows: Any, symbol: str, *, include_t1: bool = False) -> int:
    if not isinstance(rows, list):
        return 0
    total = 0
    for row in rows:
        if not isinstance(row, dict) or str(row.get("tradingsymbol", "")).strip().upper() != symbol:
            continue
        quantity = decimal_value(row.get("quantity", row.get("net_quantity", 0))) or Decimal(0)
        if include_t1:
            quantity += decimal_value(row.get("t1_quantity", 0)) or Decimal(0)
        total += int(quantity)
    return total


def active_dummy_record(records: list[dict[str, Any]], symbol: str) -> dict[str, Any] | None:
    for record in records:
        if (
            str(record.get("symbol", "")).upper() == symbol
            and str(record.get("transaction_type", "BUY")).upper() == "BUY"
            and str(record.get("status", "")).lower() == "active"
        ):
            return record
    return None


def active_real_buy_gtt(
    state: dict[str, Any], symbol: str, buy_price: Decimal, tick_size: Decimal
) -> dict[str, Any] | None:
    for gtt in active_gtt_items(state, symbol):
        entries = gtt_orders(gtt)
        has_buy = any(str(order.get("transaction_type", "")).upper() == "BUY" for order in entries)
        same_trigger = any(
            same_tick_price(value, buy_price, tick_size)
            for value in gtt_trigger_values(gtt)
        )
        if has_buy or same_trigger:
            return gtt
    return None


def active_stoploss_gtt_id(state: dict[str, Any], symbol: str) -> str | None:
    for gtt in active_gtt_items(state, symbol):
        if any(str(order.get("transaction_type", "")).upper() == "SELL" for order in gtt_orders(gtt)):
            trigger_id = gtt.get("id") or gtt.get("trigger_id")
            return str(trigger_id) if trigger_id is not None else None
    return None


def base_gtt_response(candidate: GttCandidate, tick_size: Decimal | None) -> dict[str, Any]:
    symbol = candidate.symbol.strip().upper()
    raw_buy = decimal_value(candidate.buy_price)
    buy_price = round_price_to_tick(raw_buy, tick_size) if raw_buy is not None and tick_size else None
    stoploss = (
        round_price_to_tick(buy_price * Decimal("0.95"), tick_size)
        if buy_price is not None and tick_size is not None
        else None
    )
    ltp = decimal_value(candidate.last_price)
    if ltp is not None and ltp <= 0:
        ltp = None
    return {
        "symbol": symbol,
        "quantity": candidate.quantity,
        "buy_price": response_number(buy_price),
        "stoploss": response_number(stoploss),
        "tick_size": response_number(tick_size),
        "last_price": response_number(ltp),
        "account_state": "checking",
        "position_quantity": 0,
        "holding_quantity": 0,
        "active_buy_trigger_id": None,
        "active_stoploss_trigger_id": None,
        "day_buy_order_id": None,
        "day_buy_order_status": None,
        "buy_status": "checking",
        "buy_trigger_id": None,
        "stoploss_status": "planned_after_buy" if stoploss is not None else "not_available",
        "stoploss_trigger_id": None,
        "message": "Checking Kite account state.",
    }


def check_gtt_candidate(
    candidate: GttCandidate,
    instrument_master: dict[str, dict[str, Any]],
    broker_state: dict[str, Any] | None,
    dummy_records: list[dict[str, Any]],
) -> dict[str, Any]:
    symbol = candidate.symbol.strip().upper()
    instrument = instrument_master.get(symbol)
    tick_size = decimal_value(instrument.get("tick_size")) if instrument else None
    row = base_gtt_response(candidate, tick_size)

    if broker_state is not None:
        row["active_stoploss_trigger_id"] = active_stoploss_gtt_id(broker_state, symbol)
        row["position_quantity"] = matching_quantity(broker_state.get("positions", []), symbol)
        row["holding_quantity"] = matching_quantity(
            broker_state.get("holdings", []), symbol, include_t1=True
        )
        day_order = current_day_order(broker_state.get("orders", []), symbol)
        if day_order is not None:
            row["day_buy_order_id"] = day_order.get("order_id")
            row["day_buy_order_status"] = day_order.get("status")

    if candidate.quantity != 1:
        row.update(account_state="invalid", buy_status="invalid", message="Quantity must be 1 for this simulated workflow.")
        return row
    if instrument is None or tick_size is None or tick_size <= 0:
        row.update(account_state="invalid", buy_status="not_available", message="Tick size is not available for this NSE instrument.")
        return row
    raw_buy = decimal_value(candidate.buy_price)
    if raw_buy is None or raw_buy <= 0:
        row.update(account_state="invalid", buy_status="not_available", message="Buy price is not available for this row.")
        return row

    buy_price = round_price_to_tick(raw_buy, tick_size)
    stoploss = round_price_to_tick(buy_price * Decimal("0.95"), tick_size)
    row.update(buy_price=response_number(buy_price), stoploss=response_number(stoploss))
    if stoploss <= 0 or stoploss >= buy_price:
        row.update(account_state="invalid", buy_status="invalid", message="Trigger price should be a multiple of tick size.")
        return row

    dummy = active_dummy_record(dummy_records, symbol)
    if dummy is not None:
        trigger_id = str(dummy.get("trigger_id"))
        row.update(
            account_state="already_active",
            active_buy_trigger_id=trigger_id,
            buy_status="already_active",
            buy_trigger_id=trigger_id,
            message="Skipped: dummy buy GTT already active.",
        )
        return row

    if broker_state is None:
        row.update(account_state="checking", buy_status="checking", message="Kite state could not be checked. No dummy GTT was created.")
        return row

    real_buy_gtt = active_real_buy_gtt(broker_state, symbol, buy_price, tick_size)
    if real_buy_gtt is not None:
        trigger_id = real_buy_gtt.get("id") or real_buy_gtt.get("trigger_id")
        row.update(
            account_state="already_active",
            active_buy_trigger_id=str(trigger_id) if trigger_id is not None else None,
            buy_status="already_active",
            message="Skipped: buy GTT already active.",
        )
        return row

    position_quantity = row["position_quantity"]
    holding_quantity = row["holding_quantity"]
    if position_quantity > 0 or holding_quantity > 0:
        row.update(
            account_state="position_exists",
            buy_status="blocked",
            message=f"Skipped: existing position/holding quantity is {max(position_quantity, holding_quantity)}.",
        )
        return row

    day_order = current_day_order(broker_state.get("orders", []), symbol)
    if day_order is not None:
        row.update(
            account_state="day_buy_order_exists",
            day_buy_order_id=day_order.get("order_id"),
            day_buy_order_status=day_order.get("status"),
            buy_status="blocked",
            message="Skipped: day BUY order already exists.",
        )
        return row

    if row["last_price"] is None:
        row.update(account_state="waiting_for_ltp", buy_status="waiting_for_ltp", message="Waiting for LTP.")
        return row

    row.update(account_state="clear", buy_status="ready", message="Ready for buy GTT placement.")
    return row


def read_instrument_master_or_checking(kite: KiteConnect) -> tuple[dict[str, dict[str, Any]], str | None]:
    try:
        return get_nse_instrument_master(kite), None
    except Exception:
        return {}, "Kite instrument tick sizes could not be loaded."


def rows_without_account_state(
    orders: list[GttCandidate], instrument_master: dict[str, dict[str, Any]], message: str
) -> list[dict[str, Any]]:
    rows = []
    for order in orders:
        instrument = instrument_master.get(order.symbol.strip().upper())
        tick_size = decimal_value(instrument.get("tick_size")) if instrument else None
        row = base_gtt_response(order, tick_size)
        row.update(account_state="checking", buy_status="checking", message=message)
        rows.append(row)
    return rows


@app.post("/api/algo-orders/gtt-state")
def algo_gtt_state(request: GttStateRequest) -> dict[str, Any]:
    """Read-only GTT, current-day order, position, and holding check."""
    kite = make_kite_client()
    instrument_master, instrument_error = read_instrument_master_or_checking(kite)
    if instrument_error:
        results = rows_without_account_state(request.orders, instrument_master, instrument_error)
        return {"results": results, "count": len(results)}
    try:
        broker_state = read_kite_gtt_state(kite)
    except Exception:
        results = rows_without_account_state(
            request.orders,
            instrument_master,
            "Kite state could not be checked. No dummy GTT was created.",
        )
        return {"results": results, "count": len(results)}

    try:
        with dummy_gtt_store_lock():
            store = read_dummy_gtt_store()
            results = [
                check_gtt_candidate(order, instrument_master, broker_state, store["records"])
                for order in request.orders
            ]
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return {"results": results, "count": len(results)}


@app.post("/api/algo-orders/live-gtt")
def algo_live_gtt(request: LiveGttRequest) -> dict[str, Any]:
    """Create backend-local dummy BUY GTT records only; never submit to Kite."""
    if request.confirm_live is not True:
        raise HTTPException(status_code=400, detail="confirm_live must be true to run the dummy GTT simulation.")

    kite = make_kite_client()
    instrument_master, instrument_error = read_instrument_master_or_checking(kite)
    if instrument_error:
        results = rows_without_account_state(request.orders, instrument_master, instrument_error)
        return {"results": results, "count": len(results), "simulation_only": True}
    try:
        broker_state = read_kite_gtt_state(kite)
    except Exception:
        results = rows_without_account_state(
            request.orders,
            instrument_master,
            "Kite state could not be checked. No dummy GTT was created.",
        )
        return {"results": results, "count": len(results), "simulation_only": True}

    results: list[dict[str, Any]] = []
    for candidate in request.orders:
        try:
            # Local duplicate checks and creation are serialized and persisted
            # under one cross-process lock. Kite state is read-only throughout.
            with dummy_gtt_store_lock():
                store = read_dummy_gtt_store()
                row = check_gtt_candidate(
                    candidate,
                    instrument_master,
                    broker_state,
                    store["records"],
                )
                if row["buy_status"] == "ready":
                    sequence = store["sequence"] + 1
                    trigger_id = f"DUMMY-{row['symbol']}-{sequence:06d}"
                    record = {
                        "trigger_id": trigger_id,
                        "symbol": row["symbol"],
                        "quantity": 1,
                        "trigger_price": row["buy_price"],
                        "order_price": row["buy_price"],
                        "product": "CNC",
                        "exchange": "NSE",
                        "transaction_type": "BUY",
                        "order_type": "LIMIT",
                        "tick_size": row["tick_size"],
                        "stoploss_plan": row["stoploss"],
                        "stoploss_status": "planned_after_buy",
                        "stoploss_trigger_id": None,
                        "status": "active",
                        "created_at": datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(timespec="seconds"),
                        "simulation_only": True,
                    }
                    store["sequence"] = sequence
                    store["records"].append(record)
                    write_dummy_gtt_store(store)
                    row.update(
                        account_state="clear",
                        buy_status="success",
                        active_buy_trigger_id=trigger_id,
                        buy_trigger_id=trigger_id,
                        stoploss_status="planned_after_buy",
                        stoploss_trigger_id=None,
                        message="Dummy buy GTT created. No order sent to Kite. Stoploss planned after buy execution.",
                    )
            results.append(row)
        except Exception as error:
            # A persistence error is reported as a failed simulation. There is
            # deliberately no real-order fallback.
            failed = base_gtt_response(candidate, None)
            failed.update(
                account_state="failed",
                buy_status="failed",
                message=f"Unable to create dummy GTT record: {error}",
            )
            results.append(failed)

    return {"results": results, "count": len(results), "simulation_only": True}
