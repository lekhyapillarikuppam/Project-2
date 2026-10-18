import { useRef, useState } from 'react'
import '../styles/Dropzone.css'

/**
 * Drag-and-drop area that is also a button: click, Enter or Space opens the file picker.
 * onFile(file) gets the first chosen or dropped file.
 */
export default function Dropzone({ onFile, accept, disabled = false, hint }) {
  const inputRef = useRef(null)
  const [dragging, setDragging] = useState(false)

  function choose(files) {
    const file = files?.[0]
    if (file) onFile(file)
  }

  function onDragOver(event) {
    event.preventDefault()
    if (disabled) {
      event.dataTransfer.dropEffect = 'none'
      return
    }
    event.dataTransfer.dropEffect = 'copy'
    setDragging(true)
  }

  function onDrop(event) {
    event.preventDefault()
    setDragging(false)
    if (!disabled) choose(event.dataTransfer?.files)
  }

  return (
    <div className="dropzone-wrap">
      <button
        type="button"
        className={`dropzone${dragging ? ' dropzone--dragging' : ''}`}
        onClick={() => inputRef.current?.click()}
        onDragEnter={onDragOver}
        onDragOver={onDragOver}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        disabled={disabled}
        aria-describedby={hint ? 'dropzone-hint' : undefined}
      >
        <svg className="dropzone__icon" viewBox="0 0 24 24" aria-hidden="true">
          <path d="M12 16V4m0 0-4.5 4.5M12 4l4.5 4.5M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3" />
        </svg>
        <span className="dropzone__title">{dragging ? 'Drop it here' : 'Drop a PDF or DOCX here'}</span>
        <span className="dropzone__sub">or click to choose a file</span>
      </button>
      {hint && (
        <p id="dropzone-hint" className="dropzone__hint">
          {hint}
        </p>
      )}
      <input
        ref={inputRef}
        className="visually-hidden"
        type="file"
        accept={accept}
        tabIndex={-1}
        aria-label="Choose a document"
        data-testid="file-input"
        onChange={(event) => {
          choose(event.target.files)
          event.target.value = ''
        }}
      />
    </div>
  )
}
