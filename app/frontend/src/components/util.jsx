import React from "react";

export function Stars({ n }) {
  return (
    <span className="mono" data-testid="confidence-stars">
      {Array.from({ length: 5 }).map((_, i) => (
        <span key={i} style={{ color: i < n ? "var(--gold)" : "var(--line-2)" }}>
          ★
        </span>
      ))}
    </span>
  );
}

export function fmt(v, digits = 4) {
  if (v === null || v === undefined || isNaN(v)) return "—";
  const abs = Math.abs(v);
  if (abs >= 1000) return v.toFixed(2);
  if (abs >= 1) return v.toFixed(3);
  if (abs >= 0.01) return v.toFixed(5);
  return v.toFixed(8).replace(/0+$/, "").replace(/\.$/, "");
}

export function fmtPct(v) {
  if (v === null || v === undefined || isNaN(v)) return "—";
  const sign = v >= 0 ? "+" : "";
  return `${sign}${v.toFixed(2)}%`;
}

export function timeAgo(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const s = Math.floor((Date.now() - d.getTime()) / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}
