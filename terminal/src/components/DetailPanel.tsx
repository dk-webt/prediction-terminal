import { useStore } from '../store'
import type { ArbitrageResult, CompareResult, NormalizedEvent, MarketMatchResult } from '../types'

function fmtPct(v: number) { return `${(v * 100).toFixed(1)}¢` }
function fmtAnn(v: number | null) { return v !== null ? `${(v * 100).toFixed(1)}%` : '—' }
function fmtVol(v: number) {
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`
  if (v >= 1_000) return `$${(v / 1_000).toFixed(1)}K`
  return `$${v.toFixed(0)}`
}

function stripPrefix(question: string, prefix: string) {
  const pre = prefix + ': '
  return question.startsWith(pre) ? question.slice(pre.length) : question
}

function openUrl(url: string, e: React.MouseEvent) {
  e.preventDefault()
  if (url) window.open(url, '_blank')
}

function Label({ children }: { children: React.ReactNode }) {
  return <div className="detail-label">{children}</div>
}

function Val({ children, className }: { children: React.ReactNode; className?: string }) {
  return <div className={`detail-value ${className ?? ''}`}>{children}</div>
}

// ── ARB detail ────────────────────────────────────────────────────────────────

function ArbDetail({ r }: { r: ArbitrageResult }) {
  const pmLegLabel = r.best_leg === 'pm_yes_ks_no' ? 'Yes' : 'No'
  const ksLegLabel = r.best_leg === 'pm_yes_ks_no' ? 'No' : 'Yes'
  const pmLegPrice =
    r.best_leg === 'pm_yes_ks_no' ? r.poly_market.yes_price : r.poly_market.no_price
  const ksLegPrice =
    r.best_leg === 'pm_yes_ks_no' ? r.kalshi_market.no_price : r.kalshi_market.yes_price
  const ksQ = stripPrefix(r.kalshi_market.question, r.kalshi_market.parent_event_title)
  const profitCents = r.profit * 100
  const profitCls = profitCents >= 2 ? 'detail-profit' : ''

  return (
    <div className="detail-body">
      <Label>POLYMARKET</Label>
      <Val>
        <a className="detail-link" href="#" onClick={(e) => openUrl(r.poly_market.url, e)}>
          {r.poly_market.question}
        </a>
      </Val>

      <Label>KALSHI</Label>
      <Val>
        <a className="detail-link" href="#" onClick={(e) => openUrl(r.kalshi_market.url, e)}>
          {ksQ}
        </a>
      </Val>

      <Label>MATCH SCORE</Label>
      <Val>{r.match_score.toFixed(4)}</Val>

      <Label>PRICES</Label>
      <div style={{ fontSize: 11, lineHeight: 1.8, color: 'var(--amber)' }}>
        <div>PM Yes: {fmtPct(r.poly_market.yes_price)}  No: {fmtPct(r.poly_market.no_price)}</div>
        <div>KS Yes: {fmtPct(r.kalshi_market.yes_price)}  No: {fmtPct(r.kalshi_market.no_price)}</div>
      </div>

      <Label>BEST LEG</Label>
      <Val>Buy PM {pmLegLabel} @ {fmtPct(pmLegPrice)}</Val>
      <Val>Buy KS {ksLegLabel} @ {fmtPct(ksLegPrice)}</Val>

      <Label>SPREAD / PROFIT</Label>
      <Val>{fmtPct(r.spread)} total cost</Val>
      <Val className={profitCls}>{fmtPct(r.profit)} profit ({profitCents.toFixed(2)}¢)</Val>

      <Label>TIME TO RESOLUTION</Label>
      <Val>{r.days_to_resolution !== null ? `${r.days_to_resolution} days` : 'unknown'}</Val>

      <Label>ANNUALIZED RETURN</Label>
      <Val className={profitCls}>{fmtAnn(r.annualized_return)}</Val>

      {r.poly_market.close_time && (
        <>
          <Label>PM CLOSES</Label>
          <Val>{r.poly_market.close_time.slice(0, 10)}</Val>
        </>
      )}
      {r.kalshi_market.close_time && (
        <>
          <Label>KS CLOSES</Label>
          <Val>{r.kalshi_market.close_time.slice(0, 10)}</Val>
        </>
      )}
    </div>
  )
}

// ── Compare detail ────────────────────────────────────────────────────────────

function CmpDetail({ mm }: { mm: MarketMatchResult }) {
  const ksQ = stripPrefix(mm.kalshi_market.question, mm.kalshi_market.parent_event_title)
  return (
    <div className="detail-body">
      <Label>POLYMARKET</Label>
      <Val>
        <a className="detail-link" href="#" onClick={(e) => openUrl(mm.poly_market.url, e)}>
          {mm.poly_market.question}
        </a>
      </Val>

      <Label>KALSHI</Label>
      <Val>
        <a className="detail-link" href="#" onClick={(e) => openUrl(mm.kalshi_market.url, e)}>
          {ksQ}
        </a>
      </Val>

      <Label>MATCH SCORE</Label>
      <Val>{mm.score.toFixed(4)}</Val>

      <Label>PM PRICES</Label>
      <Val>Yes {fmtPct(mm.poly_market.yes_price)}  /  No {fmtPct(mm.poly_market.no_price)}</Val>

      <Label>KS PRICES</Label>
      <Val>Yes {fmtPct(mm.kalshi_market.yes_price)}  /  No {fmtPct(mm.kalshi_market.no_price)}</Val>

      <Label>PM VOLUME</Label>
      <Val>{fmtVol(mm.poly_market.volume)}</Val>
    </div>
  )
}

// ── Event detail ──────────────────────────────────────────────────────────────

function EventDetail({ ev }: { ev: NormalizedEvent }) {
  return (
    <div className="detail-body">
      <Label>{ev.source.toUpperCase()} EVENT</Label>
      <Val>
        <a className="detail-link" href="#" onClick={(e) => openUrl(ev.url, e)}>
          {ev.title}
        </a>
      </Val>

      <Label>CATEGORY</Label>
      <Val>{ev.category || '—'}</Val>

      <Label>VOLUME</Label>
      <Val>{fmtVol(ev.volume)}</Val>

      <Label>END DATE</Label>
      <Val>{ev.end_date || '—'}</Val>

      <Label>MARKETS ({ev.markets.length})</Label>
      {ev.markets.slice(0, 6).map((m, i) => (
        <div key={i} style={{ fontSize: 11, color: 'var(--amber)', marginBottom: 3 }}>
          <div style={{ color: 'var(--amber-dim)', fontSize: 10 }}>{m.question}</div>
          <div>Y:{fmtPct(m.yes_price)} N:{fmtPct(m.no_price)} {fmtVol(m.volume)}</div>
        </div>
      ))}
      {ev.markets.length > 6 && (
        <div style={{ color: 'var(--gray)', fontSize: 10 }}>…and {ev.markets.length - 6} more</div>
      )}
    </div>
  )
}

// ── Main ──────────────────────────────────────────────────────────────────────

export default function DetailPanel({ focused }: { focused: boolean }) {
  const { activeView, selectedIndex, arbResults, compareResults, pmEvents, ksEvents } = useStore()

  let content: React.ReactNode = null

  if (selectedIndex !== null) {
    if (activeView === 'ARB' && arbResults[selectedIndex]) {
      content = <ArbDetail r={arbResults[selectedIndex]} />
    } else if (activeView === 'CMP') {
      // Find the flat bracket index across all compare groups
      let fi = 0
      let found = false
      for (const cr of compareResults) {
        for (let mi = 0; mi < cr.market_matches.length; mi++) {
          if (fi === selectedIndex) {
            content = <CmpDetail mm={cr.market_matches[mi]} />
            found = true
            break
          }
          fi++
        }
        if (found) break
      }
    } else if (activeView === 'PM' && pmEvents[selectedIndex]) {
      content = <EventDetail ev={pmEvents[selectedIndex]} />
    } else if (activeView === 'KS' && ksEvents[selectedIndex]) {
      content = <EventDetail ev={ksEvents[selectedIndex]} />
    }
  }

  return (
    <div className={`panel detail-panel${focused ? ' focused' : ''}`}>
      <div className="panel-header">
        <span className="panel-title">DETAIL</span>
      </div>
      <div className="panel-body">
        {content ?? (
          <div className="idle-state" style={{ fontSize: 10, color: 'var(--gray)' }}>
            Select a row
          </div>
        )}
      </div>
    </div>
  )
}
