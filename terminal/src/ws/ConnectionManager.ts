/**
 * ConnectionManager — manages 3 independent WebSocket connections:
 *   /ws/cmd   — ARB, CMP, CACHE commands (request/response)
 *   /ws/btc   — BTC price streaming (server push, high frequency)
 *   /ws/trade — Order confirmation + execution (stateful)
 *
 * Each socket reconnects independently. BTC auto-resubscribes on reconnect.
 */

export type SocketStatus = 'connecting' | 'connected' | 'disconnected'

export interface ConnectionCallbacks {
  onCmdMessage: (msg: Record<string, unknown>) => void
  onBtcMessage: (msg: Record<string, unknown>) => void
  onTradeMessage: (msg: Record<string, unknown>) => void
  onStatusChange: (socket: 'cmd' | 'btc' | 'trade', status: SocketStatus) => void
}

const RECONNECT_DELAY = 2000

export class ConnectionManager {
  private baseUrl: string
  private cmdWs: WebSocket | null = null
  private btcWs: WebSocket | null = null
  private tradeWs: WebSocket | null = null
  private callbacks: ConnectionCallbacks
  private destroyed = false

  // Track whether BTC should auto-resubscribe on reconnect
  private btcSubscribed = false

  constructor(baseUrl: string, callbacks: ConnectionCallbacks) {
    // Convert http://host:port to ws://host:port
    this.baseUrl = baseUrl.replace(/^http/, 'ws')
    this.callbacks = callbacks
  }

  /** Connect all 3 sockets */
  connect() {
    this.destroyed = false
    this.connectCmd()
    this.connectBtc()
    this.connectTrade()
  }

  /** Disconnect all sockets */
  disconnect() {
    this.destroyed = true
    this.btcSubscribed = false
    this.cmdWs?.close()
    this.btcWs?.close()
    this.tradeWs?.close()
    this.cmdWs = null
    this.btcWs = null
    this.tradeWs = null
  }

  // ── Socket accessors ────────────────────────────────────────────────────────

  get cmdReady(): boolean {
    return this.cmdWs?.readyState === WebSocket.OPEN
  }

  get btcReady(): boolean {
    return this.btcWs?.readyState === WebSocket.OPEN
  }

  get tradeReady(): boolean {
    return this.tradeWs?.readyState === WebSocket.OPEN
  }

  // ── Send methods ────────────────────────────────────────────────────────────

  sendCmd(data: Record<string, unknown>) {
    if (this.cmdReady) {
      this.cmdWs!.send(JSON.stringify(data))
    }
  }

  sendBtc(data: Record<string, unknown>) {
    if (this.btcReady) {
      this.btcWs!.send(JSON.stringify(data))
    }
  }

  sendTrade(data: Record<string, unknown>) {
    if (this.tradeReady) {
      this.tradeWs!.send(JSON.stringify(data))
    }
  }

  // ── BTC subscription management ────────────────────────────────────────────

  subscribeBtc() {
    this.btcSubscribed = true
    this.sendBtc({ type: 'btc', action: 'subscribe' })
  }

  unsubscribeBtc() {
    this.btcSubscribed = false
    this.sendBtc({ type: 'btc', action: 'unsubscribe' })
  }

  get isBtcSubscribed(): boolean {
    return this.btcSubscribed
  }

  // ── Individual socket connections ──────────────────────────────────────────

  private connectCmd() {
    if (this.destroyed) return
    this.callbacks.onStatusChange('cmd', 'connecting')

    const ws = new WebSocket(`${this.baseUrl}/ws/cmd`)

    ws.onopen = () => {
      this.callbacks.onStatusChange('cmd', 'connected')
    }

    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data as string)
        this.callbacks.onCmdMessage(msg)
      } catch { /* ignore parse errors */ }
    }

    ws.onclose = () => {
      this.callbacks.onStatusChange('cmd', 'disconnected')
      this.cmdWs = null
      if (!this.destroyed) {
        setTimeout(() => this.connectCmd(), RECONNECT_DELAY)
      }
    }

    ws.onerror = () => {
      // onclose will fire after onerror
    }

    this.cmdWs = ws
  }

  private connectBtc() {
    if (this.destroyed) return
    this.callbacks.onStatusChange('btc', 'connecting')

    const ws = new WebSocket(`${this.baseUrl}/ws/btc`)

    ws.onopen = () => {
      this.callbacks.onStatusChange('btc', 'connected')
      // Auto-resubscribe if BTC was active before disconnect
      if (this.btcSubscribed) {
        ws.send(JSON.stringify({ type: 'btc', action: 'subscribe' }))
      }
    }

    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data as string)
        this.callbacks.onBtcMessage(msg)
      } catch { /* ignore parse errors */ }
    }

    ws.onclose = () => {
      this.callbacks.onStatusChange('btc', 'disconnected')
      this.btcWs = null
      if (!this.destroyed) {
        setTimeout(() => this.connectBtc(), RECONNECT_DELAY)
      }
    }

    ws.onerror = () => {}

    this.btcWs = ws
  }

  private connectTrade() {
    if (this.destroyed) return
    this.callbacks.onStatusChange('trade', 'connecting')

    const ws = new WebSocket(`${this.baseUrl}/ws/trade`)

    ws.onopen = () => {
      this.callbacks.onStatusChange('trade', 'connected')
    }

    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data as string)
        this.callbacks.onTradeMessage(msg)
      } catch { /* ignore parse errors */ }
    }

    ws.onclose = () => {
      this.callbacks.onStatusChange('trade', 'disconnected')
      this.tradeWs = null
      if (!this.destroyed) {
        setTimeout(() => this.connectTrade(), RECONNECT_DELAY)
      }
    }

    ws.onerror = () => {}

    this.tradeWs = ws
  }
}
