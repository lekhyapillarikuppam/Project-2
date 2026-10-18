import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/client.js'
import { MESSAGES } from '../lib/errors.js'
import BookPage from '../pages/BookPage.jsx'
import { fakeSource, jsonResponse, makeJob } from './helpers.js'

function renderBook(source) {
  return render(
    <MemoryRouter>
      <BookPage source={source} />
    </MemoryRouter>,
  )
}

function rowFor(title) {
  return screen.getByText(title).closest('li')
}

describe('BookPage', () => {
  beforeEach(() => {
    // the connection check
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ status: 'ok', version: 'test' })))
  })

  afterEach(() => vi.unstubAllGlobals())

  it('lists the chapters with their statuses; only ready chapters can be played or downloaded', async () => {
    const job = makeJob()
    job.chapters[3] = { ...job.chapters[3], status: 'failed', error: 'TTS_FAILED' }
    renderBook(fakeSource(async () => job))

    expect(await screen.findByRole('heading', { name: 'Alice in Wonderland' })).toBeInTheDocument()
    expect(screen.getByText('Narrating chapter 2 of 4…')).toBeInTheDocument()

    const ready = rowFor('Down the Rabbit-Hole')
    expect(within(ready).getByText('ready')).toBeInTheDocument()
    expect(within(ready).getByText('11:30')).toBeInTheDocument()
    expect(within(ready).getByRole('button', { name: /play chapter 1/i })).toBeEnabled()
    expect(within(ready).getByRole('link', { name: /download chapter 1/i })).toHaveAttribute(
      'href',
      'http://audio.test/download/1',
    )

    const generating = rowFor('The Pool of Tears')
    expect(within(generating).getByText('narrating')).toBeInTheDocument()
    expect(within(generating).getByRole('button', { name: /play chapter 2/i })).toBeDisabled()
    expect(within(generating).queryByRole('link')).toBeNull()

    const pending = rowFor('A Caucus-Race')
    expect(within(pending).getByText('waiting')).toBeInTheDocument()
    expect(within(pending).getByRole('button', { name: /play chapter 3/i })).toBeDisabled()

    const failed = rowFor('The Rabbit Sends in a Little Bill')
    expect(within(failed).getByText('failed')).toBeInTheDocument()
    expect(within(failed).getByText('Narration failed for this chapter')).toBeInTheDocument()
    expect(within(failed).getByRole('button', { name: /play chapter 4/i })).toBeDisabled()

    expect(screen.queryByRole('link', { name: 'Download full book' })).toBeNull()
    expect(screen.getByText(/Audio is kept for a limited time/)).toBeInTheDocument()
  })

  it('offers the full book when done', async () => {
    const job = makeJob({ status: 'done' })
    job.chapters = job.chapters.map((c) => ({ ...c, status: 'ready', duration_seconds: 60 }))
    renderBook(fakeSource(async () => job))
    const link = await screen.findByRole('link', { name: 'Download full book' })
    expect(link).toHaveAttribute('href', 'http://audio.test/download/full')
    expect(screen.queryByText(/chapters? failed\)/)).toBeNull()
    expect(screen.queryByRole('progressbar')).toBeNull()
  })

  it('offers the full book when partial, with the number of failed chapters', async () => {
    const job = makeJob({ status: 'partial' })
    job.chapters = job.chapters.map((c, i) => ({ ...c, status: i === 2 ? 'failed' : 'ready', duration_seconds: 60 }))
    renderBook(fakeSource(async () => job))
    expect(await screen.findByRole('link', { name: 'Download full book' })).toBeInTheDocument()
    expect(screen.getByText('(1 chapter failed)')).toBeInTheDocument()
  })

  it.each([
    ['JOB_NOT_FOUND', 404],
    ['JOB_EXPIRED', 410],
  ])('shows the not-available screen for %s', async (code, status) => {
    renderBook(fakeSource(async () => Promise.reject(new ApiError(code, 'gone', status))))
    expect(await screen.findByRole('heading', { name: 'No longer available' })).toBeInTheDocument()
    expect(screen.getByText(MESSAGES.JOB_NOT_FOUND)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Upload a document' })).toHaveAttribute('href', '/')
  })

  it("shows a failed job's error with an upload-again button", async () => {
    const job = makeJob({
      status: 'failed',
      error: { code: 'TOO_MANY_WORDS', message: 'too long' },
      chapters: [],
      progress: { chapters_total: 0, chapters_done: 0, chars_total: 0, chars_done: 0, eta_seconds: null },
    })
    renderBook(fakeSource(async () => job))
    expect(await screen.findByRole('alert')).toHaveTextContent(MESSAGES.TOO_MANY_WORDS)
    expect(screen.getByRole('link', { name: 'Upload another document' })).toHaveAttribute('href', '/')
  })

  it('shows the job warnings quietly', async () => {
    renderBook(fakeSource(async () => makeJob({ warnings: ['2 image-only pages were skipped'] })))
    expect(await screen.findByText('2 image-only pages were skipped')).toBeInTheDocument()
  })
})
