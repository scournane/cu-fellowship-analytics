import {radiusVars} from '@astryxdesign/core/theme/tokens.stylex'
import * as stylex from '@stylexjs/stylex'

/** The mark, served by FastAPI out of `src/cufa/console/static/`.
 *
 *  The same file the page links as its favicon — one drawing, two uses, and
 *  nothing fetched from anywhere but this server. It is a vector, so a header
 *  at 40px and an empty state at 160px are both drawn at the size they are
 *  asked for rather than resampled from a raster. */
const DING = '/static/brand/ding.svg'

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
// the file arrives, and it keeps a pixel count out of the stylesheet. The one
// thing that is styled is the corner, and it is styled with the theme's
// container radius, so the mascot rounds off by exactly as much as a Card does.
const styles = stylex.create({
  mascot: {
    display: 'block',
    borderRadius: radiusVars['--radius-container'],
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
