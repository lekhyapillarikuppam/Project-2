/** 75 -> "1:15", 3725 -> "1:02:05". Missing or negative values -> "0:00". */
export function formatDuration(seconds) {
  const total = Number.isFinite(seconds) && seconds > 0 ? Math.floor(seconds) : 0
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  const ss = String(s).padStart(2, '0')
  return h ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`
}

/** Spoken-style duration for screen readers: "1 hour 2 minutes 5 seconds". */
export function describeDuration(seconds) {
  const total = Number.isFinite(seconds) && seconds > 0 ? Math.floor(seconds) : 0
  const parts = [
    [Math.floor(total / 3600), 'hour'],
    [Math.floor((total % 3600) / 60), 'minute'],
    [total % 60, 'second'],
  ]
    .filter(([n]) => n > 0)
    .map(([n, unit]) => `${n} ${unit}${n === 1 ? '' : 's'}`)
  return parts.length ? parts.join(' ') : '0 seconds'
}

/** ETA in plain words: null -> null, 40 -> "less than a minute", 230 -> "about 4 min", 4300 -> "about 1 h 12 min". */
export function formatEta(seconds) {
  if (seconds == null || !Number.isFinite(seconds)) return null
  if (seconds < 60) return 'less than a minute'
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `about ${minutes} min`
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  return m ? `about ${h} h ${m} min` : `about ${h} h`
}

/** 1536 -> "1.5 KB", 52428800 -> "50 MB". */
export function formatSize(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) return ''
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB']
  let value = bytes / 1024
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  const text = value >= 10 || Number.isInteger(value) ? Math.round(value).toString() : value.toFixed(1)
  return `${text} ${units[unit]}`
}

/** "Alice in Wonderland.pdf" -> "Alice in Wonderland". */
export function bookName(filename) {
  if (!filename) return 'Your audiobook'
  const dot = filename.lastIndexOf('.')
  return dot > 0 ? filename.slice(0, dot) : filename
}

/** 1234567 -> "1,234,567". */
export function formatNumber(n) {
  return Number.isFinite(n) ? n.toLocaleString('en-US') : ''
}
