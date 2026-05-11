import React from "react";
import { fmtPct } from "./util";
import { TrendingUp, TrendingDown } from "lucide-react";

export default function MarketStrip({ tickers, stream }) {
  if (!tickers?.length) return null;
  // top 12 by turnover
  const list = tickers.slice(0, 14);
  return (
    <div className="glass" style={{ borderRadius: 12, padding: "8px 12px", overflow: "hidden" }} data-testid="market-strip">
      <div className="text-[10px] mb-1 mono" style={{ color: stream?.connected ? "var(--long)" : "var(--text-mute)" }}>
        {stream?.connected ? "STREAM:LIVE" : "STREAM:RECONNECTING"}
      </div>
      <div className="flex gap-6 overflow-x-auto scrollbar" style={{ whiteSpace: "nowrap" }}>
        {list.map((t) => {
          const up = t.price_change_24h_pct >= 0;
          return (
            <div key={t.symbol} className="flex items-center gap-2" style={{ minWidth: "fit-content" }}>
              <span className="mono text-xs font-semibold" style={{ color: "var(--text-dim)" }}>
                {t.symbol.replace("USDT", "")}
              </span>
              <span className="mono text-xs">${t.last_price < 1 ? t.last_price.toFixed(5) : t.last_price.toFixed(2)}</span>
              <span className="mono text-xs flex items-center gap-1" style={{ color: up ? "var(--long)" : "var(--short)" }}>
                {up ? <TrendingUp size={12} /> : <TrendingDown size={12} />}
                {fmtPct(t.price_change_24h_pct)}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
