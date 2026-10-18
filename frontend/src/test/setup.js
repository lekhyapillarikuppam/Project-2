import * as matchers from '@testing-library/jest-dom/matchers'
import { cleanup } from '@testing-library/react'
import { afterEach, expect } from 'vitest'

expect.extend(matchers)

// jsdom has no media playback: play/pause/load throw "not implemented". Simulate the state and events.
Object.defineProperties(HTMLMediaElement.prototype, {
  paused: {
    configurable: true,
    get() {
      return this._testPaused ?? true
    },
  },
  play: {
    configurable: true,
    value() {
      this._testPaused = false
      this.dispatchEvent(new Event('play'))
      return Promise.resolve()
    },
  },
  pause: {
    configurable: true,
    value() {
      if (this._testPaused === false) {
        this._testPaused = true
        this.dispatchEvent(new Event('pause'))
      }
    },
  },
  load: {
    configurable: true,
    value() {},
  },
})

afterEach(() => {
  cleanup()
  window.localStorage.clear()
})
