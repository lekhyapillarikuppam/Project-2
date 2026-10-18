import { describeDuration, formatDuration } from '../lib/format.js'

const STATUS_LABEL = {
  pending: 'waiting',
  generating: 'narrating',
  ready: 'ready',
  failed: 'failed',
}

// Character voices (SPEC §15.8): chapters whose speakers came from the rule-based fallback (D34).
const ATTRIBUTION_NOTE = {
  baseline: 'Speakers found with the simpler rule-based method',
  mixed: 'Some speakers found with the simpler rule-based method',
}

/** One table-of-contents line: play, number, title …… duration, status, download. */
export default function ChapterRow({ chapter, number, isCurrent, isPlaying, onPlay, downloadHref, downloadName }) {
  const ready = chapter.status === 'ready'
  const failed = chapter.status === 'failed'
  const active = isCurrent && isPlaying
  const label = `${active ? 'Pause' : 'Play'} chapter ${number}: ${chapter.title}`

  return (
    <li className={`chapter-row chapter-row--${chapter.status}${isCurrent ? ' chapter-row--current' : ''}`}>
      <button
        type="button"
        className="icon-button chapter-row__play"
        onClick={() => onPlay(chapter.index)}
        disabled={!ready}
        aria-label={label}
        title={ready ? label : 'Not ready yet'}
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          {active ? <path d="M7 5h3.5v14H7zM13.5 5H17v14h-3.5z" /> : <path d="M8 5.5v13l10.5-6.5z" />}
        </svg>
      </button>

      <div className="chapter-row__main">
        <div className="chapter-row__line">
          <span className="chapter-row__number" aria-hidden="true">
            {number}
          </span>
          <span className="chapter-row__title">
            <span className="visually-hidden">Chapter {number}: </span>
            {chapter.title}
          </span>
          <span className="chapter-row__leader" aria-hidden="true" />
          {ready && chapter.duration_seconds != null && (
            <span className="chapter-row__duration">
              <span aria-hidden="true">{formatDuration(chapter.duration_seconds)}</span>
              <span className="visually-hidden">{describeDuration(chapter.duration_seconds)}</span>
            </span>
          )}
        </div>
        {failed && <p className="chapter-row__error">Narration failed for this chapter</p>}
        {ATTRIBUTION_NOTE[chapter.attribution] && <p className="chapter-row__note">{ATTRIBUTION_NOTE[chapter.attribution]}</p>}
        {isCurrent && !failed && <p className="chapter-row__now">{isPlaying ? 'Now playing' : 'Paused'}</p>}
      </div>

      <span className={`chip chip--${chapter.status}`}>{STATUS_LABEL[chapter.status] ?? chapter.status}</span>

      {ready ? (
        <a
          className="icon-button chapter-row__download"
          href={downloadHref}
          download={downloadName ?? ''}
          aria-label={`Download chapter ${number}: ${chapter.title}`}
          title="Download MP3"
        >
          <svg viewBox="0 0 24 24" aria-hidden="true" className="stroke">
            <path d="M12 4v11m0 0-4.5-4.5M12 15l4.5-4.5M5 19h14" />
          </svg>
        </a>
      ) : (
        <span className="chapter-row__download-placeholder" aria-hidden="true" />
      )}
    </li>
  )
}
