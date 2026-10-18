import { useEffect, useId, useRef, useState } from 'react'
import { SKIP_SECONDS } from '../hooks/useAudioPlayer.js'
import { formatDuration } from '../lib/format.js'
import SpeedControl from './SpeedControl.jsx'
import '../styles/PlayerBar.css'

const SHORTCUTS = [
  ['Space', 'Play / pause'],
  ['←  →', `Back / forward ${SKIP_SECONDS} s`],
  ['Shift + ←  →', 'Previous / next chapter'],
]

function KeyboardHelp() {
  const [open, setOpen] = useState(false)
  const id = useId()
  const wrapRef = useRef(null)

  useEffect(() => {
    if (!open) return undefined
    const onKey = (event) => event.key === 'Escape' && setOpen(false)
    const onClick = (event) => !wrapRef.current?.contains(event.target) && setOpen(false)
    document.addEventListener('keydown', onKey)
    document.addEventListener('mousedown', onClick)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('mousedown', onClick)
    }
  }, [open])

  return (
    <div className="kbd-help" ref={wrapRef}>
      <button
        type="button"
        className="icon-button icon-button--quiet"
        aria-expanded={open}
        aria-controls={id}
        aria-label="Keyboard shortcuts"
        title="Keyboard shortcuts"
        onClick={() => setOpen((o) => !o)}
      >
        ?
      </button>
      <div id={id} className="kbd-help__panel" role="note" hidden={!open}>
        <p className="kbd-help__title">Keyboard shortcuts</p>
        <dl>
          {SHORTCUTS.map(([keys, action]) => (
            <div key={keys} className="kbd-help__row">
              <dt>
                <kbd>{keys}</kbd>
              </dt>
              <dd>{action}</dd>
            </div>
          ))}
        </dl>
      </div>
    </div>
  )
}

/** Sticky player at the bottom of the book page. */
export default function PlayerBar({ player, chapterNumber }) {
  const { currentChapter, playing, time, duration, rate, waitingFor, buffering } = player
  const hasChapter = currentChapter != null
  const max = duration > 0 ? duration : 0
  const playLabel = playing ? 'Pause' : 'Play'

  const title = hasChapter ? currentChapter.title : 'Not playing'
  let subtitle = hasChapter ? `Chapter ${chapterNumber(currentChapter.index)}` : 'Press play or pick a chapter'
  if (waitingFor != null) subtitle = `Waiting for chapter ${chapterNumber(waitingFor)}…`
  else if (buffering && playing) subtitle = `${subtitle} · loading…`

  return (
    <section className="player-bar" aria-label="Audio player">
      <div className="player-bar__inner">
        <div className="player-bar__now">
          <p className="player-bar__title" title={title}>
            {title}
          </p>
          <p className="player-bar__subtitle" aria-live="polite">
            {subtitle}
          </p>
        </div>

        <div className="player-bar__controls">
          <button
            type="button"
            className="icon-button"
            onClick={player.prev}
            disabled={!player.hasPrev}
            aria-label="Previous chapter"
            title="Previous chapter (Shift+←)"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M6 5h2v14H6zM19 5.5v13L9.5 12z" />
            </svg>
          </button>
          <button
            type="button"
            className="icon-button player-bar__skip"
            onClick={() => player.skip(-SKIP_SECONDS)}
            disabled={!hasChapter}
            aria-label={`Back ${SKIP_SECONDS} seconds`}
            title={`Back ${SKIP_SECONDS} s (←)`}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true" className="stroke">
              <path d="M4.5 12a7.5 7.5 0 1 0 2.2-5.3M4.5 4v4h4" />
              <text x="12" y="15.5" textAnchor="middle">15</text>
            </svg>
          </button>
          <button
            type="button"
            className="icon-button icon-button--primary"
            onClick={player.toggle}
            aria-label={playLabel}
            title={`${playLabel} (Space)`}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              {playing ? <path d="M7 5h3.5v14H7zM13.5 5H17v14h-3.5z" /> : <path d="M8 5.5v13l10.5-6.5z" />}
            </svg>
          </button>
          <button
            type="button"
            className="icon-button player-bar__skip"
            onClick={() => player.skip(SKIP_SECONDS)}
            disabled={!hasChapter}
            aria-label={`Forward ${SKIP_SECONDS} seconds`}
            title={`Forward ${SKIP_SECONDS} s (→)`}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true" className="stroke">
              <path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3M19.5 4v4h-4" />
              <text x="12" y="15.5" textAnchor="middle">15</text>
            </svg>
          </button>
          <button
            type="button"
            className="icon-button"
            onClick={player.next}
            disabled={!player.hasNext}
            aria-label="Next chapter"
            title="Next chapter (Shift+→)"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M16 5h2v14h-2zM5 5.5v13l9.5-6.5z" />
            </svg>
          </button>
        </div>

        <div className="player-bar__seek">
          <span className="player-bar__time">{formatDuration(time)}</span>
          <input
            type="range"
            className="seek"
            min={0}
            max={max || 1}
            step={1}
            value={Math.min(time, max || 1)}
            disabled={!hasChapter || !max}
            onChange={(event) => player.seek(Number(event.target.value))}
            aria-label="Seek"
            aria-valuetext={`${formatDuration(time)} of ${formatDuration(max)}`}
            style={{ '--seek-fill': max ? `${(100 * Math.min(time, max)) / max}%` : '0%' }}
          />
          <span className="player-bar__time">{formatDuration(max)}</span>
        </div>

        <div className="player-bar__extra">
          <SpeedControl value={rate} onChange={player.setRate} />
          <KeyboardHelp />
        </div>
      </div>
    </section>
  )
}
