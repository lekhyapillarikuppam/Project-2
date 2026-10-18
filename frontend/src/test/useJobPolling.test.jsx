import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { apiSource } from '../api/source.js'
import { useJobPolling } from '../hooks/useJobPolling.js'
import { fakeSource, jsonResponse, makeJob } from './helpers.js'

const flush = () => act(() => vi.advanceTimersByTimeAsync(0))
const advance = (ms) => act(() => vi.advanceTimersByTimeAsync(ms))

describe('useJobPolling', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('polls every 3 s while the job runs and stops when it is done', async () => {
    const replies = [makeJob({ status: 'queued' }), makeJob(), makeJob({ status: 'done' })]
    const source = fakeSource(async () => replies.shift())
    const { result } = renderHook(() => useJobPolling(source))

    await flush()
    expect(result.current.job.status).toBe('queued')
    expect(source.getJob).toHaveBeenCalledTimes(1)

    await advance(3000)
    expect(result.current.job.status).toBe('synthesizing')
    await advance(3000)
    expect(result.current.job.status).toBe('done')
    expect(source.getJob).toHaveBeenCalledTimes(3)

    await advance(30000)
    expect(source.getJob).toHaveBeenCalledTimes(3)
  })

  it('stops and reports the error for an unknown job', async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ error: { code: 'JOB_NOT_FOUND', message: 'x' } }, 404))
    vi.stubGlobal('fetch', fetchMock)
    const source = apiSource('missing')
    const { result } = renderHook(() => useJobPolling(source))
    await flush()
    expect(result.current.error).toMatchObject({ code: 'JOB_NOT_FOUND' })
    await advance(10000)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('enters retry mode on network errors and leaves it when a request succeeds', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(makeJob()))
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockImplementation(async () => jsonResponse(makeJob()))
    vi.stubGlobal('fetch', fetchMock)
    const source = apiSource('job-1')
    const { result } = renderHook(() => useJobPolling(source))
    await flush()
    expect(result.current.connection).toBe('ok')

    await advance(3000) // next poll fails -> retrying
    expect(result.current.connection).toBe('retrying')
    expect(result.current.job.status).toBe('synthesizing') // the last good job stays visible
    await advance(1000) // retry 1 fails
    expect(result.current.connection).toBe('retrying')
    await advance(2000) // retry 2 succeeds
    expect(result.current.connection).toBe('ok')
    expect(fetchMock).toHaveBeenCalledTimes(4)
  })

  it('reports a lost connection after two minutes, then recovers', async () => {
    let up = false
    const fetchMock = vi.fn(async () => {
      if (!up) throw new TypeError('Failed to fetch')
      return jsonResponse(makeJob({ status: 'done' }))
    })
    vi.stubGlobal('fetch', fetchMock)
    const source = apiSource('job-1')
    const { result } = renderHook(() => useJobPolling(source))
    await flush()
    expect(result.current.connection).toBe('retrying')
    await advance(125000)
    expect(result.current.connection).toBe('lost')
    expect(result.current.job).toBeNull()

    up = true
    await advance(30000)
    expect(result.current.connection).toBe('ok')
    expect(result.current.job.status).toBe('done')
  })

  it('polls at once when the tab becomes visible again', async () => {
    const source = fakeSource(async () => makeJob())
    renderHook(() => useJobPolling(source))
    await flush()
    expect(source.getJob).toHaveBeenCalledTimes(1)
    vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible')
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'))
    })
    await flush()
    expect(source.getJob).toHaveBeenCalledTimes(2)
  })
})
