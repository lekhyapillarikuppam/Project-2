import { useEffect } from 'react'
import { Link } from 'react-router-dom'
import { APP_NAME } from '../lib/brand.js'

export default function NotFoundPage() {
  useEffect(() => {
    document.title = `Page not found · ${APP_NAME}`
  }, [])
  return (
    <div className="message-page">
      <h1>Page not found</h1>
      <p>There is nothing at this address.</p>
      <Link to="/" className="button button--primary">
        Go to the start page
      </Link>
    </div>
  )
}
