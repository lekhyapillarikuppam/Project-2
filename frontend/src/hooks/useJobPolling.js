import { useCallback, useEffect, useState } from 'react'
import { isAbort } from '../api/client.js'

export const ACTIVE_STATUSES = new Set(['queued', 'extracting', 'synthesizing'])
export const POLL_INTERVAL_MS = 3000
// After a request has given up (2 min of retries), keep trying quietly at this pace.
export const LOST_RETRY_MS = 30000

/**
 * Loads the job from `source` and polls it every 3 s while it is queued/extracting/synthesizing
 * (SPEC §11). Dropped connections are retried by the client (1/2/4/8 s for up to 2 min).
 *
 * connection: 'ok' | 'retrying' (a request is being retried) | 'lost' (gave up; still retried slowly).
 * error: an ApiError the server returned (e.g. JOB_NOT_FOUND); polling stops.
 * When the tab becomes visible again, an unfinished job is polled at once.
 */
export function useJobPolling(source, { intervalMs = POLL_INTERVAL_MS, lostRetryMs = LOST_RETRY_MS } = {}) {
  const [state, setState] = useState({ job: null, error: null, connection: 'ok' })
  const [generation, setGeneration] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    let timer = null
    let inFlight = false
    let finished = false

    const schedule = (ms) => {
      clearTimeout(timer)
      timer = setTimeout(poll, ms)
    }

    async function poll() {
      if (inFlight || finished || controller.signal.aborted) return
      clearTimeout(timer)
      inFlight = true
      try {
        const job = await source.getJob({
          signal: controller.signal,
          onRetry: () => setState((s) => (s.connection === 'retrying' ? s : { ...s, connection: 'retrying' })),
        })
        finished = !source.live || !ACTIVE_STATUSES.has(job.status)
        setState({ job, error: null, connection: 'ok' })
        if (!finished) schedule(intervalMs)
      } catch (error) {
        if (isAbort(error)) return
        if (error?.isNetwork) {
          setState((s) => ({ ...s, connection: 'lost' }))
          schedule(lostRetryMs)
        } else {
          finished = true
          setState((s) => ({ ...s, error, connection: 'ok' }))
        }
      } finally {
        inFlight = false
      }
    }

    function onVisibility() {
      if (document.visibilityState === 'visible') poll()
    }

    poll()
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      controller.abort()
      clearTimeout(timer)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [source, intervalMs, lostRetryMs, generation])

  /** Poll again now (e.g. a "Try again" button after the connection was lost). */
  const refresh = useCallback(() => setGeneration((g) => g + 1), [])

  return { ...state, refresh }
}
