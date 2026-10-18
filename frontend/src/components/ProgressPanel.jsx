import { formatEta } from '../lib/format.js'
import '../styles/ProgressPanel.css'

/** The sentence describing what the server is doing now. */
function statusText(job) {
  const total = job.chapters?.length ?? 0
  switch (job.status) {
    case 'queued':
      return 'Waiting for the server to start on your document…'
    case 'extracting':
      return 'Reading your document and finding chapters…'
    case 'synthesizing': {
      const position = job.chapters.findIndex((c) => c.status === 'generating')
      const n = position >= 0 ? position + 1 : Math.min((job.progress?.chapters_done ?? 0) + 1, total)
      return total ? `Narrating chapter ${n} of ${total}…` : 'Narrating…'
    }
    default:
      return ''
  }
}

/** Progress while the job is queued, extracting or synthesizing. */
export default function ProgressPanel({ job }) {
  const { chars_done: done = 0, chars_total: total = 0, eta_seconds: eta, chapters_done: chaptersDone = 0 } =
    job.progress ?? {}
  const narrating = job.status === 'synthesizing' && total > 0
  const percent = narrating ? Math.min(100, Math.round((100 * done) / total)) : 0
  const etaText = narrating ? formatEta(eta) : null
  const text = statusText(job)

  return (
    <section className="progress-panel" aria-label="Progress">
      {/* Announced when the sentence changes (status or chapter), not on every percent. */}
      <p className="progress-panel__status" aria-live="polite">
        {text}
      </p>
      <div
        className={`progress-bar${narrating ? '' : ' progress-bar--indeterminate'}`}
        role="progressbar"
        aria-label="Narration progress"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={narrating ? percent : undefined}
        aria-valuetext={narrating ? `${percent}% of the text narrated` : 'Getting started'}
      >
        <div className="progress-bar__fill" style={narrating ? { width: `${percent}%` } : undefined} />
      </div>
      <p className="progress-panel__meta">
        {narrating ? (
          <>
            <span>{percent}% narrated</span>
            <span>
              {chaptersDone} of {job.chapters.length} chapters finished
            </span>
            <span>{etaText ? `${etaText[0].toUpperCase()}${etaText.slice(1)} left` : 'Estimating time left…'}</span>
          </>
        ) : (
          <span>Chapters will appear here as soon as they are found. You can start listening to chapter 1 while the rest is narrated.</span>
        )}
      </p>
    </section>
  )
}
