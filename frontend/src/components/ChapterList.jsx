import ChapterRow from './ChapterRow.jsx'
import '../styles/ChapterList.css'

/** The book's chapters, read like a table of contents. */
export default function ChapterList({ job, source, player }) {
  if (!job.chapters?.length) return null
  return (
    <section className="chapter-list" aria-labelledby="chapters-heading">
      <h2 id="chapters-heading" className="chapter-list__heading">
        Contents
      </h2>
      <ol className="chapter-list__items">
        {job.chapters.map((chapter, i) => (
          <ChapterRow
            key={chapter.index}
            chapter={chapter}
            number={i + 1}
            isCurrent={player.currentIndex === chapter.index}
            isPlaying={player.currentIndex === chapter.index && player.playing}
            onPlay={player.playChapter}
            downloadHref={source.downloadUrl(chapter.index)}
            downloadName={source.downloadName(job, chapter.index)}
          />
        ))}
      </ol>
    </section>
  )
}
