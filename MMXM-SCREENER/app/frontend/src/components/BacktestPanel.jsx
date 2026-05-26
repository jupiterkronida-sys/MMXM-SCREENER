import React, { useEffect, useState, useCallback } from "react";
import { getBacktest } from "../lib/api";
import { Loader2, History, AlertTriangle } from "lucide-react";

export default function BacktestPanel() {
  const [data, setData] = useState(null);
  const [days, setDays] = useState(30);
  const [loading, setLoading] = useState(false);

  const [error, setError] = useState(null);

  const load = useCallback(async (d) => {
    setLoading(true);
    setError(null);
    try {
      const res = await getBacktest(d);
      setData(res);
    } catch (err) {
      setError(err?.response?.status === 504 ? "Backend timeout — try fewer days or retry." : err.message);
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { load(days); }, [days, load]);

  const onDays = (d) => { setDays(d); };

  return (
    <div className="card" style={{ padding: 18 }} data-testid="backtest-panel">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <History size={16} />
          <h3 className="font-bold tracking-tight">Backtest — MMXM signals</h3>
        </div>
        <div className="flex gap-1">
          {[7, 30, 90].map((d) => (
            <button key={d} onClick={() => onDays(d)}
              className="btn"
              style={{ padding: "6px 10px", fontSize: 12, borderColor: days === d ? "var(--long)" : undefined }}
              data-testid={`backtest-${d}d`}>
              {d}d
            </button>
          ))}
        </div>
      </div>

      <p className="text-xs mb-3" style={{ color: "var(--text-mute)" }}>
        Each past MMXM signal is replayed: did <code className="kbd">TP1</code> hit before <code className="kbd">SL</code>? This is the
        <strong> real</strong> hit-rate, not a marketing number.
      </p>

      {loading && <div className="flex items-center gap-2 text-sm" style={{ color: "var(--text-dim)" }}><Loader2 size={14} className="animate-spin" /> Replaying…</div>}
      {error && <div className="flex items-center gap-2 text-sm mt-2" style={{ color: "var(--short)" }}><AlertTriangle size={14} /> {error}</div>}

      {data && !loading && (
        <div className="grid grid-cols-4 gap-3">
          <Stat label="Total signals" value={data.total} />
          <Stat label="Wins" value={data.wins} color="var(--long)" />
          <Stat label="Losses" value={data.losses} color="var(--short)" />
          <Stat label="Win rate" value={`${data.win_rate}%`} color="var(--gold)" />
        </div>
      )}

      {data && data.total === 0 && (
        <p className="text-xs mt-3" style={{ color: "var(--text-mute)" }}>
          No closed signals yet in this window. Come back later — every scan adds more.
        </p>
      )}
    </div>
  );
}

function Stat({ label, value, color }) {
  return (
    <div className="stat">
      <span className="label">{label}</span>
      <span className="value" style={color ? { color } : {}}>{value}</span>
    </div>
  );
}
