import '../styles/Notice.css'

/**
 * A quiet message box. tone: 'info' (default), 'warning' or 'error'.
 * Errors are announced (role="alert"); the others are plain content.
 */
export default function Notice({ tone = 'info', title, children, actions, className = '' }) {
  return (
    <div className={`notice notice--${tone} ${className}`.trim()} role={tone === 'error' ? 'alert' : undefined}>
      <div className="notice__body">
        {title && <p className="notice__title">{title}</p>}
        {children}
      </div>
      {actions && <div className="notice__actions">{actions}</div>}
    </div>
  )
}
