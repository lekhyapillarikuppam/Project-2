// localStorage can be missing or throw (private windows, blocked site data): every access is guarded.

const PREFIX = 'readaloud:'

export function jobKey(jobId) {
  return `${PREFIX}job:${jobId}`
}

export function loadJSON(key) {
  try {
    const raw = window.localStorage.getItem(key)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

export function saveJSON(key, value) {
  try {
    window.localStorage.setItem(key, JSON.stringify(value))
    return true
  } catch {
    return false
  }
}

export function removeKey(key) {
  try {
    window.localStorage.removeItem(key)
  } catch {
    // nothing to do
  }
}

/** Saved player state for a job: {chapter, position, rate} or null. */
export function loadPlayback(jobId) {
  const saved = loadJSON(jobKey(jobId))
  if (!saved || typeof saved !== 'object') return null
  return {
    chapter: Number.isInteger(saved.chapter) ? saved.chapter : null,
    position: Number.isFinite(saved.position) ? saved.position : 0,
    rate: Number.isFinite(saved.rate) ? saved.rate : 1,
  }
}

export function savePlayback(jobId, state) {
  return saveJSON(jobKey(jobId), state)
}
