import EventsPanel from './EventsPanel'
import ResultsPanel from './ResultsPanel'
import DetailPanel from './DetailPanel'

interface Props {
  runCommand: (cmd: string) => void
}

export default function PanelGrid({ runCommand }: Props) {
  return (
    <div className="panel-grid">
      <EventsPanel source="PM" className="events-panel-pm" runCommand={runCommand} />
      <EventsPanel source="KS" className="events-panel-ks" runCommand={runCommand} />
      <ResultsPanel />
      <DetailPanel />
    </div>
  )
}
