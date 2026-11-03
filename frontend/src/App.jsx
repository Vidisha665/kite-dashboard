import { useCallback, useEffect, useRef, useState } from "react";
import { scanStocks } from "./algoScanner.js";

const API_BASE = "/api";

function Icon({ name, size = 20 }) {
  const shared = {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.7,
    strokeLinecap: "round",
    strokeLinejoin: "round",
    "aria-hidden": true,
  };

  if (name === "grid") return <svg {...shared}><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>;
  if (name === "user") return <svg {...shared}><circle cx="12" cy="8" r="3.5"/><path d="M5 20c.5-3.4 3-5.2 7-5.2s6.5 1.8 7 5.2"/></svg>;
  if (name === "arrow") return <svg {...shared}><path d="M5 12h14M13 6l6 6-6 6"/></svg>;
  if (name === "lock") return <svg {...shared}><rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/><path d="M12 14v3"/></svg>;
  if (name === "chart") return <svg {...shared}><path d="M4 19V5M4 19h17"/><path d="m7 15 4-4 3 2 5-7"/></svg>;
  if (name === "pulse") return <svg {...shared}><path d="M3 12h4l2.5-7 5 14 2.5-7h4"/><path d="M21 12h-2"/></svg>;
  return <svg {...shared}><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/></svg>;
}

async function apiRequest(path, options = {}) {
  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers: { "Content-Type": "application/json", ...options.headers },
    });
  } catch {
    throw new Error("Could not reach the local API. Make sure the FastAPI server is running.");
  }

  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload.detail || "Something went wrong. Please try again.");
    error.status = response.status;
    throw error;
  }
  return payload;
}

function LoginPage({ onLogin }) {
  const [form, setForm] = useState({ apiKey: "", apiSecret: "", requestToken: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  function update(event) {
    setForm((current) => ({ ...current, [event.target.name]: event.target.value }));
  }

  async function submit(event) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      await apiRequest("/login", {
        method: "POST",
        body: JSON.stringify({
          api_key: form.apiKey.trim(),
          api_secret: form.apiSecret.trim(),
          request_token: form.requestToken.trim(),
        }),
      });
      // Clear sensitive values from browser memory after the backend accepts them.
      setForm({ apiKey: "", apiSecret: "", requestToken: "" });
      await onLogin();
    } catch (loginError) {
      setError(loginError.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login-shell">
      <section className="login-card">
        <div className="login-form-side">
          <Brand />
          <div className="login-heading">
            <div className="eyebrow"><span className="eyebrow-dot" /> LOCAL WORKSPACE</div>
            <h1>Connect your<br /><span>Kite account.</span></h1>
            <p>Sign in securely to view your account profile in your private dashboard.</p>
          </div>

          <form className="login-form" onSubmit={submit}>
            <label className="field-label" htmlFor="apiKey">API key</label>
            <input id="apiKey" name="apiKey" value={form.apiKey} onChange={update} placeholder="Enter your Kite API key" autoComplete="off" required />

            <label className="field-label" htmlFor="apiSecret">API secret</label>
            <input id="apiSecret" name="apiSecret" type="password" value={form.apiSecret} onChange={update} placeholder="Enter your Kite API secret" autoComplete="new-password" required />

            <label className="field-label" htmlFor="requestToken">Request token</label>
            <input id="requestToken" name="requestToken" value={form.requestToken} onChange={update} placeholder="Paste the token from your Kite redirect" autoComplete="off" required />
            <div className="field-hint"><Icon name="info" size={15} /> Request tokens are short lived. Use a fresh token for each login.</div>

            {error && <div className="notice notice-error" role="alert">{error}</div>}

            <button className="primary-button login-button" type="submit" disabled={busy}>
              {busy ? "Connecting…" : "Connect account"}<Icon name="arrow" size={18} />
            </button>
          </form>
          <div className="secure-note"><Icon name="lock" size={16} /><span>Your access token is stored by the local backend and never sent back to this page.</span></div>
          <div className="login-footer">For personal local development&nbsp; · &nbsp;Kite Connect</div>
        </div>

        <aside className="login-visual" aria-label="Dashboard preview">
          <div className="visual-glow" />
          <div className="visual-topline"><span>ACCOUNT OVERVIEW</span><span className="live-indicator"><i /> LOCAL</span></div>
          <div className="visual-copy"><div className="visual-mark"><Icon name="chart" size={23} /></div><h2>One clear view<br />of your account.</h2><p>A calm, focused workspace for your Kite profile and trading access.</p></div>
          <div className="preview-card">
            <div className="preview-card-head"><span className="preview-avatar">K</span><div><small>ACCOUNT STATUS</small><strong>Ready to connect</strong></div><span className="preview-status" /></div>
            <div className="preview-line" /><div className="preview-grid"><div><small>PROFILE</small><span>Private</span></div><div><small>SESSION</small><span>Backend stored</span></div></div>
          </div>
          <div className="visual-foot"><span>01 / PRIVATE WORKSPACE</span><span>● &nbsp;LOCAL ONLY</span></div>
        </aside>
      </section>
      <div className="page-caption">A local dashboard for your Kite Connect session.</div>
    </main>
  );
}

function Brand() {
  return <div className="brand"><span className="brand-symbol">k<span>.</span></span><span className="brand-name">KITE DESK<small>PERSONAL WORKSPACE</small></span></div>;
}

function Dashboard({ profile, onLogout, onRefresh, refreshing, onGenerateSignals, signalState }) {
  const [activeTab, setActiveTab] = useState("User");
  const [algoRun, setAlgoRun] = useState(null);
  const [algoLoading, setAlgoLoading] = useState(false);
  const [algoError, setAlgoError] = useState("");
  const [tickPrices, setTickPrices] = useState({});
  const [liveStatus, setLiveStatus] = useState("Waiting for scan results");
  const [runLiveLoading, setRunLiveLoading] = useState(false);
  const [gttRows, setGttRows] = useState([]);
  const [gttError, setGttError] = useState("");
  const [gttStateLoading, setGttStateLoading] = useState(false);
  const [gttStateGeneration, setGttStateGeneration] = useState(null);
  const scanInFlight = useRef(false);
  const gttCheckInFlight = useRef(false);
  const scanGeneration = useRef(0);
  const tickPricesRef = useRef(tickPrices);
  tickPricesRef.current = tickPrices;

  const tabs = [
    { name: "Overview", icon: "grid" },
    { name: "User", icon: "user" },
    { name: "Signals", icon: "chart" },
    { name: "Algo Signals", icon: "pulse" },
    { name: "GTT Placement", icon: "lock" },
  ];

  async function refreshAlgoScan(forceRefresh = false) {
    if (scanInFlight.current) throw new Error("The Algo Signals scan is already running.");
    scanInFlight.current = true;
    setAlgoLoading(true);
    setAlgoError("");
    setGttError("");
    setGttRows([]);
    setGttStateGeneration(null);
    try {
      const suffix = forceRefresh ? "?refresh=true" : "";
      const payload = await apiRequest(`/scanner-data${suffix}`);
      const scanned = scanStocks(payload.stocks || []);
      const freshRun = {
        ...scanned,
        metadata: payload.metadata ?? payload.meta ?? {},
        generation: ++scanGeneration.current,
      };
      const symbols = new Set(freshRun.results.map((result) => result.symbol));
      setTickPrices((previous) => Object.fromEntries(
        Object.entries(previous).filter(([symbol]) => symbols.has(symbol)),
      ));
      setAlgoRun(freshRun);
      return freshRun;
    } catch (error) {
      setAlgoRun(null);
      setTickPrices({});
      setAlgoError(error.message);
      throw error;
    } finally {
      scanInFlight.current = false;
      setAlgoLoading(false);
    }
  }

  function candidateOrders(run) {
    const results = run?.results || [];
    return results.map((signal, index) => {
      const snapshot = Number(signal.snapshotLtp);
      const snapshotLtp = Number.isFinite(snapshot) && snapshot > 0 ? snapshot : null;
      const live = tickPricesRef.current[signal.symbol];
      const liveLtp = live && Number.isFinite(Number(live.price)) && Number(live.price) > 0
        ? Number(live.price)
        : null;
      const buyPrice = Number(signal.breakoutLow);
      return {
        symbol: signal.symbol,
        quantity: 1,
        buy_price: Number.isFinite(buyPrice) && buyPrice > 0 ? buyPrice : null,
        stoploss: Number.isFinite(buyPrice) && buyPrice > 0 ? buyPrice * 0.95 : null,
        // GTT eligibility uses only a live tick or fresh OHLC snapshot, never
        // the latest candle close as a substitute for LTP.
        last_price: liveLtp ?? snapshotLtp,
        rank: index + 1,
        companyName: signal.companyName,
      };
    });
  }

  function stateRowsForRun(run, stateRows) {
    const bySymbol = new Map((stateRows || []).map((row) => [String(row.symbol).toUpperCase(), row]));
    return (run?.results || []).map((signal, index) => {
      const state = bySymbol.get(String(signal.symbol).toUpperCase()) || {};
      return {
        ...state,
        symbol: signal.symbol,
        companyName: signal.companyName,
        rank: index + 1,
        last_price: state.last_price ?? candidateOrders(run)[index]?.last_price ?? null,
      };
    });
  }

  async function refreshGttState(run = algoRun) {
    if (!run || gttCheckInFlight.current) return [];
    gttCheckInFlight.current = true;
    setGttStateLoading(true);
    setGttError("");
    try {
      const orders = candidateOrders(run);
      const payload = await apiRequest("/algo-orders/gtt-state", {
        method: "POST",
        body: JSON.stringify({ orders }),
      });
      const rows = stateRowsForRun(run, payload.results || []);
      setGttRows(rows);
      setGttStateGeneration(run.generation);
      return rows;
    } catch (error) {
      setGttError(error.message);
      setGttRows(stateRowsForRun(run, [] ).map((row) => ({
        ...row,
        account_state: "checking",
        buy_status: "checking",
        message: error.message,
      })));
      setGttStateGeneration(run.generation);
      throw error;
    } finally {
      gttCheckInFlight.current = false;
      setGttStateLoading(false);
    }
  }

  useEffect(() => {
    if (
      (activeTab !== "Algo Signals" && activeTab !== "GTT Placement") ||
      algoRun || algoLoading || algoError || scanInFlight.current
    ) return;
    refreshAlgoScan(false).catch(() => {});
    // The scanner loads lazily when either source/results tab is first opened.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab, algoRun, algoLoading, algoError]);

  const algoResults = algoRun?.results || [];
  const tokenToSymbol = Object.fromEntries(
    algoResults
      .filter((result) => result.instrument_token != null)
      .map((result) => [String(result.instrument_token), result.symbol]),
  );
  const subscriptionTokens = [...new Set(Object.keys(tokenToSymbol))];
  const subscriptionKey = subscriptionTokens.join(",");

  useEffect(() => {
    if (!subscriptionKey) {
      setLiveStatus(algoLoading ? "Loading scanner cache..." : "No triggered stocks to subscribe");
      return undefined;
    }
    let cancelled = false;
    setLiveStatus(`Connecting live ticks for ${subscriptionTokens.length} stocks…`);
    const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${wsProtocol}//${window.location.host}/ws/ticks?tokens=${subscriptionKey}&mode=full`);
    socket.onopen = () => {
      if (!cancelled) setLiveStatus(`Live ticks connected · ${subscriptionTokens.length} subscribed`);
    };
    socket.onmessage = (event) => {
      if (cancelled) return;
      try {
        const message = JSON.parse(event.data);
        if (message.type === "status") {
          if (message.status === "connected") setLiveStatus(`Live ticks connected · ${subscriptionTokens.length} subscribed`);
          if (message.status === "connecting") setLiveStatus(`Connecting live ticks for ${subscriptionTokens.length} stocks…`);
          if (message.status === "disconnected") setLiveStatus("Live ticks disconnected · using snapshot prices");
          if (message.status === "error") setLiveStatus(message.message || "Live ticks unavailable · using snapshot prices");
        }
        const ticks = Array.isArray(message) ? message : message.ticks || message.data || [];
        if (Array.isArray(ticks) && ticks.length) {
          setTickPrices((current) => {
            const updated = { ...current };
            for (const tick of ticks) {
              const symbol = tokenToSymbol[String(tick.instrument_token ?? tick.token)];
              const price = Number(tick.last_price ?? tick.ltp);
              if (symbol && Number.isFinite(price) && price > 0) updated[symbol] = { price, receivedAt: Date.now() };
            }
            return updated;
          });
        }
      } catch {
        setLiveStatus("Live tick message could not be read · using snapshot prices");
      }
    };
    socket.onerror = () => { if (!cancelled) setLiveStatus("Live ticks unavailable · using snapshot prices"); };
    socket.onclose = () => { if (!cancelled) setLiveStatus("Live ticks disconnected · using snapshot prices"); };
    return () => { cancelled = true; socket.close(); };
    // The one shared socket follows the current scanner result tokens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subscriptionKey]);

  useEffect(() => {
    if (
      activeTab !== "GTT Placement" || !algoRun || algoLoading ||
      runLiveLoading || gttStateLoading || gttStateGeneration === algoRun.generation
    ) return;
    refreshGttState(algoRun).catch(() => {});
    // State refreshes are read-only and run once per scanner generation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab, algoRun, algoLoading, runLiveLoading, gttStateLoading, gttStateGeneration]);

  async function runLiveSimulation() {
    if (runLiveLoading || algoLoading || gttStateLoading) return;
    setRunLiveLoading(true);
    setGttError("");
    setGttRows([]);
    try {
      const freshRun = await refreshAlgoScan(true);
      const checkedRows = await refreshGttState(freshRun);
      const readyRows = checkedRows.filter((row) =>
        row.buy_status === "ready" &&
        Number.isFinite(Number(row.buy_price)) && Number(row.buy_price) > 0 &&
        Number.isFinite(Number(row.stoploss)) && Number(row.stoploss) > 0 &&
        Number.isFinite(Number(row.last_price)) && Number(row.last_price) > 0,
      );

      if (readyRows.length) {
        const payload = await apiRequest("/algo-orders/live-gtt", {
          method: "POST",
          body: JSON.stringify({
            confirm_live: true,
            orders: readyRows.map(({ symbol, quantity, buy_price, stoploss, last_price }) => ({
              symbol, quantity, buy_price, stoploss, last_price,
            })),
          }),
        });
        const placements = new Map((payload.results || []).map((row) => [String(row.symbol).toUpperCase(), row]));
        const finalRows = checkedRows.map((row) => ({
          ...row,
          ...(placements.get(String(row.symbol).toUpperCase()) || {}),
          rank: row.rank,
          companyName: row.companyName,
        }));
        setGttRows(finalRows);
      }
      setGttStateGeneration(freshRun.generation);
      setActiveTab("GTT Placement");
    } catch (error) {
      setGttError(error.message);
      setActiveTab("GTT Placement");
    } finally {
      setRunLiveLoading(false);
    }
  }

  return (
    <div className="dashboard-shell">
      <aside className="sidebar">
        <Brand />
        <div className="sidebar-label">WORKSPACE</div>
        <nav className="side-nav" aria-label="Dashboard tabs">
          {tabs.map((tab) => <button key={tab.name} className={`nav-item ${activeTab === tab.name ? "active" : ""}`} onClick={() => setActiveTab(tab.name)}><Icon name={tab.icon} size={19} /><span>{tab.name}</span>{activeTab === tab.name && <span className="nav-active-dot" />}</button>)}
        </nav>
        <div className="sidebar-bottom"><div className="sidebar-session"><span className="session-dot" /><div><strong>Connected</strong><small>Kite session active</small></div></div><button className="text-button" onClick={onLogout}>Disconnect account</button></div>
      </aside>

      <main className="dashboard-main">
        <header className="topbar"><div className="breadcrumb"><span>Workspace</span><b>/</b><strong>{activeTab}</strong></div><div className="topbar-right"><span className="environment-tag"><i /> LOCAL DEVELOPMENT</span><span className="topbar-divider" /><div className="mini-avatar">{profile.user_name?.slice(0, 1)?.toUpperCase() || "U"}</div></div></header>

        <section className="page-content">
          {activeTab === "Algo Signals" ? (
            <AlgoSignalsPanel
              run={algoRun}
              loading={algoLoading}
              error={algoError}
              tickPrices={tickPrices}
              liveStatus={liveStatus}
              running={runLiveLoading}
              onRefresh={() => refreshAlgoScan(true).catch(() => {})}
              onRunLive={runLiveSimulation}
              stateLoading={gttStateLoading}
            />
          ) : activeTab === "GTT Placement" ? (
            <GttPlacementPanel
              rows={gttRows}
              loading={algoLoading || gttStateLoading || (runLiveLoading && !gttRows.length)}
              error={algoError || gttError}
              tickPrices={tickPrices}
              liveStatus={liveStatus}
              hasScan={Boolean(algoRun)}
            />
          ) : activeTab === "Signals" ? (
            <SignalsPanel onGenerate={onGenerateSignals} state={signalState} />
          ) : activeTab === "User" ? (
            <>
              <div className="page-heading-row"><div><div className="eyebrow"><span className="eyebrow-dot" /> ACCOUNT</div><h1>User profile</h1><p>Your Kite Connect account details, retrieved securely by the backend.</p></div><button className="secondary-button" onClick={onRefresh} disabled={refreshing}>{refreshing ? "Refreshing…" : "Refresh profile"}<span className="refresh-icon">↻</span></button></div>
              <div className="profile-hero"><div className="profile-avatar">{profile.user_name?.slice(0, 1)?.toUpperCase() || "U"}</div><div className="profile-identity"><span className="profile-kicker">KITE USER</span><h2>{profile.user_name}</h2><span className="user-id">{profile.user_id}</span></div><div className="verified-pill"><i /> ACCOUNT CONNECTED</div></div>
              <div className="section-title"><div><span className="section-index">01</span><h3>Account details</h3></div><span className="section-caption">SYNCED FROM KITE</span></div>
              <div className="detail-grid">
                <DetailCard icon="user" label="User name" value={profile.user_name} hint="Name on your Kite account" />
                <DetailCard icon="info" label="User ID" value={profile.user_id} hint="Your Kite account identifier" />
                <DetailCard icon="chart" label="Products" values={profile.products} hint="Products enabled for your account" />
                <DetailCard icon="grid" label="Exchanges" values={profile.exchanges} hint="Exchanges enabled for your account" />
              </div>
              <div className="privacy-strip"><span className="privacy-icon"><Icon name="lock" size={17} /></span><span><strong>Private by design</strong><small>Your Kite access token remains on this computer and is not exposed to the browser.</small></span><span className="privacy-check">✓</span></div>
            </>
          ) : (
            <>
              <div className="eyebrow"><span className="eyebrow-dot" /> WORKSPACE</div><h1 className="overview-title">Good to see you.</h1><p className="overview-subtitle">Your Kite account is connected and ready.</p>
              <div className="overview-grid"><button className="overview-card" onClick={() => setActiveTab("User")}><div className="overview-card-icon"><Icon name="user" /></div><span className="profile-kicker">ACCOUNT</span><h2>User profile</h2><p>View your account name, ID, products and exchanges.</p><span className="card-link">Open profile <Icon name="arrow" size={16} /></span></button><div className="overview-note"><span className="note-icon"><Icon name="info" /></span><div><strong>Connected securely</strong><p>Profile information is retrieved from Kite through your local FastAPI backend.</p></div></div></div>
            </>
          )}
          <footer className="content-footer"><span>KITE DESK <i>·</i> PRIVATE LOCAL WORKSPACE</span><span>{["Signals", "Algo Signals", "GTT Placement"].includes(activeTab) ? "MARKET DATA PROVIDED BY KITE CONNECT" : "PROFILE DATA PROVIDED BY KITE CONNECT"}</span></footer>
        </section>
      </main>
    </div>
  );
}

function SignalsPanel({ onGenerate, state }) {
  const [settings, setSettings] = useState({ short_sma: 6, long_sma: 30, lookback_days: 365, max_stocks: 100 });

  function update(event) {
    setSettings((current) => ({ ...current, [event.target.name]: event.target.value }));
  }

  function submit(event) {
    event.preventDefault();
    onGenerate({
      short_sma: Number(settings.short_sma),
      long_sma: Number(settings.long_sma),
      lookback_days: Number(settings.lookback_days),
      max_stocks: Number(settings.max_stocks),
    });
  }

  const columns = [
    "Rank", "Ticker", "Company", "Crossover Type", "Crossover Date", "Close",
    `SMA ${state.result?.short_sma ?? settings.short_sma ?? 6}`,
    `SMA ${state.result?.long_sma ?? settings.long_sma ?? 30}`,
  ];

  return (
    <>
      <div className="signals-heading"><div><div className="eyebrow"><span className="eyebrow-dot" /> MARKET SCANNER</div><h1>Signals</h1><p>Find the latest completed daily SMA crossovers across Nifty 100 constituents.</p></div><div className="signals-badge"><Icon name="chart" size={16} /><span>NIFTY 100</span></div></div>
      <form className="signal-controls" onSubmit={submit}>
        <div className="signal-control"><label htmlFor="short_sma">Short SMA</label><input id="short_sma" name="short_sma" type="number" min="1" max="250" value={settings.short_sma} onChange={update} required /><small>Fast average</small></div>
        <div className="signal-control"><label htmlFor="long_sma">Long SMA</label><input id="long_sma" name="long_sma" type="number" min="2" max="500" value={settings.long_sma} onChange={update} required /><small>Slow average</small></div>
        <div className="signal-control"><label htmlFor="lookback_days">Lookback days</label><input id="lookback_days" name="lookback_days" type="number" min="1" max="3650" value={settings.lookback_days} onChange={update} required /><small>Calendar days</small></div>
        <div className="signal-control"><label htmlFor="max_stocks">Max stocks</label><input id="max_stocks" name="max_stocks" type="number" min="1" max="100" value={settings.max_stocks} onChange={update} required /><small>Constituents to scan</small></div>
        <button type="submit" className="primary-button generate-button" disabled={state.loading}><Icon name="chart" size={17} />{state.loading ? "Scanning…" : "Generate signals"}</button>
      </form>

      <div className="signal-intro"><span><span className="signal-status-dot" /> Uses the official NSE Indices constituent list</span><span>Signals are ranked by most recent crossover</span></div>
      {state.error && <div className="notice notice-error signals-error" role="alert">{state.error}</div>}
      {state.result && (
        <>
          <div className="results-heading"><div><span className="section-index">01</span><h2>Latest crossovers</h2></div><span>{state.result.signals.length} signal{state.result.signals.length === 1 ? "" : "s"} <i>·</i> {state.result.scanned} stocks scanned</span></div>
          {state.result.warnings?.length > 0 && <div className="signal-warning">Some instruments could not be scanned ({state.result.warnings.length}). Available signals are shown below.</div>}
          <div className="signals-table-wrap">
            <table className="signals-table">
              <thead><tr>{columns.map((column) => <th key={column}>{column}</th>)}</tr></thead>
              <tbody>
                {state.result.signals.map((signal) => (
                  <tr key={`${signal.ticker}-${signal.crossover_date}`}>
                    <td><span className="rank-cell">{String(signal.rank).padStart(2, "0")}</span></td>
                    <td><strong className="ticker-cell">{signal.ticker}</strong></td>
                    <td className="company-cell">{signal.company}</td>
                    <td><span className={`signal-pill ${signal.crossover_type.toLowerCase()}`}>{signal.crossover_type === "Bullish" ? "↗" : "↘"} {signal.crossover_type}</span></td>
                    <td className="date-cell">{new Date(`${signal.crossover_date}T00:00:00`).toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "numeric" })}</td>
                    <td className="number-cell">{Number(signal.close).toFixed(2)}</td>
                    <td className="number-cell">{Number(signal.short_sma).toFixed(2)}</td>
                    <td className="number-cell">{Number(signal.long_sma).toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {state.result.signals.length === 0 && <div className="empty-signals"><span className="empty-chart"><Icon name="chart" size={21} /></span><strong>No crossovers in this lookback</strong><p>Try a longer lookback period or adjust the SMA windows.</p></div>}
          </div>
          <div className="signals-note"><Icon name="info" size={15} /><span>Only the most recent crossover for each ticker is shown. Daily calculations use completed candles.</span></div>
        </>
      )}
      {!state.result && !state.loading && !state.error && <div className="signals-empty-state"><div className="empty-chart"><Icon name="chart" size={22} /></div><strong>Ready when you are</strong><p>Choose your scan settings, then generate signals to see recent Nifty 100 crossovers.</p></div>}
      {state.loading && <div className="scan-progress"><span className="spinner" />Scanning up to {settings.max_stocks} constituents. This can take a little while.</div>}
    </>
  );
}

function AlgoSignalsPanel({ run, loading, error, tickPrices, liveStatus, running, onRefresh, onRunLive, stateLoading }) {
  const [selectedSymbol, setSelectedSymbol] = useState(null);
  const scan = run || { results: [], scanned: 0 };

  function currentPrice(result) {
    return tickPrices[result.symbol] ?? result.snapshotLtp ?? result.currentPrice;
  }

  function formatPrice(value) {
    const price = Number(value);
    return Number.isFinite(price)
      ? `₹${price.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
      : "—";
  }

  function signedPercent(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return "—";
    return `${number > 0 ? "+" : ""}${number.toFixed(2)}%`;
  }

  return (
    <>
      <div className="algo-heading">
        <div><div className="eyebrow"><span className="eyebrow-dot" /> ALGO SCANNER</div><h1>High-volume breakout and retest setups</h1><p>Recent high-volume breakouts that have returned to their midpoint.</p></div>
        <div className="algo-actions"><button className="secondary-button algo-refresh" onClick={onRefresh} disabled={loading || running}>{loading ? "Loading…" : "Run scanner again"}<span className="refresh-icon">↻</span></button><button className="primary-button" onClick={onRunLive} disabled={loading || running || stateLoading}>{running ? "Running..." : "Run Live"}</button></div>
      </div>
      <div className="algo-parameters"><span>VOL LOOKBACK <b>20</b></span><span>VOL MULTIPLIER <b>3×</b></span><span>PRICE LOOKBACK <b>20</b></span><span>MAX AGE <b>20 candles</b></span><span>MIDPOINT TOLERANCE <b>±1%</b></span></div>
      <div className="algo-summary">
        <div className="algo-summary-item"><small>TRIGGERED BREAKOUTS</small><strong>{loading ? "—" : scan.results.length}</strong></div>
        <div className="algo-summary-item"><small>STOCKS SCANNED</small><strong>{loading ? "—" : scan.scanned}</strong></div>
        <div className="algo-live"><span className={liveStatus.startsWith("Live ticks connected") ? "live-dot on" : "live-dot"} /><span>{liveStatus}</span></div>
      </div>
      <div className="algo-table-heading"><div><span className="section-index">01</span><h2>Triggered Breakouts</h2></div><span>{loading ? "Loading scanner cache..." : `${scan.results.length} matching setups`}</span></div>
      {error && <div className="algo-error" role="alert"><strong>Scanner data error</strong><span>{error}</span></div>}
      <div className="algo-table-wrap">
        <table className="algo-table">
          <thead><tr><th>Rank</th><th>Symbol</th><th>Current Price</th><th>Breakout Date</th><th>Age</th><th>Breakout Vol</th><th>Retest Distance</th><th>Peak Gains</th><th>Retracement</th></tr></thead>
          <tbody>
            {!loading && !error && scan.results.map((result, index) => (
              <tr key={result.symbol} className={selectedSymbol === result.symbol ? "selected" : ""} onClick={() => setSelectedSymbol(result.symbol)} aria-selected={selectedSymbol === result.symbol}>
                <td className="algo-rank">{index + 1}</td>
                <td><div className="algo-symbol">{result.symbol}</div><div className="algo-company">{result.companyName}</div></td>
                <td className="algo-price">{formatPrice(currentPrice(result))}</td>
                <td className="algo-date">{result.breakoutDate}</td>
                <td><span className="algo-age">{result.daysSince}</span></td>
                <td className="algo-volume">{Number(result.volMultiple).toFixed(2)}x</td>
                <td className="algo-retest">{signedPercent(result.distanceFromMidpoint)}</td>
                <td className="algo-gain">{signedPercent(result.returnFromCloseToSubHigh)}</td>
                <td className="algo-retracement">{signedPercent(result.retracementHigh)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      {loading && <div className="algo-state"><span className="spinner" /><strong>Loading scanner cache...</strong></div>}
        {!loading && !error && scan.results.length === 0 && <div className="algo-state"><div className="empty-chart"><Icon name="chart" size={21} /></div><strong>No breakout retest setups match the current parameters.</strong></div>}
      </div>
    </>
  );
}

function GttPlacementPanel({ rows, loading, error, tickPrices, liveStatus, hasScan }) {
  const [selected, setSelected] = useState(null);
  const price = (row) => {
    const tick = tickPrices[row.symbol];
    const value = tick?.price ?? row.last_price;
    return Number.isFinite(Number(value)) && Number(value) > 0 ? `₹${Number(value).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "Waiting";
  };
  return <>
    <div className="gtt-heading"><div><div className="eyebrow"><span className="eyebrow-dot" /> SIMULATION WORKFLOW</div><h1>GTT Order Placement</h1><p>Read-only status from Algo Signals · Qty 1 · Buy GTT at breakout candle low · Stoploss 5% below buy · State from Kite orders, positions, holdings, and GTTs</p></div><span className="gtt-simulation-pill"><Icon name="lock" size={15} /> SIMULATION ONLY</span></div>
    <div className="gtt-notice">No orders or GTTs are sent to Kite. Successful BUY plans receive a local <code>DUMMY-</code> ID; the 5% stoploss is recorded as a plan after a simulated buy.</div>
    <div className="gtt-summary"><span><i className="live-dot" />{liveStatus}</span><strong>{rows.length} scanner candidates</strong></div>
    {error && <div className="algo-error" role="alert"><strong>GTT state error</strong><span>{error}</span></div>}
    <div className="gtt-table-heading"><div><span className="section-index">01</span><h2>Kite GTT Placement</h2></div><span>Read-only Kite checks · local dummy records</span></div>
    <div className="gtt-table-wrap"><table className="gtt-table"><thead><tr><th>Rank</th><th>Symbol</th><th>Qty</th><th>Buy</th><th>Stoploss</th><th>Last Price</th><th>Account State</th><th>Buy Status</th><th>Buy Trigger</th><th>Day Buy Order</th><th>Position Qty</th><th>Stoploss Status</th><th>Stoploss Trigger</th><th>Message</th></tr></thead><tbody>{rows.map((row, index) => <tr key={row.symbol} className={selected === row.symbol ? "selected" : ""} onClick={() => setSelected(row.symbol)}><td>{row.rank ?? index + 1}</td><td><strong>{row.symbol}</strong></td><td>{row.quantity ?? 1}</td><td>{row.buy_price != null ? `₹${Number(row.buy_price).toFixed(2)}` : "—"}</td><td>{row.stoploss != null ? `₹${Number(row.stoploss).toFixed(2)}` : "—"}</td><td>{price(row)}</td><td>{row.account_state || "checking"}</td><td><span className={`gtt-status ${row.buy_status || "checking"}`}>{row.buy_status || "checking"}</span></td><td>{row.buy_trigger_id || row.active_buy_trigger_id || "—"}</td><td>{row.day_buy_order_id ? `${row.day_buy_order_id} (${row.day_buy_order_status || "open"})` : "—"}</td><td>{row.position_quantity ?? 0}</td><td>{row.stoploss_status || "not_available"}</td><td>{row.stoploss_trigger_id || row.active_stoploss_trigger_id || "—"}</td><td className="gtt-message">{row.message || "—"}</td></tr>)}</tbody></table>
      {loading && <div className="algo-state"><span className="spinner" /><strong>Loading scanner cache...</strong></div>}
      {!loading && !error && !rows.length && <div className="algo-state"><strong>{hasScan ? "No scanner candidates are available." : "Open Algo Signals and run the scanner to load candidates."}</strong></div>}
    </div>
  </>;
}

function DetailCard({ icon, label, value, values, hint }) {
  const items = values || [];
  const empty = values && items.length === 0;
  return <article className="detail-card"><div className="detail-card-top"><span className="detail-icon"><Icon name={icon} size={18} /></span><span className="detail-label">{label}</span><span className="detail-arrow">↗</span></div>{values ? <div className="detail-value-list">{empty ? <span className="empty-value">None listed</span> : items.map((item) => <span className="value-chip" key={item}>{item}</span>)}</div> : <div className="detail-value">{value}</div>}<div className="detail-hint">{hint}</div></article>;
}

export default function App() {
  const [profile, setProfile] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [pageError, setPageError] = useState("");
  const [signalsLoading, setSignalsLoading] = useState(false);
  const [signalsError, setSignalsError] = useState("");
  const [signalsResult, setSignalsResult] = useState(null);

  const loadProfile = useCallback(async () => {
    const result = await apiRequest("/profile");
    setProfile(result);
    setPageError("");
    return result;
  }, []);

  useEffect(() => {
    let active = true;
    apiRequest("/session")
      .then(async (session) => {
        if (session.authenticated) {
          const result = await apiRequest("/profile");
          if (active) setProfile(result);
        }
      })
      .catch((error) => {
        if (active) setPageError(error.message);
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  async function handleLogin() {
    await loadProfile();
  }

  async function refreshProfile() {
    setRefreshing(true);
    try {
      await loadProfile();
    } catch (error) {
      setPageError(error.message);
      if (error.status === 401) setProfile(null);
    } finally {
      setRefreshing(false);
    }
  }

  async function disconnect() {
    try { await apiRequest("/session", { method: "DELETE" }); } catch { /* Local sign-out still works if the API stops. */ }
    setProfile(null);
  }

  async function generateSignals(settings) {
    setSignalsLoading(true);
    setSignalsError("");
    setSignalsResult(null);
    try {
      const result = await apiRequest("/signals", {
        method: "POST",
        body: JSON.stringify(settings),
      });
      setSignalsResult(result);
    } catch (error) {
      setSignalsError(error.message);
    } finally {
      setSignalsLoading(false);
    }
  }

  if (loading) return <main className="loading-screen"><div className="loading-mark">k<span>.</span></div><span>Opening your workspace…</span></main>;
  if (profile) return <Dashboard
    profile={profile}
    onLogout={disconnect}
    onRefresh={refreshProfile}
    refreshing={refreshing}
    onGenerateSignals={generateSignals}
    signalState={{ loading: signalsLoading, error: signalsError, result: signalsResult }}
  />;
  return <><LoginPage onLogin={handleLogin} />{pageError && <div className="global-error" role="status">{pageError}</div>}</>;
}
