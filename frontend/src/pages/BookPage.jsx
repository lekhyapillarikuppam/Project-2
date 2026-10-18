import { useCallback, useEffect } from 'react'
import { Link } from 'react-router-dom'
import ChapterList from '../components/ChapterList.jsx'
import ConnectionBanner from '../components/ConnectionBanner.jsx'
import Notice from '../components/Notice.jsx'
import PlayerBar from '../components/PlayerBar.jsx'
import ProgressPanel from '../components/ProgressPanel.jsx'
import { useAudioPlayer } from '../hooks/useAudioPlayer.js'
import { ACTIVE_STATUSES, useJobPolling } from '../hooks/useJobPolling.js'
import { APP_NAME } from '../lib/brand.js'
import { messageFor, MESSAGES, UNAVAILABLE_CODES } from '../lib/errors.js'
import { bookName, formatDuration } from '../lib/format.js'
import '../styles/BookPage.css'

const NO_CHAPTERS = []

const STATUS_LABEL = {
  queued: 'Waiting to start',
  extracting: 'Reading',
  synthesizing: 'Narrating',
  done: 'Ready',
  partial: 'Ready, some chapters failed',
  failed: 'Failed',
  expired: 'No longer available',
}

function UploadAgain({ children = 'Upload a document' }) {
  return (
    <Link to="/" className="button button--primary">
      {children}
    </Link>
  )
}

function NotAvailable() {
  useEffect(() => {
    document.title = `No longer available · ${APP_NAME}`
  }, [])
  return (
    <div className="message-page">
      <h1>No longer available</h1>
      <p>{MESSAGES.JOB_NOT_FOUND}</p>
      <UploadAgain />
    </div>
  )
}

/**
 * The book view for a live job (`apiSource`) or the static sample (`sampleSource`), SPEC §11.
 */
export default function BookPage({ source }) {
  const { job, error, connection, refresh } = useJobPolling(source)
  const chapters = job?.chapters ?? NO_CHAPTERS
  const title = bookName(job?.filename)
  const { attachAudio, ...player } = useAudioPlayer({ source, chapters, bookTitle: title })

  const chapterNumber = useCallback((index) => {
    const position = chapters.findIndex((c) => c.index === index)
    return position >= 0 ? position + 1 : index
  }, [chapters])

  useEffect(() => {
    if (job) document.title = `${title} · ${APP_NAME}`
  }, [job, title])

  if (error && (UNAVAILABLE_CODES.has(error.code) || job?.status === 'expired')) return <NotAvailable />
  if (job?.status === 'expired') return <NotAvailable />

  const isApi = source.kind === 'api'
  const banner = isApi && (
    <ConnectionBanner checkServer retrying={connection === 'retrying'} lost={connection === 'lost'} lastSuccess={job} onRetry={refresh} />
  )

  if (!job) {
    return (
      <div className="book-page">
        {banner}
        {error ? (
          <div className="message-page">
            <h1>Something went wrong</h1>
            <p>
              {source.kind === 'sample'
                ? 'The sample audiobook could not be loaded. Please try again later.'
                : messageFor(error.code, error.message)}
            </p>
            <UploadAgain>Back to the start</UploadAgain>
          </div>
        ) : (
          <p className="book-page__loading" aria-live="polite">
            {connection === 'lost' ? 'Still trying to reach the server…' : 'Loading your audiobook…'}
          </p>
        )}
      </div>
    )
  }

  const working = ACTIVE_STATUSES.has(job.status)
  const failedCount = chapters.filter((c) => c.status === 'failed').length
  const readyChapters = chapters.filter((c) => c.status === 'ready')
  const totalSeconds = readyChapters.reduce((sum, c) => sum + (c.duration_seconds ?? 0), 0)
  const finishedWithAudio = (job.status === 'done' || job.status === 'partial') && readyChapters.length > 0
  const showPlayer = player.currentIndex != null || player.waitingFor != null || readyChapters.length > 0

  return (
    <div className={`book-page${showPlayer ? ' book-page--with-player' : ''}`}>
      {banner}

      <header className="book-header">
        <p className="book-header__eyebrow">{source.kind === 'sample' ? 'Sample audiobook' : 'Your audiobook'}</p>
        <h1 className="book-header__title">{title}</h1>
        <p className="book-header__meta">
          <span className={`status-pill status-pill--${job.status}`}>{STATUS_LABEL[job.status] ?? job.status}</span>
          {chapters.length > 0 && (
            <span>
              {chapters.length} {chapters.length === 1 ? 'chapter' : 'chapters'}
            </span>
          )}
          {!working && totalSeconds > 0 && <span>{formatDuration(totalSeconds)} of audio</span>}
        </p>
      </header>

      {working && <ProgressPanel job={job} />}

      {job.status === 'failed' && (
        <Notice
          tone="error"
          title="This document couldn't be turned into an audiobook"
          actions={<UploadAgain>Upload another document</UploadAgain>}
        >
          <p>{job.error ? messageFor(job.error.code, job.error.message) : messageFor(null)}</p>
        </Notice>
      )}

      {finishedWithAudio && (
        <div className="book-actions">
          <a
            className="button button--primary"
            href={source.fullDownloadUrl()}
            download={source.downloadName(job) ?? ''}
          >
            Download full book
          </a>
          {job.status === 'partial' && (
            <span className="book-actions__note">
              ({failedCount} {failedCount === 1 ? 'chapter' : 'chapters'} failed)
            </span>
          )}
        </div>
      )}

      {player.resume && (
        <Notice
          className="resume-notice"
          actions={
            <>
              <button type="button" className="button button--primary" onClick={player.acceptResume}>
                Resume
              </button>
              <button type="button" className="button button--secondary" onClick={player.dismissResume}>
                Dismiss
              </button>
            </>
          }
        >
          <p>
            Resume chapter {chapterNumber(player.resume.chapter)} at {formatDuration(player.resume.position)}?
          </p>
        </Notice>
      )}

      {job.warnings?.length > 0 && (
        <Notice title="Good to know">
          <ul className="book-page__warnings">
            {job.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </Notice>
      )}

      <ChapterList job={job} source={source} player={player} />

      {isApi && (
        <p className="book-page__temporary">
          Audio is kept for a limited time — download anything you want to keep.
        </p>
      )}

      <audio ref={attachAudio} preload="metadata" />
      {showPlayer && <PlayerBar player={player} chapterNumber={chapterNumber} />}
    </div>
  )
}
