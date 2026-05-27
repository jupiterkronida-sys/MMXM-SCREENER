import React, { useState } from "react";
import { Stars, fmt, fmtPct, timeAgo } from "./util";
import { ChevronDown, Crosshair, ShieldAlert, Target, Activity, BarChart3, TrendingUp, TrendingDown, Layers, Clock } from "lucide-react";

export default function SignalCard({ sig }) {
  const [open, setOpen] = useState(false);
  const isLong = sig.side === "long";
  const isMmxm = sig.source === "mmxm";
  const isConfluence = sig.source === "confluence";
  
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
              ) : isConfluence ? (
                <span className="tag" style={{ background: "rgba(168,85,247,0.2)", color: "#a855f7" }}>CONFLUENCE</span>
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
      ) : isConfluence ? (
        <div className="space-y-3 mt-4">
          {/* Main Confluence Metrics */}
          <div className="grid grid-cols-2 gap-2">
            <KV label="Current Price" value={fmt(sig.current_price)} mono />
            <KV label="Impact In" value={`${sig.bars_to_impact} bars`} mono icon={<Clock size={12} />} />
            <KV label="S/R Level" value={fmt(sig.sr_level)} mono color={sig.sr_type === "SUPPORT" ? "var(--long)" : "var(--short)"} icon={sig.sr_type === "SUPPORT" ? <TrendingUp size={12} /> : <TrendingDown size={12} />} />
            <KV label="S/R Strength" value={`${sig.sr_strength}%`} mono color="var(--gold)" />
          </div>
          
          {/* Shadow Path Preview */}
          {sig.shadow_path && sig.shadow_path.length > 0 && (
            <div className="pt-2" style={{ borderTop: "1px solid var(--line)" }}>
              <div className="text-[10px] uppercase tracking-wider mb-1" style={{ color: "var(--text-mute)" }}>
                Future Shadow Projection
              </div>
              <div className="flex items-end gap-0.5 h-12" style={{ background: "rgba(0,0,0,0.3)", borderRadius: 6, padding: 4 }}>
                {sig.shadow_path.slice(0, 10).map((price, idx) => {
                  const min = Math.min(...sig.shadow_path);
                  const max = Math.max(...sig.shadow_path);
                  const range = max - min || 1;
                  const height = ((price - min) / range) * 100;
                  return (
                    <div
                      key={idx}
                      className="flex-1 rounded-sm"
                      style={{
                        height: `${Math.max(10, height)}%`,
                        background: idx === 0 ? (isLong ? "var(--long)" : "var(--short)") : "rgba(168,85,247,0.5)",
                        opacity: 0.3 + (idx / sig.shadow_path.length) * 0.7,
                      }}
                    />
                  );
                })}
              </div>
              <div className="text-[10px] mt-1" style={{ color: "var(--text-dim)" }}>
                Projected: {fmt(sig.shadow_path[0])} → {fmt(sig.shadow_path[sig.shadow_path.length - 1])}
              </div>
            </div>
          )}
          
          {/* Confluence Scores */}
          <div className="grid grid-cols-3 gap-2 pt-2" style={{ borderTop: "1px solid var(--line)" }}>
            <KV label="Pattern" value={`${Math.round(sig.pattern_score * 100)}%`} mono />
            <KV label="Zone" value={`${Math.round(sig.zone_score * 100)}%`} mono />
            <KV label="Total" value={`${Math.round(sig.combined_score * 100)}%`} mono color="var(--gold)" bold />
          </div>
          
          {/* Reaction Type */}
          <div className="text-xs pt-2" style={{ color: "var(--text-mute)", borderTop: "1px solid var(--line)" }}>
            <Layers size={12} className="inline mr-1" />
            Expected reaction: <strong style={{ color: "var(--text)" }}>{sig.reaction_type?.replace("_", " ") || "Unknown"}</strong>
            {sig.sr_type === "SUPPORT" ? " off support" : " off resistance"}
          </div>
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

function KV({ label, value, mono, dim, color, icon, bold }) {
  return (
    <div>
      <div className="flex items-center gap-1 text-[10px] uppercase tracking-wider" style={{ color: "var(--text-mute)" }}>
        {icon}
        {label}
      </div>
      <div className={mono ? "mono font-semibold" : "font-semibold"} style={{ color: color || (dim ? "var(--text-dim)" : "var(--text)"), fontSize: 14, fontWeight: bold ? 700 : undefined }}>
        {value}
      </div>
    </div>
  );
}
