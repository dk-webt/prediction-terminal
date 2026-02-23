import EventsPanel from './EventsPanel'
import ResultsPanel from './ResultsPanel'
import DetailPanel from './DetailPanel'
import { useStore } from '../store'

interface Props {
  runCommand: (cmd: string) => void
}

export default function PanelGrid({ runCommand }: Props) {
  const { activePanel, activeView } = useStore()

  // On the left column, highlight KS panel when activeView is KS, otherwise PM panel
  const ksFocused = activePanel === 0 && activeView === 'KS'
  const pmFocused = activePanel === 0 && !ksFocused

  return (
    <div className="panel-grid">
      <EventsPanel source="PM" className="events-panel-pm" runCommand={runCommand} focused={pmFocused} />
      <EventsPanel source="KS" className="events-panel-ks" runCommand={runCommand} focused={ksFocused} />
      <ResultsPanel focused={activePanel === 1} />
      <DetailPanel focused={activePanel === 2} />
    </div>
  )
}
