import { useMemo } from 'react'
import { Link, Route, Routes, useParams } from 'react-router-dom'
import { apiSource, sampleSource } from './api/source.js'
import { APP_NAME, TAGLINE } from './lib/brand.js'
import BookPage from './pages/BookPage.jsx'
import NotFoundPage from './pages/NotFoundPage.jsx'
import UploadPage from './pages/UploadPage.jsx'

const SAMPLE = sampleSource()
// Character voices (SPEC §15.8): Alice ch. 7 in characters mode; one chapter, so no separate full.mp3.
const CHARACTERS_SAMPLE = sampleSource(undefined, {
  folder: 'sample/characters',
  id: 'sample-characters',
  fullFile: 'ch_007.mp3',
})

function JobRoute() {
  const { jobId } = useParams()
  const source = useMemo(() => apiSource(jobId), [jobId])
  return <BookPage key={jobId} source={source} />
}

export default function App() {
  return (
    <div className="app">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <header className="site-header">
        <Link to="/" className="site-header__brand">
          <svg className="site-header__logo" viewBox="0 0 32 32" aria-hidden="true">
            <path d="M6 8.5c3.2-1.1 6.6-.8 9 1.2v15.8c-2.4-1.7-5.8-2.1-9-1.2zM26 8.5c-3.2-1.1-6.6-.8-9 1.2v15.8c2.4-1.7 5.8-2.1 9-1.2z" />
          </svg>
          <span className="site-header__name">{APP_NAME}</span>
        </Link>
        <span className="site-header__tagline">{TAGLINE}</span>
      </header>
      <main id="main" className="site-main" tabIndex={-1}>
        <Routes>
          <Route path="/" element={<UploadPage />} />
          <Route path="/book/:jobId" element={<JobRoute />} />
          <Route path="/sample" element={<BookPage key="sample" source={SAMPLE} />} />
          <Route path="/sample/characters" element={<BookPage key="sample-characters" source={CHARACTERS_SAMPLE} />} />
          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      </main>
      <footer className="site-footer">
        <p>Free and temporary: no account, nothing is kept for long. English documents only.</p>
      </footer>
    </div>
  )
}
