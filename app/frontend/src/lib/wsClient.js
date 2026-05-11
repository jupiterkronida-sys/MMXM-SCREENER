import { WS_API, getReplay } from "./api";

export class RealtimeWSClient {
  constructor({
    onEvent,
    onState,
    initialTopics = ["*"],
  } = {}) {
    this.onEvent = onEvent;
    this.onState = onState;
    this.initialTopics = initialTopics;
    this.ws = null;
    this.connected = false;
    this.shouldRun = false;
    this.backoffMs = 1000;
    this.maxBackoffMs = 20000;
    this.heartbeatId = null;
    this.staleGuardId = null;
    this.lastMessageAt = 0;
    this.lastSeq = 0;
  }

  start(lastKnownSeq = 0) {
    this.lastSeq = lastKnownSeq || 0;
    this.shouldRun = true;
    this._connect();
  }

  stop() {
    this.shouldRun = false;
    this._clearTimers();
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    this.connected = false;
  }

  _connect() {
    if (!this.shouldRun) return;
    this.onState?.({ connected: false, mode: "connecting" });
    const url = this.lastSeq > 0 ? `${WS_API}?since_seq=${this.lastSeq + 1}` : WS_API;
    this.ws = new WebSocket(url);
    this.ws.onopen = () => this._onOpen();
    this.ws.onmessage = (evt) => this._onMessage(evt);
    this.ws.onerror = () => this._onClose();
    this.ws.onclose = () => this._onClose();
  }

  async _onOpen() {
    this.connected = true;
    this.backoffMs = 1000;
    this.lastMessageAt = Date.now();
    this.onState?.({ connected: true, mode: "live" });
    this._send({ op: "subscribe", topics: this.initialTopics });
    await this._replayGap();
    this._startTimers();
  }

  _onMessage(evt) {
    this.lastMessageAt = Date.now();
    let event;
    try {
      event = JSON.parse(evt.data);
    } catch {
      return;
    }
    if (event?.type === "system:ping") {
      this._send({ op: "pong", ts: Date.now() });
      return;
    }
    if (typeof event?.seq === "number") this.lastSeq = Math.max(this.lastSeq, event.seq);
    this.onEvent?.(event);
  }

  _onClose() {
    if (!this.connected && !this.shouldRun) return;
    this.connected = false;
    this._clearTimers();
    this.onState?.({ connected: false, mode: "reconnecting" });
    if (!this.shouldRun) return;
    setTimeout(() => this._connect(), this.backoffMs);
    this.backoffMs = Math.min(this.backoffMs * 2, this.maxBackoffMs);
  }

  _startTimers() {
    this._clearTimers();
    this.heartbeatId = setInterval(() => {
      this._send({ op: "pong", ts: Date.now() });
    }, 20000);
    this.staleGuardId = setInterval(() => {
      const staleMs = Date.now() - this.lastMessageAt;
      if (staleMs > 45000 && this.ws) this.ws.close();
    }, 5000);
  }

  _clearTimers() {
    if (this.heartbeatId) clearInterval(this.heartbeatId);
    if (this.staleGuardId) clearInterval(this.staleGuardId);
    this.heartbeatId = null;
    this.staleGuardId = null;
  }

  _send(msg) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    this.ws.send(JSON.stringify(msg));
  }

  async _replayGap() {
    if (this.lastSeq <= 0) return;
    try {
      const replay = await getReplay({ sinceSeq: this.lastSeq + 1, limit: 4000 });
      for (const ev of replay?.events || []) {
        if (typeof ev?.seq === "number") this.lastSeq = Math.max(this.lastSeq, ev.seq);
        this.onEvent?.(ev);
      }
    } catch {
      // Live stream remains primary; replay failures are non-fatal.
    }
  }
}
