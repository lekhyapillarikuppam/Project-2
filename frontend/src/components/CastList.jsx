import '../styles/CastList.css'

/** The cast of a character-voices book (SPEC §15.8): the narrator first, then each character. */
export default function CastList({ cast }) {
  if (!cast?.length) return null
  return (
    <section className="cast-list" aria-labelledby="cast-heading">
      <h2 id="cast-heading" className="cast-list__heading">
        Cast
      </h2>
      <ul className="cast-list__items">
        <li className="cast-list__item">
          <span className="cast-list__name">Narrator</span>
          <span className="cast-list__voice">Narrator voice</span>
          <span className="cast-list__lines" />
        </li>
        {cast.map((member) => (
          <li key={member.name} className="cast-list__item">
            <span className="cast-list__name">{member.name}</span>
            <span className="cast-list__voice">{member.voice_label}</span>
            <span className="cast-list__lines">
              {member.lines} {member.lines === 1 ? 'line' : 'lines'}
            </span>
          </li>
        ))}
      </ul>
    </section>
  )
}
