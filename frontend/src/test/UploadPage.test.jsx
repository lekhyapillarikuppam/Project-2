import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MESSAGES } from '../lib/errors.js'
import UploadPage from '../pages/UploadPage.jsx'
import { FakeXHR, jsonResponse } from './helpers.js'

const CONFIG = { max_file_mb: 40, max_pdf_pages: 200, max_words: 100000, accepted_types: ['.pdf', '.docx'] }

async function renderPage() {
  render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route path="/" element={<UploadPage />} />
        <Route path="/book/:jobId" element={<p>Book page</p>} />
      </Routes>
    </MemoryRouter>,
  )
  // limits from /api/config are shown once loaded
  await screen.findByText(/up to 40 MB/)
  return userEvent.setup({ applyAccept: false })
}

function fileOf(name, size) {
  const file = new File(['%PDF-1.7'], name)
  if (size != null) Object.defineProperty(file, 'size', { value: size })
  return file
}

describe('UploadPage', () => {
  beforeEach(() => {
    FakeXHR.instances = []
    vi.stubGlobal('XMLHttpRequest', FakeXHR)
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url) => (String(url).includes('/api/config') ? jsonResponse(CONFIG) : jsonResponse({ status: 'ok' }))),
    )
  })

  afterEach(() => vi.unstubAllGlobals())

  it('shows the limits, the dropzone and the sample link', async () => {
    await renderPage()
    expect(screen.getByRole('button', { name: /drop a pdf or docx here/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Try a sample' })).toHaveAttribute('href', '/sample')
    expect(screen.getByText(/PDFs up to 200 pages/)).toBeInTheDocument()
  })

  it('rejects a legacy .doc file with the LEGACY_DOC message', async () => {
    const user = await renderPage()
    await user.upload(screen.getByTestId('file-input'), fileOf('report.doc'))
    expect(screen.getByRole('alert')).toHaveTextContent(MESSAGES.LEGACY_DOC)
    expect(FakeXHR.instances).toHaveLength(0)
  })

  it('rejects other file types', async () => {
    const user = await renderPage()
    await user.upload(screen.getByTestId('file-input'), fileOf('notes.txt'))
    expect(screen.getByRole('alert')).toHaveTextContent(MESSAGES.UNSUPPORTED_TYPE)
    expect(FakeXHR.instances).toHaveLength(0)
  })

  it('rejects files over the configured size', async () => {
    const user = await renderPage()
    await user.upload(screen.getByTestId('file-input'), fileOf('big.pdf', 41 * 1024 * 1024))
    expect(screen.getByRole('alert')).toHaveTextContent('This file is larger than 40 MB.')
    expect(FakeXHR.instances).toHaveLength(0)
  })

  it('shows the server message for a 422 SCANNED_PDF and stays on the page', async () => {
    const user = await renderPage()
    await user.upload(screen.getByTestId('file-input'), fileOf('scan.pdf', 1000))
    expect(FakeXHR.instances).toHaveLength(1)
    const xhr = FakeXHR.instances[0]
    expect(xhr.method).toBe('POST')
    expect(xhr.url).toMatch(/\/api\/upload$/)
    expect(xhr.body.get('file').name).toBe('scan.pdf')
    expect(screen.getByText(/Uploading/)).toBeInTheDocument()

    xhr.respond(422, { error: { code: 'SCANNED_PDF', message: 'scanned (server text)' } })
    expect(await screen.findByRole('alert')).toHaveTextContent(MESSAGES.SCANNED_PDF)
    expect(screen.getByRole('button', { name: /drop a pdf or docx here/i })).toBeInTheDocument()
  })

  it('shows the RATE_LIMITED message for a 429', async () => {
    const user = await renderPage()
    await user.upload(screen.getByTestId('file-input'), fileOf('eleventh.pdf', 1000))
    FakeXHR.instances[0].respond(429, { error: { code: 'RATE_LIMITED', message: 'too many' } })
    expect(await screen.findByRole('alert')).toHaveTextContent(MESSAGES.RATE_LIMITED)
    expect(MESSAGES.RATE_LIMITED).toBe("You've uploaded a lot of documents in the last hour. Please try again later.")
  })

  it('opens the book page after a 202', async () => {
    const user = await renderPage()
    await user.upload(screen.getByTestId('file-input'), fileOf('alice.pdf', 1000))
    FakeXHR.instances[0].respond(202, { job_id: 'abc-123', status: 'queued' })
    expect(await screen.findByText('Book page')).toBeInTheDocument()
  })
})
