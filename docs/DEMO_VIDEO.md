# Demo video — shot list (~3½ minutes)

Record at 1280×800 or larger, browser zoom 100%, light mode, the Alice upload started on camera.
Close other tabs and notifications. Speak calmly; cut long waits in editing.

| # | Length | Screen | Action | Narration |
|---|---|---|---|---|
| 1 | 0:00–0:12 | Start page `https://audiobook-generator-lekhya.vercel.app` | Hold on the page; move the pointer over the drop zone | "This is Readaloud. It turns a PDF or Word document into an audiobook, split into chapters, using neural text-to-speech." |
| 2 | 0:12–0:30 | `/sample` | Click **Try a sample**, press play for ~5 s | "Here's a sample. You get a table of contents with each chapter's length, a player, and downloads. The sample is static, so it always works." |
| 3 | 0:30–0:50 | Start page | Drag `01_novel.pdf` onto the drop zone; the upload bar fills | "Now a real book: Alice in Wonderland as a PDF. The server checks the file, extracts the text with its layout, and removes page numbers, headers and footnotes." |
| 4 | 0:50–1:10 | Book page | Show *Reading your document and finding chapters…* then *Narrating chapter 1 of 12…* and the chapter list | "It found the twelve chapters. They are narrated in order, four sentence chunks at a time, with an estimate of the time left." |
| 5 | 1:10–1:30 | Book page | When chapter 1 says *ready*, press its play button | "Chapter 1 is ready after a few seconds, so I can start listening while the rest is still being narrated." |
| 6 | 1:30–1:50 | Player bar | Change speed to 1.5×, drag the seek bar, press → once; open the **?** shortcuts | "Speed changes happen in the browser and keep the pitch. Seeking works anywhere in the chapter, and there are keyboard shortcuts." |
| 7 | 1:50–2:05 | Chapter list | Click a chapter's download icon; show the file name in the downloads bar | "Each chapter downloads as an MP3 with a readable name. When everything is done, there's a full-book download too." |
| 8 | 2:05–2:20 | Start page (new tab) | Upload `08_scanned.pdf`; the red message appears | "A scanned PDF has no real text, so it's rejected with a clear message instead of producing silence." |
| 9 | 2:20–2:45 | GitHub README, Evaluation and Performance tables | Scroll slowly | "Chapter detection was evaluated on hand-labelled documents: 0.99 F1 on a held-out set I never looked at while building it. On a free Azure VM it narrates about 700 characters a second." |
| 10 | 2:45–3:00 | Back to the Alice tab | Show *Ready*, **Download full book**, total length 2:35:47 | "And the whole book is done: two and a half hours of audio in about three minutes." |
| 11 | 3:00–3:25 | `/sample/characters` | Click **Hear a sample with character voices** on the sample page; play from the start of the tea party for ~12 s, then scroll to the **Cast** list | "Version 2 adds character voices: an LLM works out who is speaking each line, and Alice, the Hatter, the March Hare and the Dormouse each get their own voice. Thanks for watching." |

Fallback: if the live upload is slow on the day, record shots 3–7 against `npm run dev` locally and
say so in the narration, or cut from shot 5 to a finished book uploaded earlier.
