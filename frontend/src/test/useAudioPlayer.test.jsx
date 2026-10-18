import { act, renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { findNext, useAudioPlayer } from '../hooks/useAudioPlayer.js'
import { loadPlayback, savePlayback } from '../lib/storage.js'

const source = { id: 'job-audio', audioUrl: (n) => `http://audio.test/ch_${n}.mp3` }

const chapter = (index, status) => ({ index, title: `Chapter ${index}`, status, duration_seconds: 100 })

function setup(chapters) {
  const audio = document.createElement('audio')
  const hook = renderHook(({ list }) => useAudioPlayer({ source, chapters: list, bookTitle: 'Book' }), {
    initialProps: { list: chapters },
  })
  act(() => hook.result.current.attachAudio(audio))
  return { audio, ...hook }
}

const end = (audio) => act(() => audio.dispatchEvent(new Event('ended')))

describe('findNext', () => {
  it('skips failed chapters and waits for unfinished ones', () => {
    const list = [chapter(1, 'ready'), chapter(2, 'failed'), chapter(3, 'ready'), chapter(4, 'pending')]
    expect(findNext(list, 1)).toEqual({ play: 3 })
    expect(findNext(list, 3)).toEqual({ wait: 4 })
    expect(findNext(list, 4)).toBeNull()
  })
})

describe('useAudioPlayer', () => {
  it('auto-advances to the next ready chapter, skipping failed ones', () => {
    const { audio, result } = setup([chapter(1, 'ready'), chapter(2, 'failed'), chapter(3, 'ready')])
    act(() => result.current.playChapter(1))
    expect(audio.src).toBe('http://audio.test/ch_1.mp3')
    expect(result.current.playing).toBe(true)

    end(audio)
    expect(audio.src).toBe('http://audio.test/ch_3.mp3')
    expect(result.current.currentIndex).toBe(3)

    end(audio) // last chapter: stop
    expect(result.current.currentIndex).toBe(3)
    expect(result.current.playing).toBe(false)
    expect(result.current.waitingFor).toBeNull()
  })

  it('waits for a pending chapter and starts it when it becomes ready', () => {
    const { audio, result, rerender } = setup([chapter(1, 'ready'), chapter(2, 'generating'), chapter(3, 'pending')])
    act(() => result.current.playChapter(1))
    end(audio)
    expect(result.current.waitingFor).toBe(2)
    expect(audio.src).toBe('http://audio.test/ch_1.mp3')

    rerender({ list: [chapter(1, 'ready'), chapter(2, 'ready'), chapter(3, 'generating')] })
    expect(result.current.waitingFor).toBeNull()
    expect(result.current.currentIndex).toBe(2)
    expect(audio.src).toBe('http://audio.test/ch_2.mp3')
    expect(result.current.playing).toBe(true)
  })

  it('moves past a waited-for chapter that fails', () => {
    const { audio, result, rerender } = setup([chapter(1, 'ready'), chapter(2, 'generating'), chapter(3, 'pending')])
    act(() => result.current.playChapter(1))
    end(audio)
    rerender({ list: [chapter(1, 'ready'), chapter(2, 'failed'), chapter(3, 'generating')] })
    expect(result.current.waitingFor).toBe(3)
    rerender({ list: [chapter(1, 'ready'), chapter(2, 'failed'), chapter(3, 'ready')] })
    expect(audio.src).toBe('http://audio.test/ch_3.mp3')
  })

  it('keeps the chosen speed across chapters and remembers it', () => {
    const { audio, result } = setup([chapter(1, 'ready'), chapter(2, 'ready')])
    act(() => result.current.playChapter(1))
    act(() => result.current.setRate(1.5))
    expect(audio.playbackRate).toBe(1.5)
    expect(audio.preservesPitch).toBe(true)

    end(audio)
    expect(result.current.currentIndex).toBe(2)
    expect(result.current.rate).toBe(1.5)
    expect(audio.playbackRate).toBe(1.5)
    expect(loadPlayback(source.id)).toMatchObject({ chapter: 2, rate: 1.5 })
  })

  it('offers to resume the saved chapter and position', () => {
    savePlayback(source.id, { chapter: 2, position: 75, rate: 1.25 })
    const { audio, result } = setup([chapter(1, 'ready'), chapter(2, 'ready')])
    expect(result.current.rate).toBe(1.25)
    expect(result.current.resume).toMatchObject({ chapter: 2, position: 75 })
    act(() => result.current.acceptResume())
    expect(audio.src).toBe('http://audio.test/ch_2.mp3')
    expect(result.current.resume).toBeNull()
    expect(result.current.time).toBe(75)
  })
})
