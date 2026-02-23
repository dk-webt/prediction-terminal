import { useStore } from '../store'
import type { ArbitrageResult, CompareResult } from '../types'

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

function openUrl(url: string, e: React.MouseEvent) {
  e.stopPropagation()
  if (url) window.open(url, '_blank')
}

// ── ARB table ─────────────────────────────────────────────────────────────────

function ArbTable() {
  const { arbResults, selectedIndex, setSelectedIndex } = useStore()

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
              className={selectedIndex === i ? 'selected' : ''}
              onClick={() => setSelectedIndex(i)}
            >
              <td title={r.poly_market.question}>
                <a href="#" onClick={(e) => openUrl(r.poly_market.url, e)}>
                  {r.poly_market.question}
                </a>
              </td>
              <td style={{ whiteSpace: 'pre' }}>{pmLeg}</td>
              <td title={ksQ}>
                <a href="#" onClick={(e) => openUrl(r.kalshi_market.url, e)}>
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
  const { compareResults, selectedIndex, setSelectedIndex } = useStore()

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
              className={selectedIndex === fi ? 'selected' : ''}
              onClick={() => setSelectedIndex(fi)}
            >
              <td title={mm.poly_market.question}>
                <a href="#" onClick={(e) => openUrl(mm.poly_market.url, e)}>
                  {mm.poly_market.question}
                </a>
              </td>
              <td>{pmP}</td>
              <td className={`right ${scoreClass(mm.score)}`}>{fmtScore(mm.score)}</td>
              <td>{ksP}</td>
              <td title={ksQ}>
                <a href="#" onClick={(e) => openUrl(mm.kalshi_market.url, e)}>
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
    ['PM [N]', 'Fetch N Polymarket events (default: LIMIT)'],
    ['KS [N]', 'Fetch N Kalshi events'],
    ['ARB [N]', 'Run arbitrage scan across N events per platform'],
    ['CMP [N]', 'Run semantic bracket comparison'],
    ['CACHE', 'Show cache statistics'],
    ['CLEAR', 'Clear the semantic match cache'],
    ['LIMIT N', 'Set default event limit'],
    ['R', 'Refresh / re-run last command'],
    ['? / HELP', 'Show this reference'],
    ['Q', 'Quit terminal'],
  ]
  const KEYS = [
    ['/ or :', 'Focus command bar'],
    ['Esc', 'Unfocus command bar'],
    ['Tab', 'Cycle panels (left → center → right)'],
    ['↑ ↓', 'Navigate rows (when not in command bar)'],
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

// ── Main ResultsPanel ─────────────────────────────────────────────────────────

const VIEW_LABELS: Record<string, string> = {
  IDLE: 'TERMINAL',
  PM: 'POLYMARKET EVENTS',
  KS: 'KALSHI EVENTS',
  ARB: 'ARBITRAGE',
  CMP: 'COMPARISON',
  HELP: 'HELP',
  CACHE: 'CACHE',
}

export default function ResultsPanel({ focused }: { focused: boolean }) {
  const { activeView, loading, progressMsg, errorMsg, arbResults, compareResults } = useStore()

  const count =
    activeView === 'ARB'
      ? arbResults.length
      : activeView === 'CMP'
      ? compareResults.reduce((s, cr) => s + cr.market_matches.length, 0)
      : 0

  return (
    <div className={`panel results-panel${focused ? ' focused' : ''}`}>
      <div className="panel-header">
        <span className="panel-title">{VIEW_LABELS[activeView] ?? activeView}</span>
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

        {!loading && activeView === 'IDLE' && (
          <div className="idle-state">
            <div className="idle-logo">PMT</div>
            <div className="idle-hint">Type ? for commands or PM / KS / ARB / CMP to start</div>
          </div>
        )}

        {!loading && activeView === 'ARB' && <ArbTable />}
        {!loading && activeView === 'CMP' && <CmpTable />}
        {!loading && activeView === 'HELP' && <HelpView />}
        {!loading && activeView === 'CACHE' && <CacheView />}

        {!loading && (activeView === 'PM' || activeView === 'KS') && (
          <div className="idle-state">
            <div className="idle-hint" style={{ textAlign: 'center' }}>
              {activeView} events shown in left panel.
              <br />
              Select a row to see details.
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
