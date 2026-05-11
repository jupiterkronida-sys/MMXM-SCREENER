import axios from "axios";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL?.trim() || "http://localhost:8000";
export const API = `${BACKEND_URL}/api`;
export const WS_API = `${BACKEND_URL.replace(/^http/i, "ws")}/api/ws`;

export const api = axios.create({ baseURL: API, timeout: 600000 });

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export async function getHealth()      { return (await api.get("/health")).data; }
export async function getReplay({ since, sinceSeq, limit = 2000 } = {}) {
  const params = {};
  if (since != null) params.since = since;
  if (sinceSeq != null) params.since_seq = sinceSeq;
  params.limit = limit;
  return (await api.get("/ws/replay", { params })).data;
}
export async function getSignals(p={}) { return (await api.get("/signals", { params: p })).data; }
export async function getStats()       { return (await api.get("/signals/stats")).data; }
export async function runScan() {
  const maxRetries = 2; // total attempts: 3
  for (let attempt = 0; ; attempt += 1) {
    try {
      return (await api.post("/scan/run")).data;
    } catch (error) {
      const status = error?.response?.status;
      const retriable = status === 502 || status === 503 || status === 504;
      if (!retriable || attempt >= maxRetries) throw error;
      await sleep(1500 * (attempt + 1));
    }
  }
}
export async function getRuns()        { return (await api.get("/scan/runs", { params: { limit: 10 } })).data; }
export async function getBacktest(d=30){ return (await api.get("/backtest", { params: { days: d } })).data; }
export async function getMarketTop()   { return (await api.get("/market/top")).data; }
export async function getCoingecko()   { return (await api.get("/market/coingecko")).data; }
export async function getCMC(syms)     { return (await api.get("/market/cmc", { params: { symbols: syms } })).data; }
export async function getKlines(s,iv)  { return (await api.get(`/market/klines/${s}`, { params: { interval: iv, limit: 200 } })).data; }
export async function testTelegram()   { return (await api.post("/telegram/test", { text: "🔔 Test alert from MMXM Screener UI" })).data; }
