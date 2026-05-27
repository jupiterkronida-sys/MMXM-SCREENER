import React, { useEffect, useState, useCallback, useReducer, useMemo, useRef } from "react";
import {
  getHealth, getSignals, getStats, runScan, getMarketTop, testTelegram,
} from "../lib/api";
import { RealtimeWSClient } from "../lib/wsClient";
import { initialRealtimeState, realtimeReducer, selectSignals, selectTickers } from "../lib/realtimeStore";
import SignalCard from "./SignalCard";
import MarketStrip from "./MarketStrip";
import BacktestPanel from "./BacktestPanel";
import {
  RefreshCw, Send, AlertTriangle, ScanLine, Activity, Zap, ShieldAlert,
  Filter, ArrowUpRight, ArrowDownRight, Layers,
} from "lucide-react";

const SIDES = [
  { id: "all", label: "All" },
  { id: "long", label: "Longs" },
  { id: "short", label: "Shorts" },
];
const SOURCES = [
  { id: "all", label: "All" },
  { id: "mmxm", label: "MMXM" },
  { id: "screener", label: "Pump/Dump" },
];

export default function Dashboard() {
  const [rtState, dispatch] = useReducer(realtimeReducer, initialRealtimeState);
  const wsRef = useRef(null);
  const [loading, setLoading] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [sideF, setSideF] = useState("all");
  const [sourceF, setSourceF] = useState("all");
  const [minConf, setMinConf] = useState(0);

  const bootstrap = useCallback(async () => {
    setLoading(true);
    const wrap = (fn) => fn().catch((e) => { console.error(e); return null; });
    const [sigs, st, h, mt] = await Promise.all([
      wrap(() => getSignals({ limit: 100 })),
      wrap(() => getStats()),
      wrap(() => getHealth()),
      wrap(() => getMarketTop()),
    ]);
    dispatch({
      type: "BOOTSTRAP",
      payload: {
        signals: sigs || [],
        stats: st,
        health: h,
        tickers: mt || [],
      },
    });
    setLoading(false);
  }, []);

  useEffect(() => {
    let mounted = true;
    const init = async () => {
      await bootstrap();
      if (!mounted) return;
      const ws = new RealtimeWSClient({
        onEvent: (ev) => dispatch({ type: "EVENT", payload: ev }),
        onState: (st) => dispatch({ type: "STREAM_STATE", payload: st }),
        initialTopics: ["*", "market", "signals", "scanner"],
      });
      wsRef.current = ws;
      ws.start(0);
    };
    init();
    return () => {
      mounted = false;
      wsRef.current?.stop();
      wsRef.current = null;
    };
  }, [bootstrap]);

  const signals = useMemo(() => selectSignals(rtState), [rtState]);
  const tickers = useMemo(() => selectTickers(rtState), [rtState]);
  const stats = rtState.stats;
  const health = rtState.health;

  const onScan = async () => {
    setScanning(true);
    try {
      await runScan();
    } finally {
      setScanning(false);
    }
  };

  const onTestTg = async () => {
    const r = await testTelegram();
    alert(r.sent ? "✅ Test message sent to Telegram." : "❌ Telegram failed. Check token/chat ID in backend .env.");
  };

  const filtered = signals.filter((s) => {
    if (sideF !== "all" && s.side !== sideF) return false;
    if (sourceF !== "all" && s.source !== sourceF) return false;
    if ((s.confidence || 0) < minConf) return false;
    return true;
  });

  const longs = filtered.filter((s) => s.side === "long");
  const shorts = filtered.filter((s) => s.side === "short");

  return (
    <div className="min-h-screen scanline-bg">
      <Header onScan={onScan} scanning={scanning} onTestTg={onTestTg} health={health} />

      <main className="max-w-[1400px] mx-auto px-6 py-6 space-y-6">
        {/* NFA banner */}
        <div className="banner-warn" data-testid="nfa-banner">
          <AlertTriangle size={16} />
          <span>
            <strong>Not financial advice.</strong> Crypto trading carries substantial risk. Signals are pattern-based heuristics, not predictions. Realistic win rates are 45–60%, never 90%. Always use stop losses and risk only what you can afford to lose.
          </span>
        </div>

        <MarketStrip tickers={tickers} stream={rtState.stream} />

        {/* Stats row */}
        <section className="grid grid-cols-2 md:grid-cols-5 gap-3" data-testid="stats-row">
          <StatTile icon={<Layers size={14} />} label="Signals (7d)" value={stats?.total_7d ?? "—"} />
          <StatTile icon={<ArrowUpRight size={14} />} label="Longs (7d)" value={stats?.longs_7d ?? "—"} color="var(--long)" />
          <StatTile icon={<ArrowDownRight size={14} />} label="Shorts (7d)" value={stats?.shorts_7d ?? "—"} color="var(--short)" />
          <StatTile icon={<ShieldAlert size={14} />} label="MMXM setups" value={stats?.mmxm_7d ?? "—"} />
          <StatTile icon={<Zap size={14} />} label="Pump/Dump" value={stats?.screener_7d ?? "—"} color="var(--gold)" />
        </section>

        <BacktestPanel />

        {/* Filters */}
        <section className="flex flex-wrap items-center gap-2" data-testid="filters">
          <div className="flex items-center gap-2 text-sm" style={{ color: "var(--text-dim)" }}>
            <Filter size={14} /> Filters
          </div>
          <SegGroup options={SIDES} value={sideF} onChange={setSideF} testid="side" />
          <SegGroup options={SOURCES} value={sourceF} onChange={setSourceF} testid="source" />
          <div className="flex items-center gap-1">
            {[0, 2, 3, 4].map((c) => (
              <button key={c} className="btn" style={{ padding: "6px 10px", fontSize: 12, borderColor: minConf === c ? "var(--gold)" : undefined }}
                onClick={() => setMinConf(c)} data-testid={`min-conf-${c}`}>
                {c === 0 ? "Any conf" : `${c}+ ★`}
              </button>
            ))}
          </div>
          <div className="ml-auto text-xs" style={{ color: "var(--text-mute)" }}>
            {filtered.length} signal{filtered.length === 1 ? "" : "s"} shown · {rtState.stream.connected ? "live stream" : "reconnecting"}
          </div>
        </section>

        {/* Two column: longs / shorts */}
        <section className="grid lg:grid-cols-2 gap-4">
          <Column title="Longs" tone="long" sigs={longs} />
          <Column title="Shorts" tone="short" sigs={shorts} />
        </section>

        <Footer health={health} />
      </main>
    </div>
  );
}

function StatTile({ icon, label, value, color }) {
  return (
    <div className="stat" data-testid={`stat-${label}`}>
      <span className="label flex items-center gap-1.5" style={{ color: "var(--text-mute)" }}>
        {icon} {label}
      </span>
      <span className="value" style={color ? { color } : {}}>{value}</span>
    </div>
  );
}

function SegGroup({ options, value, onChange, testid }) {
  return (
    <div className="flex" style={{ border: "1px solid var(--line)", borderRadius: 10, overflow: "hidden" }}>
      {options.map((o) => (
        <button key={o.id}
          onClick={() => onChange(o.id)}
          data-testid={`seg-${testid}-${o.id}`}
          style={{
            padding: "7px 12px", fontSize: 12, fontWeight: 600,
            background: value === o.id ? "rgba(255,255,255,0.06)" : "transparent",
            color: value === o.id ? "var(--text)" : "var(--text-dim)",
            border: "none", cursor: "pointer",
          }}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

function Column({ title, tone, sigs }) {
  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-lg font-bold tracking-tight flex items-center gap-2">
          {tone === "long" ? <ArrowUpRight size={16} style={{ color: "var(--long)" }} /> : <ArrowDownRight size={16} style={{ color: "var(--short)" }} />}
          {title}
          <span className="tag" style={{ marginLeft: 4 }}>{sigs.length}</span>
        </h2>
      </div>
      {sigs.length === 0 ? (
        <div className="card" style={{ padding: 28, textAlign: "center", color: "var(--text-mute)", fontSize: 13 }} data-testid={`empty-${tone}`}>
          <ScanLine size={20} className="inline-block mb-2 opacity-60" />
          <div>No active {title.toLowerCase()} matching filters.</div>
          <div className="text-xs mt-1">The scanner runs every 5 min — new setups will appear here.</div>
        </div>
      ) : (
        <div className="space-y-3">
          {sigs.map((s) => <SignalCard key={s.id} sig={s} />)}
        </div>
      )}
    </div>
  );
}

function Header({ onScan, scanning, onTestTg, health }) {
  return (
    <header className="sticky top-0 z-20" style={{ background: "rgba(7,9,12,0.85)", backdropFilter: "blur(10px)", borderBottom: "1px solid var(--line)" }} data-testid="app-header">
      <div className="max-w-[1400px] mx-auto px-6 py-3 flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <Logo />
          <div>
            <div className="font-extrabold text-lg tracking-tight leading-none">MMXM<span style={{ color: "var(--long)" }}>·</span>Screener</div>
            <div className="text-[11px] mono mt-1" style={{ color: "var(--text-mute)" }}>
              <span className="dot dot-live mr-1.5" />
              {health?.last_scan ? <>last scan {Math.max(0, Math.round((Date.now() - new Date(health.last_scan.finished_at).getTime()) / 1000))}s ago · {health.total_signals} total signals</> : "scanner online"}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button className="btn" onClick={onTestTg} data-testid="test-telegram">
            <Send size={14} /> Test Telegram
          </button>
          <button className="btn btn-primary" onClick={onScan} disabled={scanning} data-testid="run-scan">
            <RefreshCw size={14} className={scanning ? "animate-spin" : ""} />
            {scanning ? "Scanning…" : "Scan now"}
          </button>
        </div>
      </div>
    </header>
  );
}

function Logo() {
  return (
    <div style={{ width: 36, height: 36, borderRadius: 10, background: "linear-gradient(135deg, var(--long), #1a8b66)", display: "grid", placeItems: "center", boxShadow: "0 4px 24px rgba(43,217,159,0.25)" }}>
      <Activity size={18} color="#052016" strokeWidth={3} />
    </div>
  );
}

function Footer({ health }) {
  return (
    <footer className="pt-6 pb-10 text-xs" style={{ color: "var(--text-mute)" }} data-testid="app-footer">
      <div className="hairline mb-4" />
      <div className="flex flex-wrap items-center gap-3 justify-between">
        <div>
          Data: <strong style={{ color: "var(--text-dim)" }}>Gate.io</strong> (primary) · <strong style={{ color: "var(--text-dim)" }}>CoinGecko</strong> · <strong style={{ color: "var(--text-dim)" }}>CoinMarketCap</strong>
        </div>
        <div>
          Telegram: {health?.telegram_configured ? "✅ configured" : "❌ off"} · CMC: {health?.cmc_configured ? "✅" : "❌"}
        </div>
        <div>Scan interval: {health?.scan_interval_seconds ?? 300}s · Universe: top {health?.top_n ?? 60}</div>
      </div>
    </footer>
  );
}
