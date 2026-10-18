import { useEffect, useRef, useState } from 'react'
import { checkHealth, isAbort } from '../api/client.js'
import '../styles/ConnectionBanner.css'

const HEALTH_RETRY_MS = 5000

/**
 * "Can't reach the server right now. Retrying…" (SPEC §11).
 * Shown while `retrying` or `lost` (from polling) is true, or — with `checkServer` — when
 * GET /api/health fails or takes more than 5 s on load; the health check then repeats every 5 s.
 * `lastSuccess` changes whenever another request succeeded, which also hides the banner.
 */
export default function ConnectionBanner({ checkServer = false, retrying = false, lost = false, lastSuccess, onRetry }) {
  // The failed check remembers which `lastSuccess` it saw; a newer success hides the banner.
  const [failedAt, setFailedAt] = useState(null)
  const lastSuccessRef = useRef(lastSuccess)

  useEffect(() => {
    lastSuccessRef.current = lastSuccess
  }, [lastSuccess])

  useEffect(() => {
    if (!checkServer) return undefined
    const controller = new AbortController()
    let timer = null
    async function check() {
      try {
        const { ok } = await checkHealth({ signal: controller.signal })
        setFailedAt(ok ? null : { seen: lastSuccessRef.current })
        if (!ok) timer = setTimeout(check, HEALTH_RETRY_MS)
      } catch (error) {
        if (!isAbort(error)) timer = setTimeout(check, HEALTH_RETRY_MS)
      }
    }
    check()
    return () => {
      controller.abort()
      clearTimeout(timer)
    }
  }, [checkServer])

  const healthBad = failedAt != null && failedAt.seen === lastSuccess
  const visible = healthBad || retrying || lost
  return (
    <div className="connection-banner-slot" role="status" aria-live="polite">
      {visible && (
        <div className="connection-banner">
          <span className="connection-banner__dot" aria-hidden="true" />
          <span className="connection-banner__text">Can&rsquo;t reach the server right now. Retrying&hellip;</span>
          {lost && onRetry && (
            <button type="button" className="button button--small" onClick={onRetry}>
              Try again now
            </button>
          )}
        </div>
      )}
    </div>
  )
}
