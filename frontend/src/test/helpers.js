import { vi } from 'vitest'

export function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

export function makeJob(overrides = {}) {
  return {
    job_id: 'job-1',
    status: 'synthesizing',
    created_at: '2026-10-18T10:00:00Z',
    expires_at: '2026-10-19T10:00:00Z',
    filename: 'Alice in Wonderland.pdf',
    error: null,
    warnings: [],
    progress: { chapters_total: 4, chapters_done: 1, chars_total: 4000, chars_done: 1500, eta_seconds: 240 },
    chapters: [
      { index: 1, title: 'Down the Rabbit-Hole', status: 'ready', duration_seconds: 690.5, error: null },
      { index: 2, title: 'The Pool of Tears', status: 'generating', duration_seconds: null, error: null },
      { index: 3, title: 'A Caucus-Race', status: 'pending', duration_seconds: null, error: null },
      { index: 4, title: 'The Rabbit Sends in a Little Bill', status: 'pending', duration_seconds: null, error: null },
    ],
    engine_used: 'edge',
    ...overrides,
  }
}

/** A BookSource whose getJob is a mock. */
export function fakeSource(getJob, overrides = {}) {
  return {
    kind: 'api',
    id: 'job-1',
    live: true,
    getJob: vi.fn(getJob),
    audioUrl: (n) => `http://audio.test/ch_${n}.mp3`,
    downloadUrl: (n) => `http://audio.test/download/${n}`,
    fullDownloadUrl: () => 'http://audio.test/download/full',
    downloadName: () => undefined,
    ...overrides,
  }
}

/** Minimal XMLHttpRequest stand-in; the test answers with respond(). */
export class FakeXHR {
  static instances = []

  constructor() {
    this.upload = {}
    this.status = 0
    this.responseText = ''
    this.headers = {}
    FakeXHR.instances.push(this)
  }

  open(method, url) {
    this.method = method
    this.url = url
  }

  setRequestHeader(name, value) {
    this.headers[name] = value
  }

  send(body) {
    this.body = body
  }

  abort() {
    this.onabort?.()
  }

  respond(status, body) {
    this.upload.onprogress?.({ lengthComputable: true, loaded: 10, total: 10 })
    this.upload.onload?.()
    this.status = status
    this.responseText = JSON.stringify(body)
    this.onload?.()
  }
}
