// User-facing messages for API error codes (SPEC §12), plus the client-side network error.

const NO_LONGER_AVAILABLE =
  'This audiobook is no longer available (audio is temporary). Please upload the document again.'

export const MESSAGES = {
  UNSUPPORTED_TYPE: 'Only PDF and DOCX files are supported.',
  LEGACY_DOC: 'Old Word (.doc) files aren\'t supported. Open it in Word and "Save As" .docx, then try again.',
  FILE_TOO_LARGE: 'This file is larger than 50 MB.',
  TOO_MANY_PAGES: 'This PDF has more than 200 pages. Please upload a shorter document.',
  TOO_MANY_WORDS: 'This document is too long (over 100,000 words). Please upload a shorter document.',
  CORRUPT_FILE: "This file couldn't be opened. It may be damaged.",
  ENCRYPTED_FILE: 'This PDF is password-protected. Remove the password and try again.',
  SCANNED_PDF: "This PDF looks like scanned images with no selectable text. Scanned documents aren't supported.",
  EMPTY_DOCUMENT: 'No readable text was found in this document.',
  SERVER_BUSY: 'The server is busy converting other documents. Please try again in a few minutes.',
  JOB_NOT_FOUND: NO_LONGER_AVAILABLE,
  JOB_EXPIRED: NO_LONGER_AVAILABLE,
  CHAPTER_NOT_READY: 'This chapter is still being generated.',
  TTS_FAILED: 'Narration failed for this chapter.',
  NETWORK_ERROR: "Can't reach the server right now. Please check your connection and try again.",
}

export const GENERIC_MESSAGE = 'Something went wrong. Please try again.'

export const UNAVAILABLE_CODES = new Set(['JOB_NOT_FOUND', 'JOB_EXPIRED'])

/**
 * The message to show for an error code. Known codes use the catalogue (with the configured limits
 * where they appear); unknown codes use the server's own message, then a generic one.
 */
export function messageFor(code, serverMessage, limits = {}) {
  if (code === 'FILE_TOO_LARGE' && limits.maxFileMb) return `This file is larger than ${limits.maxFileMb} MB.`
  if (code === 'TOO_MANY_PAGES' && limits.maxPdfPages)
    return `This PDF has more than ${limits.maxPdfPages} pages. Please upload a shorter document.`
  return MESSAGES[code] ?? serverMessage ?? GENERIC_MESSAGE
}
