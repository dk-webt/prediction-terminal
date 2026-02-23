import { useRef, useState, KeyboardEvent, RefObject } from 'react'

interface Props {
  runCommand: (cmd: string) => void
  inputRef: RefObject<HTMLInputElement | null>
}

const HINTS = [
  ['PM [N]', 'Polymarket'],
  ['KS [N]', 'Kalshi'],
  ['ARB [N]', 'Arbitrage'],
  ['CMP [N]', 'Compare'],
  ['CACHE', 'Stats'],
  ['CLEAR', 'Reset cache'],
  ['R', 'Refresh'],
  ['?', 'Help'],
  ['Q', 'Quit'],
]

export default function CommandBar({ runCommand, inputRef }: Props) {
  const [value, setValue] = useState('')
  const historyRef = useRef<string[]>([])
  const histPosRef = useRef<number>(-1)
  const savedInputRef = useRef<string>('')

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      const trimmed = value.trim()
      if (trimmed) {
        historyRef.current = [trimmed, ...historyRef.current.slice(0, 49)]
        histPosRef.current = -1
        savedInputRef.current = ''
        runCommand(trimmed)
        setValue('')
      }
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      const hist = historyRef.current
      if (!hist.length) return
      if (histPosRef.current === -1) {
        savedInputRef.current = value
      }
      const next = Math.min(histPosRef.current + 1, hist.length - 1)
      histPosRef.current = next
      setValue(hist[next])
    } else if (e.key === 'ArrowDown') {
      e.preventDefault()
      if (histPosRef.current === -1) return
      const next = histPosRef.current - 1
      histPosRef.current = next
      setValue(next === -1 ? savedInputRef.current : historyRef.current[next])
    } else if (e.key === 'Escape') {
      histPosRef.current = -1
      ;(e.target as HTMLInputElement).blur()
    }
  }

  return (
    <div className="command-bar">
      <div className="command-row">
        <span className="cmd-prompt">CMD ▶</span>
        <input
          ref={inputRef as RefObject<HTMLInputElement>}
          className="cmd-input"
          type="text"
          value={value}
          onChange={(e) => {
            setValue(e.target.value)
            histPosRef.current = -1
          }}
          onKeyDown={onKeyDown}
          placeholder="type a command…"
          spellCheck={false}
          autoComplete="off"
          autoCorrect="off"
          autoCapitalize="off"
        />
      </div>
      <div className="cmd-hints">
        {HINTS.map(([key, desc]) => (
          <span key={key} title={desc}>
            <span className="cmd-hint-key">{key}</span>
          </span>
        ))}
        <span>│</span>
        <span className="cmd-hint-key">/ or :</span> focus
        <span className="cmd-hint-key" style={{ marginLeft: 8 }}>↑↓</span> history/nav
        <span className="cmd-hint-key" style={{ marginLeft: 8 }}>Tab</span> panels
      </div>
    </div>
  )
}
