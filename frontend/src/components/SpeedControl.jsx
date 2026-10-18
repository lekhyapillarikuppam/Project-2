import { SPEEDS } from '../hooks/useAudioPlayer.js'

/** Playback speed (client-side playbackRate, D12). */
export default function SpeedControl({ value, onChange }) {
  return (
    <label className="speed-control">
      <span className="speed-control__label">Speed</span>
      <select aria-label="Playback speed" value={value} onChange={(event) => onChange(Number(event.target.value))}>
        {SPEEDS.map((speed) => (
          <option key={speed} value={speed}>
            {speed}×
          </option>
        ))}
      </select>
    </label>
  )
}
