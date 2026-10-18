// fetch wrapper for the backend API (SPEC §9): base URL, JSON, the error shape -> ApiError, and
// retry with backoff for GETs so polling survives dropped connections (SPEC §11).

export const API_URL = String(import.meta.env.VITE_API_URL ?? '').replace(/\/+$/, '')

export const RETRY_DELAYS_MS = [1000, 2000, 4000, 8000]
export const GIVE_UP_MS = 2 * 60 * 1000
const REQUEST_TIMEOUT_MS = 15000
const HEALTH_TIMEOUT_MS = 5000
// Caddy answers 502/504 while the API container restarts; treat them like a dropped connection.
const RETRYABLE_STATUS = new Set([502, 503, 504])

export class ApiError extends Error {
  /** code: SPEC §12 code, 'NETWORK_ERROR', or 'HTTP_<status>' when the body has no error shape. */
  constructor(code, message, status = 0) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
  }

  get isNetwork() {
    return this.code === 'NETWORK_ERROR'
  }
}

export function apiUrl(path) {
  return `${API_URL}${path}`
}

export function networkError() {
  return new ApiError('NETWORK_ERROR', "Can't reach the server.", 0)
}

function abortError() {
  return new DOMException('The request was aborted.', 'AbortError')
}

export function isAbort(error) {
  return error?.name === 'AbortError'
}

/** `{"error": {"code", "message"}}` -> ApiError; anything else -> ApiError('HTTP_<status>'). */
export function parseErrorBody(body, status) {
  const error = body && typeof body === 'object' ? body.error : null
  if (error && typeof error.code === 'string') {
    return new ApiError(error.code, typeof error.message === 'string' ? error.message : '', status)
  }
  return new ApiError(`HTTP_${status}`, '', status)
}

async function errorFromResponse(response) {
  let body = null
  try {
    body = await response.json()
  } catch {
    // not JSON (e.g. a proxy error page)
  }
  return parseErrorBody(body, response.status)
}

function sleep(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(abortError())
      return
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort)
      resolve()
    }, ms)
    function onAbort() {
      clearTimeout(timer)
      reject(abortError())
    }
    signal?.addEventListener('abort', onAbort, { once: true })
  })
}

/** fetch with a timeout; a timeout or connection failure becomes a NETWORK_ERROR ApiError. */
async function fetchOnce(url, { signal, timeoutMs }) {
  const controller = new AbortController()
  const onAbort = () => controller.abort()
  signal?.addEventListener('abort', onAbort, { once: true })
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    return await fetch(url, { signal: controller.signal, headers: { Accept: 'application/json' } })
  } catch (error) {
    if (signal?.aborted) throw abortError()
    throw networkError(error)
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', onAbort)
  }
}

/**
 * GET a JSON document. Network failures and 502/503/504 are retried after 1, 2, 4, 8, 8 … s until
 * two minutes have passed, then a NETWORK_ERROR ApiError is thrown. Other HTTP errors throw at once.
 * onRetry({attempt, delayMs, error}) is called before each wait.
 */
export async function getJson(url, options = {}) {
  const {
    signal,
    onRetry,
    retry = true,
    timeoutMs = REQUEST_TIMEOUT_MS,
    retryDelays = RETRY_DELAYS_MS,
    giveUpMs = GIVE_UP_MS,
  } = options
  const started = Date.now()
  for (let attempt = 1; ; attempt += 1) {
    let lastError
    try {
      const response = await fetchOnce(url, { signal, timeoutMs })
      if (response.ok) return await response.json()
      lastError = await errorFromResponse(response)
      if (!RETRYABLE_STATUS.has(response.status)) throw lastError
    } catch (error) {
      if (isAbort(error)) throw error
      if (!(error instanceof ApiError)) throw error
      if (!error.isNetwork && !RETRYABLE_STATUS.has(error.status)) throw error
      lastError = error
    }
    if (!retry) throw lastError
    const delayMs = retryDelays[Math.min(attempt - 1, retryDelays.length - 1)]
    if (Date.now() - started + delayMs > giveUpMs) throw networkError()
    onRetry?.({ attempt, delayMs, error: lastError })
    await sleep(delayMs, signal)
  }
}

/** One GET /api/health, no retries: {ok, ms}. Slower than 5 s counts as not ok. */
export async function checkHealth({ signal, timeoutMs = HEALTH_TIMEOUT_MS } = {}) {
  const started = Date.now()
  try {
    const response = await fetchOnce(apiUrl('/api/health'), { signal, timeoutMs })
    return { ok: response.ok, ms: Date.now() - started }
  } catch (error) {
    if (isAbort(error)) throw error
    return { ok: false, ms: Date.now() - started }
  }
}

/** GET /api/config, or null if the server can't be reached (the page then uses default limits). */
export async function getConfig({ signal } = {}) {
  try {
    return await getJson(apiUrl('/api/config'), { signal, retry: false, timeoutMs: 8000 })
  } catch (error) {
    if (isAbort(error)) throw error
    return null
  }
}

/**
 * POST /api/upload with XMLHttpRequest, because fetch has no upload progress.
 * Returns {promise, abort}; the promise resolves to {job_id, status} on 202 and rejects with an
 * ApiError (or an AbortError after abort()). onProgress(fraction) reports the bytes sent.
 */
export function uploadFile(file, { onProgress } = {}) {
  const xhr = new XMLHttpRequest()
  const promise = new Promise((resolve, reject) => {
    xhr.open('POST', apiUrl('/api/upload'))
    xhr.setRequestHeader('Accept', 'application/json')
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && event.total > 0) onProgress?.(event.loaded / event.total)
    }
    xhr.upload.onload = () => onProgress?.(1)
    xhr.onload = () => {
      let body = null
      try {
        body = JSON.parse(xhr.responseText)
      } catch {
        // handled below
      }
      if (xhr.status === 202 && body?.job_id) resolve(body)
      else reject(xhr.status ? parseErrorBody(body, xhr.status) : networkError())
    }
    xhr.onerror = () => reject(networkError())
    xhr.ontimeout = () => reject(networkError())
    xhr.onabort = () => reject(abortError())
    const form = new FormData()
    form.append('file', file, file.name)
    xhr.send(form)
  })
  return { promise, abort: () => xhr.abort() }
}
