/**
 * Classroom — the console's theme.
 *
 * Built to the style reference the team supplied: white paper canvas, one
 * saturated green that reads as "correct answer", calm mid-gray body copy,
 * and chunky 12px-rounded components with 2px borders so every control looks
 * like a sticker pressed onto the page.
 *
 * Everything here is a token or a component override, which is the only place
 * brand belongs (see frontend/AGENTS.md). No screen sets a colour or a radius
 * of its own, so the look changes here and nowhere else.
 *
 * Three rules from the reference that explain choices which would otherwise
 * look arbitrary:
 *
 *   * Green is for progress: CTA fills, section headings, the funnel bars. It
 *     never carries small UI text, and it is never the colour of a link.
 *   * Blue is the link colour, and the outline on a secondary button. Nothing
 *     else.
 *   * No gradients, no shadows, no glass. Surfaces are flat fills with borders,
 *     so every `--shadow-*` here is `none` on purpose rather than by omission.
 *
 * Light only. The reference is a light design, and a dark scheme derived from
 * it would be a different design rather than this one after dark — so
 * `main.jsx` pins `mode="light"` and every token below is a single value.
 *
 * This is the SOURCE. `npm run theme` compiles it to `classroom.js` and
 * `classroom.css`, which are what the app imports; edit this file, never those.
 */

import {defineTheme} from '@astryxdesign/core/theme'
import {neutralTheme} from '@astryxdesign/theme-neutral'

import {classroomIcons} from './icons.js'

// Straight from the reference's token table.
const PAPER_WHITE = '#ffffff'
const EAGER_GREEN = '#58cc02'
const STORYBOOK_GREEN = '#d7ffb8'
const FRESH_LEAF = '#a5ed6e'
const SPARK_BLUE = '#1cb0f6'
const CHARCOAL = '#4b4b4b'
const PENCIL_GRAY = '#777777'
const FADED_GRAY = '#afafaf'

// Derived, and marked as such. The reference gives one border colour, #afafaf,
// and specifies it for buttons and pills. Ruling every table row and card at
// that weight would draw the eye to the furniture instead of the data, so
// surfaces get a lighter step of the same neutral and #afafaf stays on the
// controls it was specified for.
const RULE_GRAY = '#e5e5e5'
const WASH_GRAY = '#f7f7f7'

// The reference has no error or warning colour — its palette came from a
// marketing page, and this is a console that has to say "that did not work".
// These are flat and saturated so they sit beside the green rather than under
// it.
const BEE_YELLOW = '#ffc800'
const CARDINAL_RED = '#ff4b4b'

// Ink that stays readable on each pale wash: every pair below clears 4.5:1 on
// its own background.
const DEEP_GREEN = '#3d7000'
const DEEP_BLUE = '#00679c'
const DEEP_AMBER = '#8a5200'
const DEEP_RED = '#a62828'

// Nav labels, table headings and button text: uppercase at the reference's
// tracking. Labels only — never body copy.
const LABEL_TRACKING = '0.053em'
// The reference sets nav labels at 15px. This console carries ten of them
// rather than the reference's three, and 15px uppercase does not fit across a
// 1280-wide laptop, so they run one step down.
const NAV_LABEL_SIZE = '14px'

export const classroomTheme = defineTheme({
  name: 'classroom',
  extends: neutralTheme,

  typography: {
    // 17px body, as the reference sets it. The ratio puts the page title at
    // ~33px and section headings at ~27px, which is the 19–32px band it asks
    // for; the 48–64px display sizes are a marketing voice this console has no
    // use for.
    scale: {base: 17, ratio: 1.25},
    // Nunito for headings — the rounded face the reference names as the
    // substitute for Feather — and Nunito Sans for reading. Both are bundled
    // from node_modules by the build, so nothing is fetched at page load,
    // which is the promise the rest of the bundle already makes.
    heading: {
      family: 'Nunito Variable',
      fallbacks: 'Nunito, ui-rounded, "SF Pro Rounded", system-ui, sans-serif',
      weight: 800,
    },
    body: {
      family: 'Nunito Sans Variable',
      fallbacks: '"Nunito Sans", "Segoe UI", system-ui, -apple-system, sans-serif',
      weight: 500,
    },
  },

  radius: {base: 4, multiplier: 1},

  tokens: {
    // ---- canvas and ink ------------------------------------------------
    '--color-background-body': PAPER_WHITE,
    '--color-background-surface': PAPER_WHITE,
    '--color-background-card': PAPER_WHITE,
    '--color-background-popover': PAPER_WHITE,
    '--color-background-muted': WASH_GRAY,
    '--color-background-inverted': CHARCOAL,
    '--color-text-primary': CHARCOAL,
    '--color-text-secondary': PENCIL_GRAY,
    '--color-text-disabled': FADED_GRAY,
    '--color-on-dark': PAPER_WHITE,
    '--color-on-light': CHARCOAL,
    '--color-icon-primary': CHARCOAL,
    '--color-icon-secondary': PENCIL_GRAY,
    '--color-icon-disabled': FADED_GRAY,
    '--color-border': RULE_GRAY,
    '--color-border-emphasized': FADED_GRAY,
    '--color-skeleton': '#efefef',

    // ---- green for progress, blue for links ----------------------------
    '--color-accent': EAGER_GREEN,
    '--color-accent-muted': STORYBOOK_GREEN,
    '--color-on-accent': PAPER_WHITE,
    // Deliberately not the accent: the reference gives links their own blue,
    // and green on small text is the one thing it says never to do.
    '--color-text-accent': SPARK_BLUE,
    '--color-icon-accent': SPARK_BLUE,
    '--color-neutral': '#4b4b4b0d',
    '--color-overlay': '#4b4b4b80',
    '--color-overlay-hover': '#58cc0214',
    '--color-overlay-pressed': '#58cc0229',
    '--color-tint-hover': EAGER_GREEN,

    // ---- outcomes ------------------------------------------------------
    '--color-success': EAGER_GREEN,
    '--color-success-muted': STORYBOOK_GREEN,
    '--color-on-success': DEEP_GREEN,
    '--color-warning': BEE_YELLOW,
    '--color-warning-muted': '#fff3cc',
    '--color-on-warning': DEEP_AMBER,
    '--color-error': CARDINAL_RED,
    '--color-error-muted': '#ffe0e0',
    '--color-on-error': DEEP_RED,

    // ---- the washes tokens and badges draw from ------------------------
    '--color-background-green': STORYBOOK_GREEN,
    '--color-border-green': FRESH_LEAF,
    '--color-icon-green': DEEP_GREEN,
    '--color-text-green': DEEP_GREEN,

    '--color-background-blue': '#ddf4ff',
    '--color-border-blue': '#9adcfb',
    '--color-icon-blue': DEEP_BLUE,
    '--color-text-blue': DEEP_BLUE,

    '--color-background-orange': '#ffe9c9',
    '--color-border-orange': '#ffc06e',
    '--color-icon-orange': DEEP_AMBER,
    '--color-text-orange': DEEP_AMBER,

    '--color-background-yellow': '#fff3cc',
    '--color-border-yellow': BEE_YELLOW,
    '--color-icon-yellow': DEEP_AMBER,
    '--color-text-yellow': DEEP_AMBER,

    '--color-background-red': '#ffe0e0',
    '--color-border-red': '#ffb3b3',
    '--color-icon-red': DEEP_RED,
    '--color-text-red': DEEP_RED,

    '--color-background-gray': '#f2f2f2',
    '--color-border-gray': RULE_GRAY,
    '--color-icon-gray': PENCIL_GRAY,
    '--color-text-gray': PENCIL_GRAY,

    '--color-background-teal': '#d6f5ec',
    '--color-border-teal': '#8fe3cd',
    '--color-icon-teal': '#00695c',
    '--color-text-teal': '#00695c',

    '--color-background-cyan': '#d9f4fb',
    '--color-border-cyan': '#8fdcee',
    '--color-icon-cyan': '#00607a',
    '--color-text-cyan': '#00607a',

    '--color-background-purple': '#ece2ff',
    '--color-border-purple': '#c3a9ff',
    '--color-icon-purple': '#5b3fa8',
    '--color-text-purple': '#5b3fa8',

    '--color-background-pink': '#ffe1ef',
    '--color-border-pink': '#ffaed0',
    '--color-icon-pink': '#a32663',
    '--color-text-pink': '#a32663',

    // ---- rounding ------------------------------------------------------
    '--radius-inner': '8px',
    '--radius-element': '12px',
    '--radius-container': '16px',
    '--radius-page': '16px',

    // ---- flat, on purpose ----------------------------------------------
    '--shadow-low': 'none',
    '--shadow-med': 'none',
    '--shadow-high': 'none',
    '--color-shadow': '#00000000',
    '--shadow-inset-hover': `inset 0 0 0 2px ${EAGER_GREEN}33`,
    '--shadow-inset-selected': `inset 0 0 0 2px ${EAGER_GREEN}80`,
    '--shadow-inset-success': `inset 0 0 0 2px ${EAGER_GREEN}80`,
    '--shadow-inset-warning': `inset 0 0 0 2px ${BEE_YELLOW}80`,
    '--shadow-inset-error': `inset 0 0 0 2px ${CARDINAL_RED}80`,
  },

  components: {
    heading: {
      base: {letterSpacing: '-0.02em'},
      // A heading colour of its own, because the built-in ones are wrong here:
      // `accent` is the link blue, and green is not a link. Screens ask for it
      // by name — <Heading color="progress"> — so the section colour is set in
      // one place and named after what it means.
      'color:progress': {color: EAGER_GREEN},
    },

    text: {
      'type:display-1': {fontFamily: 'var(--font-family-heading)'},
      'type:display-2': {fontFamily: 'var(--font-family-heading)'},
      'type:display-3': {fontFamily: 'var(--font-family-heading)'},
      // The number on a stat tile is a headline, not a paragraph.
      'type:label': {fontWeight: 700},
    },

    // Sticker buttons: thick border, 12px radius, uppercase label. Filled green
    // for the action, blue-on-outline for the alternative — the reference is
    // explicit that an outlined button is a first-class control here, not a
    // fallback.
    button: {
      base: {
        borderRadius: '12px',
        borderWidth: '2px',
        borderStyle: 'solid',
        borderColor: FADED_GRAY,
        fontWeight: 700,
        textTransform: 'uppercase',
        letterSpacing: LABEL_TRACKING,
        boxShadow: 'none',
      },
      'variant:primary': {
        backgroundColor: EAGER_GREEN,
        color: PAPER_WHITE,
        borderColor: 'transparent',
        ':hover': {backgroundColor: '#4fb700'},
      },
      'variant:secondary': {
        backgroundColor: 'transparent',
        color: SPARK_BLUE,
        borderColor: FADED_GRAY,
        ':hover': {backgroundColor: '#1cb0f612'},
      },
      'variant:ghost': {borderColor: 'transparent'},
      'variant:destructive': {
        backgroundColor: CARDINAL_RED,
        color: PAPER_WHITE,
        borderColor: 'transparent',
      },
    },

    // The controls, not the field wrapper: a border on `field` would draw a box
    // around the label as well as the input.
    'text-input': {
      base: {borderRadius: '12px', borderWidth: '2px', borderStyle: 'solid', borderColor: RULE_GRAY},
    },
    selector: {
      base: {borderRadius: '12px', borderWidth: '2px', borderStyle: 'solid', borderColor: RULE_GRAY},
    },
    textarea: {
      base: {borderRadius: '12px', borderWidth: '2px', borderStyle: 'solid', borderColor: RULE_GRAY},
    },
    'field-label': {
      base: {fontWeight: 700, color: PENCIL_GRAY},
    },

    card: {
      base: {
        borderRadius: '16px',
        borderWidth: '2px',
        borderStyle: 'solid',
        borderColor: RULE_GRAY,
      },
    },

    section: {base: {borderRadius: '16px'}},

    banner: {base: {borderRadius: '16px', borderWidth: '2px', borderStyle: 'solid'}},

    // Tags and counts: pills with a border of their own colour, so a pale wash
    // still has an edge on white paper.
    token: {
      base: {
        borderRadius: '12px',
        fontWeight: 700,
        borderWidth: '2px',
        borderStyle: 'solid',
        borderColor: 'color-mix(in srgb, currentColor 30%, transparent)',
      },
    },
    badge: {base: {borderRadius: '12px', fontWeight: 700}},

    // Column headings are labels, which is the one place uppercase belongs.
    'table-header-cell': {
      base: {
        textTransform: 'uppercase',
        letterSpacing: LABEL_TRACKING,
        fontWeight: 700,
        fontSize: '13px',
        color: PENCIL_GRAY,
      },
    },

    'top-nav': {
      base: {
        backgroundColor: PAPER_WHITE,
        borderBottomWidth: '2px',
        borderBottomStyle: 'solid',
        borderBottomColor: RULE_GRAY,
      },
    },
    'top-nav-item': {
      base: {
        borderRadius: '12px',
        textTransform: 'uppercase',
        letterSpacing: LABEL_TRACKING,
        fontWeight: 700,
        fontSize: NAV_LABEL_SIZE,
        color: PENCIL_GRAY,
      },
      selected: {backgroundColor: STORYBOOK_GREEN, color: DEEP_GREEN},
    },
    'top-nav-heading': {
      base: {fontFamily: 'var(--font-family-heading)', fontWeight: 800},
    },

    // The funnel. Chunky and fully rounded, because a progress bar is the one
    // place this system earns the word "progress".
    progressbar: {base: {height: '16px'}},
    'progressbar-track': {base: {backgroundColor: RULE_GRAY, borderRadius: '9999px'}},
    'progressbar-fill': {base: {backgroundColor: EAGER_GREEN, borderRadius: '9999px'}},
  },

  // Every semantic name the design system looks up, drawn from one set —
  // see icons.js for why Lucide and what each name maps to.
  icons: classroomIcons,
})
