import EventsPanel from './EventsPanel'
import ResultsPanel from './ResultsPanel'
import DetailPanel from './DetailPanel'
import { useStore } from '../store'

interface Props {
  runCommand: (cmd: string) => void
}

export default function PanelGrid({ runCommand }: Props) {
  const { activePanel } = useStore()

  return (
    <div className="panel-grid">
      <EventsPanel source="PM" className="events-panel-pm" runCommand={runCommand} focused={activePanel === 0} />
      <EventsPanel source="KS" className="events-panel-ks" runCommand={runCommand} focused={activePanel === 1} />
      <ResultsPanel focused={activePanel === 2} />
      <DetailPanel focused={activePanel === 3} />
    </div>
  )
}
