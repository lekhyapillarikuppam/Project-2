import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { getConfig, isAbort, uploadFile } from '../api/client.js'
import ConnectionBanner from '../components/ConnectionBanner.jsx'
import Dropzone from '../components/Dropzone.jsx'
import Notice from '../components/Notice.jsx'
import { APP_NAME, TAGLINE } from '../lib/brand.js'
import { messageFor } from '../lib/errors.js'
import { formatNumber, formatSize } from '../lib/format.js'
import '../styles/UploadPage.css'

// Used when GET /api/config can't be reached (SPEC §4).
const DEFAULT_LIMITS = {
  maxFileMb: 50,
  maxPdfPages: 200,
  maxWords: 100000,
  acceptedTypes: ['.pdf', '.docx'],
}

function limitsFrom(config) {
  if (!config) return DEFAULT_LIMITS
  return {
    maxFileMb: config.max_file_mb ?? DEFAULT_LIMITS.maxFileMb,
    maxPdfPages: config.max_pdf_pages ?? DEFAULT_LIMITS.maxPdfPages,
    maxWords: config.max_words ?? DEFAULT_LIMITS.maxWords,
    acceptedTypes: config.accepted_types?.length ? config.accepted_types : DEFAULT_LIMITS.acceptedTypes,
  }
}

function extension(name) {
  const dot = name.lastIndexOf('.')
  return dot >= 0 ? name.slice(dot).toLowerCase() : ''
}

/** Client-side check before uploading (SPEC §4): returns an error code or null. */
function checkFile(file, limits) {
  const ext = extension(file.name)
  if (ext === '.doc') return 'LEGACY_DOC'
  if (!limits.acceptedTypes.includes(ext)) return 'UNSUPPORTED_TYPE'
  if (file.size > limits.maxFileMb * 1024 * 1024) return 'FILE_TOO_LARGE'
  return null
}

function typeList(types) {
  const names = types.map((t) => t.replace('.', '').toUpperCase())
  return names.length > 1 ? `${names.slice(0, -1).join(', ')} or ${names.at(-1)}` : names[0]
}

export default function UploadPage() {
  const navigate = useNavigate()
  const [config, setConfig] = useState(null)
  const [phase, setPhase] = useState('idle') // idle | uploading | checking
  const [progress, setProgress] = useState(0)
  const [file, setFile] = useState(null)
  const [error, setError] = useState(null)
  const uploadRef = useRef(null)
  const limits = limitsFrom(config)

  useEffect(() => {
    document.title = `${APP_NAME} · ${TAGLINE}`
    const controller = new AbortController()
    getConfig({ signal: controller.signal })
      .then((value) => value && setConfig(value))
      .catch(() => {})
    return () => {
      controller.abort()
      uploadRef.current?.abort()
    }
  }, [])

  async function start(chosen) {
    setError(null)
    const problem = checkFile(chosen, limits)
    if (problem) {
      setFile(null)
      setError(messageFor(problem, null, limits))
      return
    }
    setFile(chosen)
    setProgress(0)
    setPhase('uploading')
    const upload = uploadFile(chosen, {
      onProgress: (fraction) => {
        setProgress(fraction)
        if (fraction >= 1) setPhase('checking')
      },
    })
    uploadRef.current = upload
    try {
      const { job_id: jobId } = await upload.promise
      navigate(`/book/${encodeURIComponent(jobId)}`)
    } catch (err) {
      if (!isAbort(err)) setError(messageFor(err.code, err.message, limits))
      setPhase('idle')
    } finally {
      uploadRef.current = null
    }
  }

  const busy = phase !== 'idle'
  const percent = Math.round(progress * 100)
  const hint = `${typeList(limits.acceptedTypes)} · up to ${limits.maxFileMb} MB · PDFs up to ${limits.maxPdfPages} pages · up to ${formatNumber(limits.maxWords)} words`

  return (
    <div className="upload-page">
      <ConnectionBanner checkServer lastSuccess={config} />

      <section className="upload-hero">
        <h1 className="upload-hero__title">Listen to any document</h1>
        <p className="upload-hero__lead">
          Upload a PDF or Word document and {APP_NAME} reads it aloud, chapter by chapter, as an audiobook you can play
          here or download.
        </p>
      </section>

      <section className="upload-card" aria-label="Upload a document">
        {busy ? (
          <div className="upload-progress">
            <p className="upload-progress__file">
              <span className="upload-progress__name">{file?.name}</span>
              <span className="upload-progress__size">{formatSize(file?.size)}</span>
            </p>
            <div
              className="progress-bar"
              role="progressbar"
              aria-label="Upload progress"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={percent}
            >
              <div className="progress-bar__fill" style={{ width: `${percent}%` }} />
            </div>
            <p className="upload-progress__status" aria-live="polite">
              {phase === 'checking' ? 'Checking your document…' : `Uploading… ${percent}%`}
            </p>
            {phase === 'uploading' && (
              <button type="button" className="button button--secondary" onClick={() => uploadRef.current?.abort()}>
                Cancel
              </button>
            )}
          </div>
        ) : (
          <Dropzone onFile={start} accept={limits.acceptedTypes.join(',')} hint={hint} />
        )}

        {error && (
          <Notice tone="error" className="upload-card__error">
            {error}
          </Notice>
        )}

        <div className="upload-card__sample">
          <span>Not sure yet?</span>
          <Link to="/sample" className="button button--secondary">
            Try a sample
          </Link>
        </div>
      </section>

      <section className="how-it-works" aria-labelledby="how-heading">
        <h2 id="how-heading">How it works</h2>
        <ol>
          <li>
            <span className="how-it-works__step" aria-hidden="true">
              1
            </span>
            <div>
              <h3>Upload</h3>
              <p>Choose a PDF or DOCX with selectable text. Scanned pages can&rsquo;t be read.</p>
            </div>
          </li>
          <li>
            <span className="how-it-works__step" aria-hidden="true">
              2
            </span>
            <div>
              <h3>We find the chapters</h3>
              <p>Headings become chapters; tables, footnotes and page numbers are left out.</p>
            </div>
          </li>
          <li>
            <span className="how-it-works__step" aria-hidden="true">
              3
            </span>
            <div>
              <h3>Listen or download</h3>
              <p>Start with chapter 1 while the rest is narrated, then download the chapters or the whole book.</p>
            </div>
          </li>
        </ol>
      </section>
    </div>
  )
}
