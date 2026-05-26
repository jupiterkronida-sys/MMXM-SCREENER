export const initialRealtimeState = {
  signalsById: {},
  signalIds: [],
  tickersBySymbol: {},
  stats: null,
  health: null,
  scanner: { running: false, lastCompleted: null },
  stream: { connected: false, mode: "bootstrap" },
  lastSeq: 0,
};

export function buildBootstrapState({ signals = [], stats = null, health = null, tickers = [] } = {}) {
  const signalsById = {};
  const signalIds = [];
  for (const s of signals) {
    if (!s?.id) continue;
    signalsById[s.id] = s;
    signalIds.push(s.id);
  }
  const tickersBySymbol = {};
  for (const t of tickers) {
    if (!t?.symbol) continue;
    tickersBySymbol[t.symbol] = t;
  }
  return {
    ...initialRealtimeState,
    signalsById,
    signalIds,
    tickersBySymbol,
    stats,
    health,
    stream: { connected: false, mode: "bootstrap" },
  };
}

export function realtimeReducer(state, action) {
  switch (action.type) {
    case "BOOTSTRAP":
      return buildBootstrapState(action.payload);
    case "STREAM_STATE":
      return { ...state, stream: { ...state.stream, ...action.payload } };
    case "EVENT":
      return applyEvent(state, action.payload);
    default:
      return state;
  }
}

function applyEvent(state, event) {
  if (!event) return state;
  const seq = Number(event.seq || 0);
  if (seq && seq <= state.lastSeq) return state;

  const next = { ...state, lastSeq: seq || state.lastSeq };
  const payload = event.payload || {};

  switch (event.type) {
    case "signal:new": {
      const sig = payload;
      if (!sig?.id || next.signalsById[sig.id]) return next;
      const signalsById = { ...next.signalsById, [sig.id]: sig };
      const signalIds = [sig.id, ...next.signalIds].slice(0, 500);
      return { ...next, signalsById, signalIds };
    }
    case "signal:update": {
      const sig = payload;
      if (!sig?.id) return next;
      const prev = next.signalsById[sig.id] || {};
      const signalsById = { ...next.signalsById, [sig.id]: { ...prev, ...sig } };
      const signalIds = next.signalIds.includes(sig.id) ? next.signalIds : [sig.id, ...next.signalIds];
      return { ...next, signalsById, signalIds: signalIds.slice(0, 500) };
    }
    case "market:ticker": {
      if (!payload?.symbol) return next;
      return {
        ...next,
        tickersBySymbol: { ...next.tickersBySymbol, [payload.symbol]: payload },
      };
    }
    case "market:snapshot": {
      const tickersBySymbol = { ...next.tickersBySymbol };
      for (const t of payload?.tickers || []) {
        if (t?.symbol) tickersBySymbol[t.symbol] = t;
      }
      return { ...next, tickersBySymbol };
    }
    case "stats:update":
      return { ...next, stats: payload };
    case "health:update":
      return { ...next, health: payload };
    case "scan:started":
      return { ...next, scanner: { ...next.scanner, running: true } };
    case "scan:completed":
      return { ...next, scanner: { running: false, lastCompleted: payload } };
    default:
      return next;
  }
}

export function selectSignals(state) {
  return state.signalIds.map((id) => state.signalsById[id]).filter(Boolean);
}

export function selectTickers(state) {
  return Object.values(state.tickersBySymbol).sort((a, b) => (b.turnover_24h || 0) - (a.turnover_24h || 0));
}
