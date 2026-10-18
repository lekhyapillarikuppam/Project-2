import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { APP_NAME } from '../lib/brand.js'
import { loadPlayback, savePlayback } from '../lib/storage.js'

export const SPEEDS = [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2]
export const SKIP_SECONDS = 15
const SAVE_EVERY_MS = 5000
// "Previous" restarts the chapter when more than this much of it has played.
const RESTART_THRESHOLD_S = 3
// A saved position shorter than this (in chapter 1) is not worth a resume prompt.
const MIN_RESUME_S = 5

/**
 * Where playback goes after chapter `from` (SPEC §11): the next ready chapter, skipping failed ones.
 * Returns {play: n}, {wait: n} when the next unfinished chapter is still pending/generating, or null.
 */
export function findNext(chapters, from) {
  for (const chapter of chapters) {
    if (chapter.index <= from || chapter.status === 'failed') continue
    return chapter.status === 'ready' ? { play: chapter.index } : { wait: chapter.index }
  }
  return null
}

function findPrevReady(chapters, from) {
  let found = null
  for (const chapter of chapters) {
    if (chapter.index >= from) break
    if (chapter.status === 'ready') found = chapter.index
  }
  return found
}

function setPitchAndRate(audio, rate) {
  audio.defaultPlaybackRate = rate
  audio.playbackRate = rate
  audio.preservesPitch = true
  audio.webkitPreservesPitch = true
}

function isTypingTarget(target) {
  const tag = target?.tagName
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || Boolean(target?.isContentEditable)
}

/**
 * One <audio> element for the whole book: pass `attachAudio` as its ref.
 * chapters: the job's chapter objects (§7.2); the hook reacts when their statuses change.
 */
export function useAudioPlayer({ source, chapters, bookTitle }) {
  const audioElRef = useRef(null)
  const [attached, setAttached] = useState(false)
  const attachAudio = useCallback((element) => {
    audioElRef.current = element
    setAttached(Boolean(element))
  }, [])
  const saved = useMemo(() => loadPlayback(source.id), [source.id])

  const [current, setCurrent] = useState(null)
  const [playing, setPlaying] = useState(false)
  const [time, setTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [rate, setRateState] = useState(() => (SPEEDS.includes(saved?.rate) ? saved.rate : 1))
  const [waitingFor, setWaitingFor] = useState(null)
  const [buffering, setBuffering] = useState(false)
  const [resumeOffer, setResumeOffer] = useState(() =>
    saved?.chapter != null && (saved.chapter > 1 || saved.position >= MIN_RESUME_S)
      ? { chapter: saved.chapter, position: saved.position }
      : null,
  )

  const chaptersRef = useRef(chapters)
  const currentRef = useRef(null)
  const rateRef = useRef(rate)
  const waitingRef = useRef(null)
  const pendingSeekRef = useRef(null)
  const lastSaveRef = useRef(0)
  useLayoutEffect(() => {
    chaptersRef.current = chapters
  }, [chapters])

  const persist = useCallback(
    (position) => {
      lastSaveRef.current = Date.now()
      savePlayback(source.id, { chapter: currentRef.current, position: Math.floor(position ?? 0), rate: rateRef.current })
    },
    [source.id],
  )

  const setWaiting = (n) => {
    waitingRef.current = n
    setWaitingFor(n)
  }

  const load = useCallback(
    (n, startAt = 0, autoplay = true) => {
      const audio = audioElRef.current
      if (!audio) return
      const chapter = chaptersRef.current.find((c) => c.index === n)
      currentRef.current = n
      setCurrent(n)
      setWaiting(null)
      setResumeOffer(null)
      pendingSeekRef.current = startAt > 0 ? startAt : null
      audio.src = source.audioUrl(n)
      setPitchAndRate(audio, rateRef.current)
      setTime(startAt)
      setDuration(chapter?.duration_seconds ?? 0)
      persist(startAt)
      if (autoplay) {
        const result = audio.play()
        if (result?.catch) result.catch(() => setPlaying(false))
      }
    },
    [source, persist],
  )

  const goNext = useCallback(() => {
    const target = findNext(chaptersRef.current, currentRef.current ?? 0)
    if (target?.play != null) {
      load(target.play)
    } else if (target?.wait != null) {
      audioElRef.current?.pause()
      setWaiting(target.wait)
    } else {
      setWaiting(null)
      setPlaying(false)
    }
  }, [load])

  const goPrev = useCallback(() => {
    const audio = audioElRef.current
    if (!audio || currentRef.current == null) return
    if (audio.currentTime > RESTART_THRESHOLD_S) {
      audio.currentTime = 0
      setTime(0)
      return
    }
    const target = findPrevReady(chaptersRef.current, currentRef.current)
    if (target != null) load(target)
    else {
      audio.currentTime = 0
      setTime(0)
    }
  }, [load])

  const resume = useMemo(() => {
    if (!resumeOffer || current != null) return null
    const chapter = chapters.find((c) => c.index === resumeOffer.chapter)
    return chapter?.status === 'ready' ? { ...resumeOffer, title: chapter.title } : null
  }, [resumeOffer, current, chapters])

  const acceptResume = useCallback(() => {
    if (resume) load(resume.chapter, resume.position)
  }, [resume, load])

  const dismissResume = useCallback(() => setResumeOffer(null), [])

  const toggle = useCallback(() => {
    const audio = audioElRef.current
    if (!audio) return
    if (currentRef.current == null) {
      if (resume) {
        acceptResume()
        return
      }
      const first = chaptersRef.current.find((c) => c.status === 'ready')
      if (first) load(first.index)
      return
    }
    if (audio.paused) {
      const result = audio.play()
      if (result?.catch) result.catch(() => setPlaying(false))
    } else {
      audio.pause()
    }
  }, [resume, acceptResume, load])

  const playChapter = useCallback(
    (n) => {
      if (n === currentRef.current) toggle()
      else load(n)
    },
    [load, toggle],
  )

  const seek = useCallback(
    (seconds) => {
      const audio = audioElRef.current
      if (!audio || currentRef.current == null) return
      const max = Number.isFinite(audio.duration) && audio.duration > 0 ? audio.duration : duration
      const target = Math.max(0, max ? Math.min(seconds, max) : seconds)
      audio.currentTime = target
      setTime(target)
    },
    [duration],
  )

  const skip = useCallback((delta) => seek((audioElRef.current?.currentTime ?? 0) + delta), [seek])

  const setRate = useCallback(
    (value) => {
      rateRef.current = value
      setRateState(value)
      const audio = audioElRef.current
      if (audio) setPitchAndRate(audio, value)
      persist(audio?.currentTime ?? 0)
    },
    [persist],
  )

  // Audio element events.
  useEffect(() => {
    const audio = audioElRef.current
    if (!attached || !audio) return undefined
    const onPlay = () => setPlaying(true)
    const onPause = () => {
      setPlaying(false)
      if (currentRef.current != null) persist(audio.currentTime)
    }
    const onTime = () => {
      setTime(audio.currentTime)
      if (Date.now() - lastSaveRef.current >= SAVE_EVERY_MS) persist(audio.currentTime)
    }
    const onMeta = () => {
      if (Number.isFinite(audio.duration) && audio.duration > 0) setDuration(audio.duration)
      if (pendingSeekRef.current != null) {
        audio.currentTime = pendingSeekRef.current
        pendingSeekRef.current = null
      }
      // Some browsers reset the rate when a new source loads.
      setPitchAndRate(audio, rateRef.current)
    }
    const onEnded = () => {
      setPlaying(false)
      goNext()
    }
    const onWaiting = () => setBuffering(true)
    const onReady = () => setBuffering(false)
    const events = {
      play: onPlay,
      pause: onPause,
      timeupdate: onTime,
      loadedmetadata: onMeta,
      ended: onEnded,
      waiting: onWaiting,
      playing: onReady,
      canplay: onReady,
    }
    Object.entries(events).forEach(([name, fn]) => audio.addEventListener(name, fn))
    return () => Object.entries(events).forEach(([name, fn]) => audio.removeEventListener(name, fn))
  }, [attached, goNext, persist])

  // A chapter we were waiting for became ready (or failed): continue automatically.
  useEffect(() => {
    if (waitingRef.current == null) return
    const target = findNext(chapters, currentRef.current ?? 0)
    if (target?.play != null) load(target.play)
    else if (target?.wait != null) {
      if (target.wait !== waitingRef.current) setWaiting(target.wait)
    } else setWaiting(null)
  }, [chapters, load])

  // Keyboard shortcuts: Space, ←/→ (15 s), Shift+←/→ (chapters).
  const actionsRef = useRef({})
  useLayoutEffect(() => {
    actionsRef.current = { toggle, skip, goNext, goPrev }
  }, [toggle, skip, goNext, goPrev])
  useEffect(() => {
    function onKeyDown(event) {
      if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return
      const target = event.target
      if (isTypingTarget(target)) return
      const actions = actionsRef.current
      if (event.key === ' ' || event.code === 'Space') {
        if (target?.tagName === 'BUTTON' || target?.tagName === 'A') return
        event.preventDefault()
        actions.toggle()
      } else if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
        if (currentRef.current == null) return
        event.preventDefault()
        const forward = event.key === 'ArrowRight'
        if (event.shiftKey) {
          if (forward) actions.goNext()
          else actions.goPrev()
        } else actions.skip(forward ? SKIP_SECONDS : -SKIP_SECONDS)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])

  const currentChapter = chapters.find((c) => c.index === current) ?? null

  // Media Session: lock-screen title and next/previous buttons, where supported.
  useEffect(() => {
    const session = typeof navigator !== 'undefined' ? navigator.mediaSession : undefined
    if (!session) return undefined
    if (currentChapter && typeof window.MediaMetadata === 'function') {
      session.metadata = new window.MediaMetadata({ title: currentChapter.title, artist: APP_NAME, album: bookTitle })
    }
    const handlers = {
      play: () => {
        if (audioElRef.current?.paused) actionsRef.current.toggle()
      },
      pause: () => audioElRef.current?.pause(),
      previoustrack: () => actionsRef.current.goPrev(),
      nexttrack: () => actionsRef.current.goNext(),
      seekbackward: () => actionsRef.current.skip(-SKIP_SECONDS),
      seekforward: () => actionsRef.current.skip(SKIP_SECONDS),
    }
    Object.entries(handlers).forEach(([action, fn]) => {
      try {
        session.setActionHandler(action, fn)
      } catch {
        // action not supported by this browser
      }
    })
    return () =>
      Object.keys(handlers).forEach((action) => {
        try {
          session.setActionHandler(action, null)
        } catch {
          // ignore
        }
      })
  }, [currentChapter, bookTitle])

  const hasPrev = current != null
  const hasNext = current != null && findNext(chapters, current) != null

  return {
    attachAudio,
    currentIndex: current,
    currentChapter,
    playing,
    time,
    duration,
    rate,
    waitingFor,
    buffering,
    resume,
    hasPrev,
    hasNext,
    playChapter,
    toggle,
    seek,
    skip,
    next: goNext,
    prev: goPrev,
    setRate,
    acceptResume,
    dismissResume,
  }
}
