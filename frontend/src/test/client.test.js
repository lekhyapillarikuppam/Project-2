import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, getJson, parseErrorBody } from '../api/client.js'
import { messageFor } from '../lib/errors.js'
import { jsonResponse } from './helpers.js'

const URL = 'http://api.test/api/jobs/abc'

describe('error shape', () => {
  it('turns {"error": {code, message}} into an ApiError', () => {
    const error = parseErrorBody({ error: { code: 'SCANNED_PDF', message: 'Scanned.' } }, 422)
    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ code: 'SCANNED_PDF', message: 'Scanned.', status: 422 })
  })

  it('falls back to HTTP_<status> for bodies without the shape', () => {
    expect(parseErrorBody(null, 500)).toMatchObject({ code: 'HTTP_500', status: 500 })
    expect(parseErrorBody({ detail: 'x' }, 400)).toMatchObject({ code: 'HTTP_400' })
  })

  it('maps codes to the SPEC §12 messages, with a fallback', () => {
    expect(messageFor('LEGACY_DOC')).toMatch(/Save As/)
    expect(messageFor('SOMETHING_NEW', 'Server says hi.')).toBe('Server says hi.')
    expect(messageFor('SOMETHING_NEW')).toBe('Something went wrong. Please try again.')
    expect(messageFor('FILE_TOO_LARGE', null, { maxFileMb: 40 })).toBe('This file is larger than 40 MB.')
  })
})

describe('getJson', () => {
  let fetchMock

  beforeEach(() => {
    vi.useFakeTimers()
    fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('returns the parsed JSON', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ status: 'done' }))
    await expect(getJson(URL)).resolves.toEqual({ status: 'done' })
  })

  it('throws the API error at once for a 404, without retrying', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ error: { code: 'JOB_NOT_FOUND', message: 'gone' } }, 404))
    await expect(getJson(URL)).rejects.toMatchObject({ code: 'JOB_NOT_FOUND', status: 404 })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('retries after a network failure and then succeeds', async () => {
    fetchMock
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce(new Response('bad gateway', { status: 502 }))
      .mockResolvedValueOnce(jsonResponse({ status: 'synthesizing' }))
    const onRetry = vi.fn()
    const promise = getJson(URL, { onRetry })
    await vi.advanceTimersByTimeAsync(1000)
    expect(fetchMock).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(2000)
    await expect(promise).resolves.toEqual({ status: 'synthesizing' })
    expect(fetchMock).toHaveBeenCalledTimes(3)
    expect(onRetry.mock.calls.map(([info]) => info.delayMs)).toEqual([1000, 2000])
  })

  it('gives up with NETWORK_ERROR after two minutes of failures', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'))
    const onRetry = vi.fn()
    const promise = getJson(URL, { onRetry })
    const settled = promise.catch((error) => error)
    await vi.advanceTimersByTimeAsync(119000)
    expect(onRetry).toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(5000)
    const error = await settled
    expect(error).toBeInstanceOf(ApiError)
    expect(error.code).toBe('NETWORK_ERROR')
    // 1, 2, 4, 8 s, then every 8 s, never waiting past 2 minutes in total
    const waited = onRetry.mock.calls.reduce((sum, [info]) => sum + info.delayMs, 0)
    expect(waited).toBeLessThanOrEqual(120000)
    expect(onRetry.mock.calls.slice(0, 5).map(([info]) => info.delayMs)).toEqual([1000, 2000, 4000, 8000, 8000])
    expect(fetchMock).toHaveBeenCalledTimes(onRetry.mock.calls.length + 1)
  })

  it('does not retry when retry is off', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'))
    await expect(getJson(URL, { retry: false })).rejects.toMatchObject({ code: 'NETWORK_ERROR' })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})
