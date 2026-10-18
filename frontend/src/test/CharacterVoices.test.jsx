// Character voices in the UI (SPEC §15.8): the upload choice, "Finding the characters…", the cast,
// per-chapter attribution notes, and the static characters sample.
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { sampleSource } from '../api/source.js'
import BookPage from '../pages/BookPage.jsx'
import UploadPage from '../pages/UploadPage.jsx'
import { FakeXHR, fakeSource, jsonResponse, makeJob } from './helpers.js'

const CONFIG = { max_file_mb: 40, max_pdf_pages: 200, max_words: 100000, accepted_types: ['.pdf', '.docx'] }
const CAST = [
  { name: 'Alice', aliases: [], gender: 'female', lines: 41, voice: 'en-GB-SoniaNeural', voice_label: 'Voice 1 (female, British)' },
  { name: 'Hatter', aliases: [], gender: 'male', lines: 1, voice: 'en-US-EricNeural', voice_label: 'Voice 24 (male, American)' },
]

function characterJob(overrides = {}) {
  const job = makeJob({ narration_mode: 'characters', cast: CAST, ...overrides })
  job.progress = { ...job.progress, windows_total: 12, windows_done: 2, ...overrides.progress }
  return job
}

async function renderUpload(config) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url) => (String(url).includes('/api/config') ? jsonResponse(config) : jsonResponse({ status: 'ok' }))),
  )
  render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route path="/" element={<UploadPage />} />
        <Route path="/book/:jobId" element={<p>Book page</p>} />
      </Routes>
    </MemoryRouter>,
  )
  await screen.findByText(/up to 40 MB/)
  return userEvent.setup({ applyAccept: false })
}

function renderBook(source) {
  return render(
    <MemoryRouter>
      <BookPage source={source} />
    </MemoryRouter>,
  )
}

describe('upload: narration mode', () => {
  beforeEach(() => {
    FakeXHR.instances = []
    vi.stubGlobal('XMLHttpRequest', FakeXHR)
  })

  afterEach(() => vi.unstubAllGlobals())

  it('is hidden when character voices are not available', async () => {
    await renderUpload({ ...CONFIG, character_voices_available: false })
    expect(screen.queryByRole('radio', { name: /character voices/i })).toBeNull()
  })

  it('defaults to single narrator and sends it', async () => {
    const user = await renderUpload({ ...CONFIG, character_voices_available: true })
    expect(screen.getByRole('radio', { name: 'Single narrator' })).toBeChecked()
    expect(screen.queryByText(/document’s text is sent/)).toBeNull()
    await user.upload(screen.getByTestId('file-input'), new File(['%PDF'], 'a.pdf'))
    expect(FakeXHR.instances[0].body.get('narration_mode')).toBe('single')
  })

  it('shows the privacy and timing notes for character voices and sends the mode', async () => {
    const user = await renderUpload({ ...CONFIG, character_voices_available: true })
    await user.click(screen.getByRole('radio', { name: 'Character voices (beta)' }))
    expect(
      screen.getByText('Uses an AI service (Groq) to work out who is speaking; the document’s text is sent to it.'),
    ).toBeInTheDocument()
    expect(screen.getByText(/take longer to prepare, especially for long books/)).toBeInTheDocument()
    await user.upload(screen.getByTestId('file-input'), new File(['%PDF'], 'a.pdf'))
    expect(FakeXHR.instances[0].body.get('narration_mode')).toBe('characters')
  })
})

describe('book page: character voices', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ status: 'ok', version: 'test' })))
  })

  afterEach(() => vi.unstubAllGlobals())

  it('shows "Finding the characters…" with the window count while attributing', async () => {
    const job = characterJob({ status: 'attributing', progress: { windows_done: 2, windows_total: 12 } })
    renderBook(fakeSource(async () => job))
    expect(await screen.findByText('Finding the characters… (window 3 of 12)')).toBeInTheDocument()
    expect(screen.getByText('Finding the characters', { selector: '.status-pill' })).toBeInTheDocument()
  })

  it('lists the cast with the narrator first, voice labels and lines', async () => {
    renderBook(fakeSource(async () => characterJob()))
    const cast = await screen.findByRole('region', { name: 'Cast' })
    const rows = within(cast).getAllByRole('listitem')
    expect(rows.map((r) => r.querySelector('.cast-list__name').textContent)).toEqual(['Narrator', 'Alice', 'Hatter'])
    expect(within(rows[1]).getByText('Voice 1 (female, British)')).toBeInTheDocument()
    expect(within(rows[1]).getByText('41 lines')).toBeInTheDocument()
    expect(within(rows[2]).getByText('1 line')).toBeInTheDocument()
  })

  it('has no cast section in single-narrator mode', async () => {
    renderBook(fakeSource(async () => makeJob({ narration_mode: 'single', cast: [] })))
    await screen.findByRole('heading', { name: 'Alice in Wonderland' })
    expect(screen.queryByRole('region', { name: 'Cast' })).toBeNull()
  })

  it('notes chapters whose speakers came from the rule-based fallback', async () => {
    const job = characterJob()
    job.chapters = job.chapters.map((c, i) => ({ ...c, attribution: ['llm', 'baseline', 'mixed', 'none'][i] }))
    renderBook(fakeSource(async () => job))
    await screen.findByRole('heading', { name: 'Alice in Wonderland' })
    const row = (title) => screen.getByText(title).closest('li')
    expect(within(row('Down the Rabbit-Hole')).queryByText(/rule-based/)).toBeNull()
    expect(within(row('The Pool of Tears')).getByText('Speakers found with the simpler rule-based method')).toBeInTheDocument()
    expect(within(row('A Caucus-Race')).getByText('Some speakers found with the simpler rule-based method')).toBeInTheDocument()
  })
})

describe('characters sample', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('reads from public/sample/characters and uses the chapter as the full book', () => {
    const source = sampleSource('/', { folder: 'sample/characters', id: 'sample-characters', fullFile: 'ch_007.mp3' })
    expect(source.audioUrl(7)).toBe('/sample/characters/ch_007.mp3')
    expect(source.fullDownloadUrl()).toBe('/sample/characters/ch_007.mp3')
    expect(source.id).toBe('sample-characters')
  })

  it('links between the two samples', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ status: 'ok' })))
    const single = fakeSource(async () => makeJob({ status: 'done', narration_mode: 'single' }), { kind: 'sample' })
    const { unmount } = renderBook(single)
    expect(await screen.findByRole('link', { name: /sample with character voices/ })).toHaveAttribute(
      'href',
      '/sample/characters',
    )
    unmount()
    renderBook(fakeSource(async () => characterJob({ status: 'done' }), { kind: 'sample' }))
    expect(await screen.findByRole('link', { name: 'Hear the single-narrator sample' })).toHaveAttribute('href', '/sample')
  })
})
