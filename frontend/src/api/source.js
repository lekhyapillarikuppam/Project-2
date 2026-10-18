// Data sources for the book view (SPEC §11): the live API job, or the static sample in
// public/sample/ that never touches the backend (D22). Both expose the same shape.

import { apiUrl, getJson } from './client.js'

const pad = (n, width) => String(n).padStart(width, '0')

/** Like the server's filename rules (SPEC §9): drop invalid characters, whitespace -> "_", 60 chars. */
export function sanitizePart(text) {
  return String(text)
    .replace(/\s+/g, '_')
    // eslint-disable-next-line no-control-regex
    .replace(/[<>:"/\\|?*\u0000-\u001f]/g, '')
    .slice(0, 60)
}

function bookPart(job) {
  const name = job?.filename ?? 'audiobook'
  const dot = name.lastIndexOf('.')
  return sanitizePart(dot > 0 ? name.slice(0, dot) : name)
}

/**
 * @typedef {object} BookSource
 * @property {'api'|'sample'} kind
 * @property {string} id                  job id used for saved playback state
 * @property {boolean} live               true if the job can still change (poll it)
 * @property {(options?: object) => Promise<object>} getJob   the §7.2 job object
 * @property {(n: number) => string} audioUrl
 * @property {(n: number) => string} downloadUrl
 * @property {() => string} fullDownloadUrl
 * @property {(job: object, n?: number) => string|undefined} downloadName  `download` attribute, if any
 */

/** @returns {BookSource} */
export function apiSource(jobId) {
  const base = `/api/jobs/${encodeURIComponent(jobId)}`
  return {
    kind: 'api',
    id: jobId,
    live: true,
    getJob: (options) => getJson(apiUrl(base), options),
    audioUrl: (n) => apiUrl(`${base}/chapters/${n}/audio`),
    downloadUrl: (n) => apiUrl(`${base}/download/${n}`),
    fullDownloadUrl: () => apiUrl(`${base}/download/full`),
    // The server sends Content-Disposition with the filename; a cross-origin download attribute is ignored.
    downloadName: () => undefined,
  }
}

/**
 * A static sample under public/: `folder` holds job.json and ch_NNN.mp3. `fullFile` is the
 * full-book file (the one-chapter character-voices sample uses its chapter instead of a copy).
 * @returns {BookSource}
 */
export function sampleSource(baseUrl = import.meta.env.BASE_URL, { folder = 'sample', id = 'sample', fullFile = 'full.mp3' } = {}) {
  const root = `${baseUrl.replace(/\/?$/, '/')}${folder}/`
  const chapterFile = (n) => `${root}ch_${pad(n, 3)}.mp3`
  return {
    kind: 'sample',
    id,
    live: false,
    getJob: (options) => getJson(`${root}job.json`, { ...options, retry: false }),
    audioUrl: chapterFile,
    downloadUrl: chapterFile,
    fullDownloadUrl: () => `${root}${fullFile}`,
    downloadName: (job, n) => {
      if (n == null) return `${bookPart(job)}.mp3`
      const chapter = job?.chapters?.find((c) => c.index === n)
      return `${bookPart(job)}_${pad(n, 2)}_${sanitizePart(chapter?.title ?? `Chapter ${n}`)}.mp3`
    },
  }
}
