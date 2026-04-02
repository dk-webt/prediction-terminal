import EventsPanel from './EventsPanel'
import ResultsPanel from './ResultsPanel'
import DetailPanel from './DetailPanel'
import PositionsPanel from './PositionsPanel'
import { useStore } from '../store'

interface Props {
  runCommand: (cmd: string) => void
}

export default function PanelGrid({ runCommand }: Props) {
  const { activePanel, showPm, showKs, showDetail, showPositions } = useStore()

  // Build dynamic grid-template-columns based on visible panels
  const showLeft = showPm || showKs || showPositions
  const cols: string[] = []
  if (showLeft) cols.push('280px')
  cols.push('1fr')
  if (showDetail) cols.push('300px')

  // Count left panels to determine row split
  const leftPanelCount = (showPositions ? 1 : 0) + (showPm ? 1 : 0) + (showKs ? 1 : 0)
  const rowTemplate = leftPanelCount > 1
    ? Array(leftPanelCount).fill('1fr').join(' ')
    : '1fr'

  const gridStyle = {
    gridTemplateColumns: cols.join(' '),
    gridTemplateRows: rowTemplate,
  }

  // Calculate grid positions dynamically
  const leftCol = 1
  const centerCol = showLeft ? 2 : 1
  const detailCol = showDetail ? (showLeft ? 3 : 2) : -1

  // Assign left panel rows: positions first, then PM, then KS
  let nextRow = 1

  return (
    <div className="panel-grid" style={gridStyle}>
      {showPositions && (
        <PositionsPanel
          style={{ gridColumn: leftCol, gridRow: leftPanelCount > 1 ? nextRow++ : '1 / span 1' }}
        />
      )}
      {showPm && (
        <EventsPanel
          source="PM"
          className="events-panel-dynamic"
          style={{ gridColumn: leftCol, gridRow: leftPanelCount > 1 ? nextRow++ : '1 / span 1' }}
          runCommand={runCommand}
          focused={activePanel === 0}
        />
      )}
      {showKs && (
        <EventsPanel
          source="KS"
          className="events-panel-dynamic"
          style={{ gridColumn: leftCol, gridRow: leftPanelCount > 1 ? nextRow++ : '1 / span 1' }}
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
