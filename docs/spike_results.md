# Render deployment spike: results (Phase 2b)

Question (SPEC §14, "Early deployment check"): on Render's free tier, (a) does edge-tts work from
Render's network, (b) can Piper run inside 512 MB and how fast, (c) how much memory does the
Phase 1 pipeline need for a 200-page PDF?

- **Render**: free tier, Singapore, 2026-10-17, Docker image from `backend/Dockerfile`
  (python:3.11-slim, one uvicorn worker). Each measurement ran in its own subprocess
  (`python -m spike.run …`) started by `GET /spike/{name}`, so its peak memory is its own.
- **Local**: Windows 11 laptop, Python 3.11.9 (Phase 2b, 2026-10-16), plus a local Docker
  container limited to 512 MB as a Linux check.
- Text for the TTS measurements: the first 3,348 characters (normalized) of *Alice*, chapter 1.
- The spike code (`backend/spike/`) was removed in Phase 3; it is in commit `d7c2675`.

## Measurements

| Measurement | Render free (512 MB) | Local Windows | Local Docker, 512 MB |
|---|---|---|---|
| Container limit / idle usage | cgroup v2, **512 MB**; idle **147.9 MB** (server process peak 93.2 MB) | – | cgroup v2, 512 MB; idle 105 MB |
| edge-tts, 3,348 chars | **ok**: 14.61 s = **229.2 chars/s**, 203.7 s audio, peak **97.7 MB** | ok: 640.6 chars/s, peak 84 MB | ok: 518–574 chars/s, peak 100 MB |
| Extract 200-page PDF (A5, 11 pt; 74,235 words, 35 chapters) | **ok**: 7.78 s (validate 1.38, extract 4.00, clean 2.00, structure 0.39), peak **103.4 MB** | ok: 2.03 s, peak 91 MB | ok: 1.8 s, peak 107 MB |
| Piper `low`, arena off, 300-char pieces | **crashed**: "Ran out of memory (used over 512MB)", instance restarted | ok: 237 chars/s (synthesis only), peak 391 MB | with swap: 181 chars/s, 566 MB RSS; without swap: **killed** |
| Piper `medium`, arena off | **crashed** (out of memory, restart) | – | with swap: 136 chars/s, 566 MB; without swap: **killed** |
| Piper `medium`, arena on | **crashed** (out of memory, restart) | ok: 280 chars/s, peak 592 MB | with swap: 237 chars/s, 564 MB; without swap: **killed** |

On Render every Piper run ended with the event "Ran out of memory (used over 512MB)" and an
instance restart (4 restarts in total). The two medium runs were started 4 s apart and overlapped,
which made that crash worse, but the low/arena-off run crashed on its own.

### Local Piper memory controls (Phase 2b step 1)
`narrate.py --engine piper --chapters 1` on `01_novel.pdf` (10,844 chars), `PIPER_MAX_CHARS=300`,
a fresh process each, Windows:

| Model | Memory arena | Peak memory | chars/s (incl. ~7 s model load) |
|---|---|---|---|
| medium | on | 831 MB | 246.9 |
| medium | off | 460 MB | 180.2 |
| low | on | 595 MB | 278.3 |
| low | off | **395 MB** | 214.7 |

For comparison, Phase 2 (medium, arena on, no splitting) peaked at 997 MB. The arena and splitting
controls more than halve Piper's peak on Windows, but on Linux the same process peaks about 170 MB
higher, and the server itself already uses about 150 MB on Render. So Piper does not fit.

## Recommendations (recorded as D26, D27 and SPEC rev 5 §6.7, §6.8, §14)

- **edge-tts is usable from Render**: no errors, 229 chars/s with one stream, under 100 MB.
- **Piper: no variant fits** under ~450 MB together with the app. On Render the fallback is turned
  off (`TTS_FALLBACK=none`, D26) and edge-tts is retried more patiently: `TTS_MAX_RETRIES=5`, with
  backoff 1, 2, 4, 8 s. After 5 consecutive failed chunks the narration service counts as
  unavailable: the remaining chapters fail with `TTS_FAILED`. Piper stays available for local
  use (`TTS_FALLBACK=piper`, `PIPER_DISABLE_ARENA=true`, `PIPER_MAX_CHARS=300`), and the Docker
  image leaves the voices out by default (`--build-arg INCLUDE_PIPER=false`).
- **No silent gaps** (D27): a chunk that fails on every available engine fails its whole chapter.
- **Extraction of a 200-page PDF** peaks at about 103 MB, which is well within the limit.
- **Settings**: `TTS_CONCURRENCY=4` (measured in Phase 3); Piper settings only matter locally.
- **Limits in SPEC §4 stay as they are** (200 pages / 100,000 words / 50 MB). Estimate for the
  largest allowed book: 100,000 words ≈ 537,000 chars (5.37 chars/word in *Alice*) ≈ **39 min**
  at 229 chars/s with a single stream. Concurrency 4 should do better; Phase 3 measures it on
  Render.
