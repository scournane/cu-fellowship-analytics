import * as stylex from '@stylexjs/stylex'

/** The mark, served by FastAPI out of `src/cufa/console/static/`.
 *
 *  Not the favicon. The favicon (`ding.svg`) is the same bell painted onto a
 *  filled teal square, which is what a browser tab needs and exactly what a
 *  card does not: dropped into an empty state it reads as a sticker somebody
 *  stuck on the page. This is the same drawing on nothing — the on-light mark
 *  the brand set already carried and that nothing was using — so the bell sits
 *  on the card's own white and belongs to the page.
 *
 *  It is a vector, so a header at 40px and an empty state at 160px are both
 *  drawn at the size they are asked for rather than resampled from a raster. */
const DING = '/static/brand/ding-mark.svg'

/** What it is, for anyone who cannot see it. Not "logo": the bell is a
 *  character with a name, and "Ding" is the word the rest of the product uses
 *  for it. */
const ALT = 'Ding, the smiling desk bell that is this console’s mascot'

// Astryx has no image component to lean on here — the two that hold a picture
// are Avatar, whose own guidance says not to use it for a logo or anything that
// is not a person, and Thumbnail, which is an upload preview with a remove
// button. The system's illustration guidance (`astryx docs illustrations`) says
// to place a plain <img> inside its layout components, which is what this is.
//
// So the element is a bare <img>, sized by its width/height ATTRIBUTES rather
// than by CSS — that is the markup's own sizing, it reserves the space before
// the file arrives, and it keeps a pixel count out of the stylesheet. Nothing
// else is styled: the mark is drawn on nothing, so a corner radius would round
// off transparent pixels and clip the sound arcs that reach the top edge.
const styles = stylex.create({
  mascot: {
    display: 'block',
  },
})

/**
 * Ding, at a size.
 *
 * Empty states and the fellow's page header. Astryx's illustration guidance
 * puts these between 120 and 240px on a full-page empty state, and the default
 * here is the small end of that; a header or an inline use wants 40–64.
 *
 * @param {object} props
 * @param {number} [props.size] Edge length in CSS pixels; the mark is square.
 * @param {string} [props.alt] Override the description. Pass `alt=""` where the
 *   bell is decoration beside text that already says the same thing — an empty
 *   state whose heading carries the message — and it drops out of the
 *   accessibility tree instead of being read as a second title.
 */
export function Mascot({size = 120, alt = ALT}) {
  return (
    <img
      src={DING}
      alt={alt}
      width={size}
      height={size}
      {...stylex.props(styles.mascot)}
    />
  )
}
