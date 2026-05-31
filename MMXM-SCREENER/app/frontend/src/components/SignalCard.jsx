import { useState } from "react";
import { Stars, fmt, fmtPct, timeAgo } from "./util";
import { ChevronDown, Crosshair, ShieldAlert, Target, Activity, BarChart3 } from "lucide-react";

export default function SignalCard({ sig }) {
  const [open, setOpen] = useState(false);
  const isLong = sig.side === "long";
  const isMmxm = sig.source === "mmxm";

  return (
    <div className={`card signal-card ${sig.side}`} data-testid={`signal-card-${sig.symbol}-${sig.source}-${sig.side}`}>
      {/* Header */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <div>
            <div className="flex items-center gap-2">
              <span className="font-extrabold text-lg tracking-tight">{sig.symbol}</span>
              <span className={`tag ${isLong ? "tag-long" : "tag-short"}`}>
                {isLong ? "LONG" : "SHORT"}
              </span>
              {isMmxm ? (
                <span className="tag">MMXM · {sig.timeframe}</span>
              ) : (
                <span className="tag">SCREENER · 1H</span>
              )}
            </div>
            <div className="text-xs mt-1" style={{ color: "var(--text-mute)" }}>
              {timeAgo(sig.created_at)}
              {sig.telegram_sent && <span className="ml-2">· 📲 sent</span>}
            </div>
          </div>
        </div>
        <Stars n={sig.confidence || 0} />
      </div>

      {/* Body */}
      {isMmxm ? (
        <div className="grid grid-cols-2 gap-2 mt-4">
          <KV label="Price" value={fmt(sig.current_price)} mono />
          <KV label="R:R (TP2)" value={`${sig.risk_reward_tp2}×`} mono />
          <KV label="Entry zone" value={`${fmt(sig.entry_zone_low)} – ${fmt(sig.entry_zone_high)}`} mono />
          <KV label="Swept" value={fmt(sig.swept_level)} mono dim />
          <KV label="Stop loss" value={fmt(sig.stop_loss)} mono color="var(--short)" icon={<ShieldAlert size={12} />} />
          <KV label="Take profit 1" value={fmt(sig.take_profit_1)} mono color="var(--long)" icon={<Target size={12} />} />
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-2 mt-4">
          <KV label="Price" value={fmt(sig.current_price)} mono />
          <KV label="1h move" value={fmtPct(sig.pct_change_1h)} mono color={sig.pct_change_1h >= 0 ? "var(--long)" : "var(--short)"} />
          <KV label="Volume Z" value={`${sig.volume_zscore}σ`} mono icon={<BarChart3 size={12} />} />
          <KV label="RSI(14)" value={sig.rsi} mono icon={<Activity size={12} />} />
        </div>
      )}

      {isMmxm && (
        <button
          className="mt-4 text-xs flex items-center gap-1"
          style={{ color: "var(--text-dim)" }}
          onClick={() => setOpen(!open)}
          data-testid={`expand-${sig.id}`}
        >
          <ChevronDown size={14} style={{ transform: open ? "rotate(180deg)" : "none", transition: "transform .15s" }} />
          {open ? "Hide trade plan" : "Show full trade plan"}
        </button>
      )}

      {open && isMmxm && (
        <div className="mt-3 pt-3" style={{ borderTop: "1px solid var(--line)" }}>
          <div className="grid grid-cols-3 gap-2 text-sm">
            <KV label="TP1" value={fmt(sig.take_profit_1)} mono color="var(--long)" />
            <KV label="TP2" value={fmt(sig.take_profit_2)} mono color="var(--long)" />
            <KV label="TP3" value={fmt(sig.take_profit_3)} mono color="var(--long)" />
          </div>
          <div className="text-xs mt-3" style={{ color: "var(--text-mute)" }}>
            <Crosshair size={12} className="inline mr-1" />
            {isLong ? "Bullish" : "Bearish"} MMXM: liquidity sweep at <code className="kbd">{fmt(sig.swept_level)}</code>
            {" + "}MSS confirmed. Entry zone is the {sig.ob_used && sig.fvg_used ? "Order Block + FVG" : sig.ob_used ? "Order Block" : "Fair Value Gap"}.
          </div>
        </div>
      )}
    </div>
  );
}

function KV({ label, value, mono, dim, color, icon }) {
  return (
    <div>
      <div className="flex items-center gap-1 text-[10px] uppercase tracking-wider" style={{ color: "var(--text-mute)" }}>
        {icon}
        {label}
      </div>
      <div className={mono ? "mono font-semibold" : "font-semibold"} style={{ color: color || (dim ? "var(--text-dim)" : "var(--text)"), fontSize: 14 }}>
        {value}
      </div>
    </div>
  );
}
