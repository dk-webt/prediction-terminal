import EventsPanel from './EventsPanel'
import ResultsPanel from './ResultsPanel'
import DetailPanel from './DetailPanel'
import { useStore } from '../store'

interface Props {
  runCommand: (cmd: string) => void
}

export default function PanelGrid({ runCommand }: Props) {
  const { activePanel, showPm, showKs, showDetail } = useStore()

  // Build dynamic grid-template-columns based on visible panels
  const showLeft = showPm || showKs
  const cols: string[] = []
  if (showLeft) cols.push('280px')
  cols.push('1fr')
  if (showDetail) cols.push('300px')

  // If left panels hidden, results takes column 1; if detail hidden, results stretches
  const gridStyle = {
    gridTemplateColumns: cols.join(' '),
    gridTemplateRows: showPm && showKs ? '1fr 1fr' : '1fr',
  }

  // Calculate grid positions dynamically
  const leftCol = 1
  const centerCol = showLeft ? 2 : 1
  const detailCol = showDetail ? (showLeft ? 3 : 2) : -1

  return (
    <div className="panel-grid" style={gridStyle}>
      {showPm && (
        <EventsPanel
          source="PM"
          className="events-panel-dynamic"
          style={{ gridColumn: leftCol, gridRow: showKs ? 1 : '1 / span 1' }}
          runCommand={runCommand}
          focused={activePanel === 0}
        />
      )}
      {showKs && (
        <EventsPanel
          source="KS"
          className="events-panel-dynamic"
          style={{ gridColumn: leftCol, gridRow: showPm ? 2 : '1 / span 1' }}
          runCommand={runCommand}
          focused={activePanel === 1}
        />
      )}
      <ResultsPanel
        focused={activePanel === 2}
        style={{ gridColumn: centerCol, gridRow: '1 / -1' }}
      />
      {showDetail && (
        <DetailPanel
          focused={activePanel === 3}
          style={{ gridColumn: detailCol, gridRow: '1 / -1' }}
        />
      )}
    </div>
  )
}
