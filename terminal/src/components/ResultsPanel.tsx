import { useRef, useEffect } from 'react'
import { useStore } from '../store'
import type { ArbitrageResult, CompareResult } from '../types'
import type { CenterSnapshot } from '../store'
import BtcPanel from './BtcPanel'

// ── Formatters ────────────────────────────────────────────────────────────────

function fmtPct(v: number) { return `${(v * 100).toFixed(1)}¢` }
function fmtAnn(v: number | null) { return v !== null ? `${(v * 100).toFixed(1)}%` : '—' }
function fmtDays(v: number | null) { return v !== null ? String(v) : '—' }
function fmtScore(v: number) { return v.toFixed(3) }

function scoreClass(v: number) {
  if (v >= 0.92) return 'cell-score-hi'
  if (v >= 0.85) return 'cell-score-mid'
  return 'cell-score-lo'
}

function stripPrefix(question: string, prefix: string) {
  const pre = prefix + ': '
  return question.startsWith(pre) ? question.slice(pre.length) : question
}

function linkClick(e: React.MouseEvent) {
  e.stopPropagation()
}

// ── ARB table ─────────────────────────────────────────────────────────────────

function ArbTable() {
  const { arbResults, selectedIndex, setSelectedIndex, setActivePanel, activePanel } = useStore()
  const selectedRowRef = useRef<HTMLTableRowElement | null>(null)
  useEffect(() => {
    if (activePanel === 2 && selectedIndex !== null && selectedRowRef.current) {
      selectedRowRef.current.scrollIntoView({ block: 'nearest' })
    }
  }, [selectedIndex, activePanel])

  if (!arbResults.length) {
    return (
      <div className="progress-bar" style={{ color: 'var(--amber-dim)' }}>
        No arbitrage results. Run: ARB [limit]
      </div>
    )
  }

  return (
    <table className="result-table">
      <colgroup>
        <col style={{ width: '24%' }} />
        <col style={{ width: '9%' }} />
        <col style={{ width: '24%' }} />
        <col style={{ width: '9%' }} />
        <col style={{ width: '7%' }} />
        <col style={{ width: '7%' }} />
        <col style={{ width: '5%' }} />
        <col style={{ width: '7%' }} />
      </colgroup>
      <thead>
        <tr>
          <th>PM Bracket</th>
          <th>PM Leg</th>
          <th>KS Bracket</th>
          <th>KS Leg</th>
          <th className="right">Spread</th>
          <th className="right">Profit</th>
          <th className="right">Days</th>
          <th className="right">Ann%</th>
        </tr>
      </thead>
      <tbody>
        {arbResults.map((r, i) => {
          const pmLeg =
            r.best_leg === 'pm_yes_ks_no'
              ? `Yes ${fmtPct(r.poly_market.yes_price)}`
              : `No  ${fmtPct(r.poly_market.no_price)}`
          const ksLeg =
            r.best_leg === 'pm_yes_ks_no'
              ? `No  ${fmtPct(r.kalshi_market.no_price)}`
              : `Yes ${fmtPct(r.kalshi_market.yes_price)}`
          const profitCents = r.profit * 100
          const profitCls = profitCents >= 2 ? 'cell-profit' : profitCents >= 0.5 ? '' : ''
          const ksQ = stripPrefix(r.kalshi_market.question, r.kalshi_market.parent_event_title)

          return (
            <tr
              key={i}
              ref={i === selectedIndex ? selectedRowRef : null}
              className={selectedIndex === i ? 'selected' : ''}
              onClick={() => { setActivePanel(2); setSelectedIndex(i) }}
            >
              <td title={r.poly_market.question}>
                <a href={r.poly_market.url} target="_blank" rel="noopener noreferrer" onClick={linkClick}>
                  {r.poly_market.question}
                </a>
              </td>
              <td style={{ whiteSpace: 'pre' }}>{pmLeg}</td>
              <td title={ksQ}>
                <a href={r.kalshi_market.url} target="_blank" rel="noopener noreferrer" onClick={linkClick}>
                  {ksQ}
                </a>
              </td>
              <td style={{ whiteSpace: 'pre' }}>{ksLeg}</td>
              <td className="right">{fmtPct(r.spread)}</td>
              <td className={`right ${profitCls}`}>{fmtPct(r.profit)}</td>
              <td className="right">{fmtDays(r.days_to_resolution)}</td>
              <td className="right">{fmtAnn(r.annualized_return)}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

// ── Compare table ─────────────────────────────────────────────────────────────

function CmpTable() {
  const { compareResults, selectedIndex, setSelectedIndex, setActivePanel, activePanel } = useStore()
  const selectedRowRef = useRef<HTMLTableRowElement | null>(null)
  useEffect(() => {
    if (activePanel === 2 && selectedIndex !== null && selectedRowRef.current) {
      selectedRowRef.current.scrollIntoView({ block: 'nearest' })
    }
  }, [selectedIndex, activePanel])

  if (!compareResults.length) {
    return (
      <div className="progress-bar" style={{ color: 'var(--amber-dim)' }}>
        No comparison results. Run: CMP [limit]
      </div>
    )
  }

  // Flatten into rows: group header + bracket rows
  type Row =
    | { kind: 'header'; result: CompareResult; groupIdx: number }
    | { kind: 'bracket'; result: CompareResult; mmIdx: number; flatIdx: number }

  const rows: Row[] = []
  let flatIdx = 0
  compareResults.forEach((cr, gi) => {
    rows.push({ kind: 'header', result: cr, groupIdx: gi })
    cr.market_matches.forEach((_, mi) => {
      rows.push({ kind: 'bracket', result: cr, mmIdx: mi, flatIdx: flatIdx++ })
    })
  })

  return (
    <table className="result-table">
      <colgroup>
        <col style={{ width: '30%' }} />
        <col style={{ width: '13%' }} />
        <col style={{ width: '8%' }} />
        <col style={{ width: '13%' }} />
        <col style={{ width: '30%' }} />
        <col style={{ width: '6%' }} />
      </colgroup>
      <thead>
        <tr>
          <th>PM Bracket</th>
          <th>PM Price</th>
          <th className="right">Score</th>
          <th>KS Price</th>
          <th>KS Bracket</th>
          <th className="right">Ev%</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row, ri) => {
          if (row.kind === 'header') {
            const em = row.result.event_match
            const label =
              em.poly_event.title === em.kalshi_event.title
                ? em.poly_event.title
                : `${em.poly_event.title}  ↔  ${em.kalshi_event.title}`
            return (
              <tr key={`h-${ri}`} className="group-header-row">
                <td colSpan={5} title={label}>
                  {label}
                </td>
                <td className="right">{fmtScore(em.score)}</td>
              </tr>
            )
          }

          const mm = row.result.market_matches[row.mmIdx]
          const ksQ = stripPrefix(mm.kalshi_market.question, mm.kalshi_market.parent_event_title)
          const pmP = `Y:${fmtPct(mm.poly_market.yes_price)} N:${fmtPct(mm.poly_market.no_price)}`
          const ksP = `Y:${fmtPct(mm.kalshi_market.yes_price)} N:${fmtPct(mm.kalshi_market.no_price)}`
          const fi = row.flatIdx

          return (
            <tr
              key={`b-${ri}`}
              ref={fi === selectedIndex ? selectedRowRef : null}
              className={selectedIndex === fi ? 'selected' : ''}
              onClick={() => { setActivePanel(2); setSelectedIndex(fi) }}
            >
              <td title={mm.poly_market.question}>
                <a href={mm.poly_market.url} target="_blank" rel="noopener noreferrer" onClick={linkClick}>
                  {mm.poly_market.question}
                </a>
              </td>
              <td>{pmP}</td>
              <td className={`right ${scoreClass(mm.score)}`}>{fmtScore(mm.score)}</td>
              <td>{ksP}</td>
              <td title={ksQ}>
                <a href={mm.kalshi_market.url} target="_blank" rel="noopener noreferrer" onClick={linkClick}>
                  {ksQ}
                </a>
              </td>
              <td />
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

// ── Help view ─────────────────────────────────────────────────────────────────

function HelpView() {
  const CMDS = [
    ['PM [N] [ND]', 'Fetch N Polymarket events; ND = max days to expiry (e.g. 30D)'],
    ['KS [N] [ND]', 'Fetch N Kalshi events; ND = max days to expiry'],
    ['ARB [N] [CAT] [ND]', 'Run arbitrage scan; CAT filters by category (e.g. SPORTS); ND = max days'],
    ['CMP [N] [CAT] [ND]', 'Run semantic bracket comparison; tokens in any order'],
    ['HIST', 'Show result history (all past ARB/CMP runs)'],
    ['HIST N', 'Jump to history entry N (e.g. HIST 2)'],
    ['CATS', 'Show available categories for ARB/CMP filtering'],
    ['CACHE', 'Show cache statistics'],
    ['CLEAR', 'Clear the semantic match cache'],
    ['LIMIT N', 'Set default event limit'],
    ['BTC', 'Live BTC 15-min binary options watcher (auto-refreshes)'],
    ['BUY KS/PM YES/UP N P', 'Buy contracts (BUY KS YES 10 0.50, BUY PM UP 5 MKT)'],
    ['SELL KS/PM YES/DOWN N', 'Sell contracts (SELL KS NO 10 0.55)'],
    ['cmd1 | cmd2', 'Pipe: execute multiple orders without Y/N confirmation'],
    ['BTC ATE ON', 'Enable Auto Trade Executor — buys arb combo when profit >= $0.06'],
    ['BTC ATE OFF', 'Disable Auto Trade Executor'],
    ['BTC ATE', 'Show ATE status'],
    ['BTC ORDERS', 'Show live orders panel (active + recent fills)'],
    ['BTC ORDERS CLEAR', 'Clear recent fills from orders panel'],
    ['BTC REFRESH', 'Force re-fetch contracts + reconnect feeds (fallback if auto-roll fails)'],
    ['POS', 'Show current positions on both platforms'],
    ['FUND KS/PM/PCT', 'Set trade funds (FUND KS 50, FUND PM 60, FUND PCT 0.6)'],
    ['SHOW/HIDE/TOGGLE PM|KS|DETAIL', 'Show, hide, or toggle side panels'],
    ['DBG ON/OFF', 'Enable/disable BTC debug logging (DBG to download, DBG CLEAR to reset)'],
    ['R', 'Refresh / re-run last command (fetches fresh data)'],
    ['? / HELP', 'Show this reference'],
    ['Q', 'Quit terminal'],
  ]
  const KEYS = [
    ['/ or :', 'Focus command bar'],
    ['Esc', 'Unfocus command bar'],
    ['Tab', 'Cycle panels (left → center → right)'],
    ['↑ ↓', 'Navigate rows (when not in command bar)'],
    ['Alt+← / Alt+→', 'Navigate center panel history (back / forward)'],
  ]

  return (
    <div className="help-view">
      <h2>PREDICTION MARKET TERMINAL</h2>
      <div style={{ color: 'var(--amber-dim)', marginBottom: 16, fontSize: 11 }}>
        Bloomberg-style arbitrage scanner — Polymarket × Kalshi
      </div>

      <div style={{ color: 'var(--amber)', marginBottom: 6, fontSize: 10, letterSpacing: 1 }}>
        COMMANDS
      </div>
      <table className="cmd-table" style={{ marginBottom: 20 }}>
        <tbody>
          {CMDS.map(([k, v]) => (
            <tr key={k}>
              <td>{k}</td>
              <td>{v}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <div style={{ color: 'var(--amber)', marginBottom: 6, fontSize: 10, letterSpacing: 1 }}>
        KEYBOARD
      </div>
      <table className="cmd-table">
        <tbody>
          {KEYS.map(([k, v]) => (
            <tr key={k}>
              <td>{k}</td>
              <td>{v}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── Cache view ────────────────────────────────────────────────────────────────

function CacheView() {
  const { cacheStats } = useStore()
  if (!cacheStats) {
    return (
      <div className="progress-bar" style={{ color: 'var(--amber-dim)' }}>
        Loading cache stats…
      </div>
    )
  }
  return (
    <div className="cache-view">
      <div><span className="ck-label">Event pairs cached: </span><span className="ck-value">{cacheStats.event_pairs}</span></div>
      <div><span className="ck-label">Market pairs cached:</span><span className="ck-value"> {cacheStats.market_pairs}</span></div>
      <div><span className="ck-label">Oldest entry:       </span><span className="ck-value">{cacheStats.oldest_entry?.slice(0, 19) ?? '—'}</span></div>
      <div><span className="ck-label">Newest entry:       </span><span className="ck-value">{cacheStats.newest_entry?.slice(0, 19) ?? '—'}</span></div>
      <div style={{ marginTop: 12, color: 'var(--gray)', fontSize: 10 }}>DB: {cacheStats.db_path}</div>
    </div>
  )
}

// ── Categories view ───────────────────────────────────────────────────────────

function CatsView() {
  const { categories } = useStore()
  if (!categories) {
    return (
      <div className="progress-bar" style={{ color: 'var(--amber-dim)' }}>
        Loading categories…
      </div>
    )
  }

  const allCats = Array.from(
    new Set([...categories.polymarket, ...categories.kalshi])
  ).sort()

  return (
    <div className="cache-view">
      <div style={{ color: 'var(--amber-dim)', marginBottom: 10, fontSize: 10, letterSpacing: 1 }}>
        AVAILABLE CATEGORIES — use with ARB or CMP e.g. ARB 200 SPORTS
      </div>
      <table className="cmd-table">
        <thead>
          <tr>
            <td style={{ color: 'var(--amber-dim)' }}>CATEGORY</td>
            <td style={{ color: 'var(--amber-dim)' }}>PM</td>
            <td style={{ color: 'var(--amber-dim)' }}>KS</td>
          </tr>
        </thead>
        <tbody>
          {allCats.map((cat) => (
            <tr key={cat}>
              <td>{cat.toUpperCase()}</td>
              <td style={{ color: categories.polymarket.includes(cat) ? 'var(--green)' : 'var(--amber-dim)' }}>
                {categories.polymarket.includes(cat) ? '●' : '○'}
              </td>
              <td style={{ color: categories.kalshi.includes(cat) ? 'var(--green)' : 'var(--amber-dim)' }}>
                {categories.kalshi.includes(cat) ? '●' : '○'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── History view ──────────────────────────────────────────────────────────────

function HistView() {
  const { centerHistory, centerHistoryIndex } = useStore()

  if (centerHistory.length === 0) {
    return (
      <div className="progress-bar" style={{ color: 'var(--amber-dim)' }}>
        No history yet. Run ARB or CMP to build history.
      </div>
    )
  }

  return (
    <div className="cache-view">
      <div style={{ color: 'var(--amber-dim)', marginBottom: 10, fontSize: 10, letterSpacing: 1 }}>
        RESULT HISTORY — type HIST N to jump  •  Alt+← / Alt+→ to step through
      </div>
      <table className="cmd-table">
        <thead>
          <tr>
            <td style={{ color: 'var(--amber-dim)', width: 32 }}>#</td>
            <td style={{ color: 'var(--amber-dim)' }}>COMMAND</td>
            <td style={{ color: 'var(--amber-dim)' }}>TIME</td>
            <td style={{ color: 'var(--amber-dim)' }}>RESULTS</td>
          </tr>
        </thead>
        <tbody>
          {centerHistory.map((snap: CenterSnapshot, i: number) => {
            const isCurrent = i === centerHistoryIndex
            const timeStr = new Date(snap.timestamp).toLocaleTimeString('en-US', { hour12: false })
            const resultLabel = snap.view === 'ARB'
              ? `${snap.resultCount} arb${snap.resultCount !== 1 ? 's' : ''}`
              : `${snap.resultCount} pair${snap.resultCount !== 1 ? 's' : ''}`
            return (
              <tr key={i} style={{ color: isCurrent ? 'var(--amber-hi)' : 'var(--amber)' }}>
                <td>{isCurrent ? '▶' : ' '} {i + 1}</td>
                <td>{snap.label}</td>
                <td>{timeStr}</td>
                <td>{resultLabel}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ── Main ResultsPanel ─────────────────────────────────────────────────────────

const VIEW_LABELS: Record<string, string> = {
  IDLE: 'TERMINAL',
  ARB: 'ARBITRAGE',
  CMP: 'COMPARISON',
  HELP: 'HELP',
  CACHE: 'CACHE',
  BTC: 'BTC 15-MIN OPTIONS',
  CATS: 'CATEGORIES',
  HIST: 'HISTORY',
}

export default function ResultsPanel({ focused, style }: { focused: boolean; style?: React.CSSProperties }) {
  const { centerView, activeCategory, loading, progressMsg, errorMsg, arbResults, compareResults } = useStore()

  const count =
    centerView === 'ARB'
      ? arbResults.length
      : centerView === 'CMP'
      ? compareResults.reduce((s, cr) => s + cr.market_matches.length, 0)
      : 0

  const baseLabel = VIEW_LABELS[centerView] ?? centerView
  const panelTitle =
    activeCategory && (centerView === 'ARB' || centerView === 'CMP')
      ? `${baseLabel} • ${activeCategory}`
      : baseLabel

  return (
    <div className={`panel results-panel${focused ? ' focused' : ''}`} style={style}>
      <div className="panel-header">
        <span className="panel-title">{panelTitle}</span>
        {count > 0 && <span className="panel-count">{count}</span>}
      </div>

      <div className="panel-body">
        {loading && (
          <div className="progress-bar">
            <span className="spinner">◐</span>
            {progressMsg || 'Working…'}
          </div>
        )}

        {!loading && errorMsg && (
          <div className="error-msg">{errorMsg}</div>
        )}

        {!loading && centerView === 'IDLE' && (
          <div className="idle-state">
            <div className="idle-logo">PMT</div>
            <div className="idle-hint">Type ? for commands or ARB / CMP to start</div>
          </div>
        )}

        {!loading && centerView === 'ARB' && <ArbTable />}
        {!loading && centerView === 'CMP' && <CmpTable />}
        {!loading && centerView === 'HELP' && <HelpView />}
        {!loading && centerView === 'CACHE' && <CacheView />}
        {!loading && centerView === 'CATS' && <CatsView />}
        {!loading && centerView === 'HIST' && <HistView />}
        {centerView === 'BTC' && <BtcPanel />}
      </div>
    </div>
  )
}
