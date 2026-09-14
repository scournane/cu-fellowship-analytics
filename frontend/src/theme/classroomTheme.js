/**
 * Classroom — the console's theme.
 *
 * Built to the app the team is copying, not its marketing page. Three things
 * carry that look, and everything else here is in service of them:
 *
 *   * Controls sit on a solid bevel. A filled green button has a 4px band of
 *     darker green directly beneath it, square-cut, no blur; pressing drops the
 *     face onto the band. A white outlined button does the same on gray. This
 *     is the most recognisable element on those screens.
 *   * A region opens with a block of one saturated colour carrying white text
 *     — `<Card variant="lead-green">` and its blue, purple and orange siblings.
 *     Not a tint: the fill is the hue at full strength, stepped down in value
 *     only as far as white needs to stay readable on it (see LEAD_* below).
 *   * Type is heavy. Rounded display for numbers and headings, bold for every
 *     label, uppercase with tracking on the labels that name things.
 *
 * Surfaces stay flat otherwise: white cards, 16px corners, a 2px outline, and
 * no drop shadows anywhere. The bevel is the one exception, and it is a solid
 * offset rather than a blur, which is why `--shadow-*` are all still `none`.
 *
 * Green is for progress — the funnel, the primary action, the selected page.
 * Blue is for links and the outlined button. Neither carries small gray UI text.
 *
 * Light only. The reference is a light design, so `main.jsx` pins `mode="light"`
 * and every token below is a single value.
 *
 *
 * HOW THIS FILE HAS TO BE WRITTEN — read before editing
 * ----------------------------------------------------
 * The console's bundle puts StyleX's `@layer priority1…priority10` AFTER
 * `@layer astryx-theme` (verified in the browser: that is the layer order
 * document.styleSheets reports). A component's own StyleX declaration therefore
 * beats any theme rule that names the same CSS property — `.astryx-card
 * {border-width: 2px}` compiles fine and still loses to StyleX's
 * `border-width: var(--border-width)`.
 *
 * So the rule here is: **set the variable the component reads, not the property
 * it computes.** `--border-width`, `--color-text-primary`, `--text-label-size`,
 * `--font-weight-medium` and friends are read by StyleX at the element, so a
 * theme value for them wins wherever it is set. Astryx relies on the same
 * indirection itself (see the comment on `--astryx-card-padding` in core's
 * container.stylex.ts). Plain CSS properties are safe only where the component
 * sets nothing — `text-transform`, `letter-spacing`, `position`, `min-height`,
 * `translate`, and the `::before`/`::after` that nothing else claims.
 *
 * Anything changed here must be checked in a browser with `getComputedStyle`.
 * A rule that compiles is not a rule that applied.
 *
 * This is the SOURCE. `npm run theme` compiles it to `classroom.js` and
 * `classroom.css`, which are what the app imports; edit this file, never those.
 */

import {defineTheme} from '@astryxdesign/core/theme'
import {neutralTheme} from '@astryxdesign/theme-neutral'

import {classroomIcons} from './icons.js'

// ---------------------------------------------------------------------------
// Palette
// ---------------------------------------------------------------------------

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
const TRACK_GRAY = '#e5e5e5'

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

// Spark Blue carries almost every piece of interactive text in the console —
// links, and the label on every outlined button — and #1cb0f6 on paper measures
// 2.44:1, which is not a colour you can read a button by. Same treatment as the
// lead blocks: the hue and saturation stay, the value steps down until it
// clears the 4.5:1 body floor at 4.56:1. Spark Blue itself stays the palette's
// blue and the family the pale blue wash is drawn from; it just never carries
// text. (Deliberately the same value as LEAD_BLUE below — one deep blue.)
const LINK_BLUE = '#077db5'

// ---- the bevel band ------------------------------------------------------
// One step down in value from the face it sits under, square-cut. These carry
// no text, so they have no contrast floor of their own.
const GREEN_BEVEL = '#46a302'
const GRAY_BEVEL = FADED_GRAY
const RED_BEVEL = '#e03d3d'

// ---- the lead blocks -----------------------------------------------------
// The four colours a region can open with. The reference names them at full
// brightness — #58cc02, #1cb0f6, #a560f0, #ff9600 — and white on those measures
// 2.09, 2.44, 3.80 and 2.18 to one, so three of the four are illegible and the
// fourth only clears the large-text floor. The contract these variants owe the
// screens is that *any* text dropped into them is readable without the screen
// setting a colour, which is the 4.5:1 body floor, not the 3:1 headline one.
//
// So each is its own hue and saturation with the value stepped down until white
// clears 4.5:1, and no further: 4.52, 4.56, 4.52 and 4.60 to one respectively.
// They still read as green/blue/purple/orange at full strength — what moved is
// how dark they are, not how colourful. If the team would rather have the
// brighter fills and accept sub-AA text, these four constants are the only edit.
const LEAD_GREEN = '#3a8701'
const LEAD_BLUE = '#077db5'
const LEAD_PURPLE = '#9a4cee'
const LEAD_ORANGE = '#b85c00'

// Nav labels, table headings and button text: uppercase at the reference's
// tracking. Labels only — never body copy.
const LABEL_TRACKING = '0.053em'
// The reference sets nav labels at 15px. The top bar could not afford that with
// ten of them across a laptop; the rail gives each its own line, so 15px is back.
const NAV_LABEL_SIZE = '15px'

/** The ink a lead block hands everything inside it. White throughout — a
 *  translucent white for supporting copy would put the measured contrast back
 *  under the floor the fills were chosen for.
 *
 *  Both routes to a child's colour are covered, because a screen can take
 *  either: `color` on the card is what a `<Text color="inherit">` inherits,
 *  and the tokens below are what a Text or Heading left on its default colour
 *  reads from its own StyleX class — that class sits in a layer above any
 *  theme rule, so the token is the only thing that reaches it. */
function leadInk(fill) {
  return {
    backgroundColor: fill,
    color: PAPER_WHITE,
    // Every token a Text, Heading, Icon or Link reads for its colour. StyleX
    // resolves these at the element, so redefining them on the card is what
    // actually recolours the children — a `color` on the card alone would be
    // overwritten by each component's own class.
    '--color-text-primary': PAPER_WHITE,
    '--color-text-secondary': PAPER_WHITE,
    '--color-text-disabled': 'rgba(255,255,255,0.62)',
    '--color-text-accent': PAPER_WHITE,
    '--color-icon-primary': PAPER_WHITE,
    '--color-icon-secondary': PAPER_WHITE,
    '--color-icon-accent': PAPER_WHITE,
    '--color-on-light': PAPER_WHITE,
    // `--color-accent` too, and not only for completeness: AppFrame's `Region`
    // paints its heading with `xstyle={{color: var(--color-accent)}}`, so a
    // Region dropped inside a block would otherwise put deep green on the fill.
    // Flipping the accent pair also turns a primary Button inside a block into
    // a white button lettered in the block's own colour, which is what the
    // reference does with a call to action on a coloured panel — and `fill` on
    // white is the same measured ratio as white on `fill`.
    '--color-accent': PAPER_WHITE,
    '--color-on-accent': fill,
    '--section-heading-ink': PAPER_WHITE,
    '--accent-bevel': 'rgba(0,0,0,0.22)',
    // Anything neutral-filled inside — a Token, a secondary Button — needs to
    // read against the block rather than against paper.
    '--color-neutral': 'rgba(255,255,255,0.18)',
    '--color-border': 'rgba(255,255,255,0.32)',
    '--color-overlay-hover': 'rgba(255,255,255,0.12)',
    '--color-overlay-pressed': 'rgba(255,255,255,0.22)',
  }
}

export const classroomTheme = defineTheme({
  name: 'classroom',
  extends: neutralTheme,

  // ---- icons -------------------------------------------------------------
  // Every semantic name the design system looks up, plus one per staff
  // destination for the rail. See icons.js for which drawing each name gets and
  // why they are named one at a time rather than imported as a set.
  icons: classroomIcons,

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
    // and green on small text is the one thing it says never to do. The deep
    // step of that blue, because this token is only ever read by text and icons.
    '--color-text-accent': LINK_BLUE,
    '--color-icon-accent': LINK_BLUE,
    '--color-neutral': '#4b4b4b0d',
    '--color-overlay': '#4b4b4b80',
    '--color-overlay-hover': '#00000010',
    '--color-overlay-pressed': '#0000001f',
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
    '--radius-inner': '12px',
    '--radius-element': '12px',
    '--radius-container': '16px',
    '--radius-page': '16px',

    // ---- weight --------------------------------------------------------
    // Labels — buttons, nav rows, table headings, Text type="label" — are the
    // one place this design shouts, so the named weights sit a step heavier
    // than a neutral theme would put them and the label scale is bold outright.
    '--font-weight-medium': '700',
    '--font-weight-semibold': '800',
    '--font-weight-bold': '800',
    '--text-label-weight': '800',
    '--text-label-size': '15px',
    // The number on a stat tile is a headline, not a paragraph. The type scale
    // leaves the display weights at the body weight, which is not what a
    // rounded display face is for.
    '--text-display-1-weight': '800',
    '--text-display-2-weight': '800',
    '--text-display-3-weight': '800',

    // What a section heading is painted with — see the `heading` override.
    '--section-heading-ink': LEAD_GREEN,

    // The band under a filled green button. A token rather than a literal on
    // the button, so a lead block can hand its buttons a band that belongs to
    // the block instead of a green one.
    '--accent-bevel': GREEN_BEVEL,

    // ---- control height ------------------------------------------------
    // Buttons, selects and nav rows. The reference's controls are chunky; 32px
    // is a dense-admin height and reads as a different product.
    '--size-element-sm': '32px',
    '--size-element-md': '40px',
    '--size-element-lg': '48px',

    // ---- flat, on purpose ----------------------------------------------
    // The bevel under a button is drawn as a solid ::after band, not a shadow,
    // so nothing here needs to become one.
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
      base: {
        letterSpacing: '-0.02em',
        // AppFrame's `Region` paints its section heading with
        // `xstyle={{color: colorVars['--color-accent']}}`, which is the only
        // way a component's own colour can be reached from a screen. That puts
        // Eager Green on paper at 2.09:1. Re-pointing `--color-accent` on the
        // heading alone — buttons and progress bars read it on their own
        // elements and are untouched — takes those headings to 4.52:1 without
        // AppFrame having to change.
        //
        // Through a second token rather than straight to the green, because
        // this rule lands on the heading itself and would otherwise outrank a
        // lead block's own accent: inside a colour block the section heading
        // has to be the block's ink, not a green of its own.
        '--color-accent': 'var(--section-heading-ink)',
      },
      // A heading colour of its own, because the built-in ones are wrong here:
      // `accent` is the link blue, and green is not a link. Screens ask for it
      // by name — <Heading color="progress"> — so the section colour is set in
      // one place and named after what it means.
      //
      // The deep green, not the bright one: this is the only green in the
      // system that carries text, and #58cc02 on paper measures 2.09:1. At
      // 4.52:1 a "progress" heading is legible at any size the screens pick.
      'color:progress': {color: LEAD_GREEN},
    },

    text: {
      // Numbers and display lines take the rounded face. Text sets no
      // font-family of its own at these types, so the property lands.
      'type:display-1': {fontFamily: 'var(--font-family-heading)'},
      'type:display-2': {fontFamily: 'var(--font-family-heading)'},
      'type:display-3': {fontFamily: 'var(--font-family-heading)'},
      // The stat-tile caption. Uppercase belongs on a label and nowhere else.
      'type:label': {textTransform: 'uppercase', letterSpacing: LABEL_TRACKING},
    },

    // ---- the bevel -----------------------------------------------------
    // `::after` is the band, sitting flush under the face at `top: 100%`, with
    // the face's own bottom corners. Pressing translates the button down by the
    // band's height and collapses the band to nothing, so the bottom edge stays
    // put and the face lands on the page — which is what the reference does.
    //
    // It is a pseudo-element rather than `box-shadow: 0 4px 0` because Button
    // always composes its own `box-shadow` (elevation `none`) from a StyleX
    // layer above this one; a themed shadow here compiles and never paints.
    // `translate` is used rather than `transform` for the same reason: Button
    // owns `transform` on `:active`, and `translate` composes with it.
    button: {
      base: {
        borderRadius: '12px',
        textTransform: 'uppercase',
        letterSpacing: LABEL_TRACKING,
        '--button-bevel': GRAY_BEVEL,
        '::after': {
          content: '""',
          position: 'absolute',
          insetInlineStart: '0',
          insetInlineEnd: '0',
          top: '100%',
          height: '4px',
          backgroundColor: 'var(--button-bevel)',
          borderEndStartRadius: '12px',
          borderEndEndRadius: '12px',
          pointerEvents: 'none',
        },
        ':active': {translate: '0 4px'},
        ':active::after': {height: '0px'},
      },
      'variant:primary': {'--button-bevel': 'var(--accent-bevel)'},
      // The outlined button: white face, gray ring, gray band, blue label. The
      // ring is a `::before` because Button zeroes its own border width from a
      // later layer, so a themed border never shows.
      'variant:secondary': {
        '--color-neutral': PAPER_WHITE,
        '--color-text-primary': LINK_BLUE,
        '--color-icon-primary': LINK_BLUE,
        '--button-bevel': GRAY_BEVEL,
        '::before': {
          content: '""',
          position: 'absolute',
          inset: '0',
          borderRadius: 'inherit',
          borderWidth: '2px',
          borderStyle: 'solid',
          borderColor: FADED_GRAY,
          pointerEvents: 'none',
        },
      },
      // Ghost is the quiet one — no ring, no band, nothing to press onto.
      'variant:ghost': {
        '--button-bevel': 'transparent',
        ':active': {translate: '0 0'},
      },
      'variant:destructive': {'--button-bevel': RED_BEVEL},
    },

    // The controls, not the field wrapper: a border on `field` would draw a box
    // around the label as well as the input.
    'text-input': {
      base: {borderRadius: '12px', '--border-width': '2px', '--color-border': RULE_GRAY},
    },
    selector: {
      base: {borderRadius: '12px', '--border-width': '2px', '--color-border': RULE_GRAY},
    },
    textarea: {
      base: {borderRadius: '12px', '--border-width': '2px', '--color-border': RULE_GRAY},
    },
    'field-label': {
      base: {'--font-weight-medium': '700', '--color-text-secondary': PENCIL_GRAY},
    },

    // ---- cards ---------------------------------------------------------
    // White, 16px, outlined 2px, never shadowed. `--border-width` rather than
    // `border-width`: Card draws its border from the token.
    card: {
      base: {
        borderRadius: '16px',
        padding: '20px',
        '--border-width': '2px',
      },

      // ---- the four lead blocks ---------------------------------------
      // The contract with the screens: `<Card variant="lead-green">` (and
      // -blue, -purple, -orange) is a saturated block whose text is already the
      // right colour. Nothing inside has to pass a colour — the variant
      // redefines the ink tokens, and Text/Heading/Icon read them at the
      // element, so a plain <Heading> and a plain <Text type="supporting">
      // both come out white.
      //
      // Card applies its border and background only for variant="default", so
      // these fills meet no StyleX declaration and land as written.
      'variant:lead-green': leadInk(LEAD_GREEN),
      'variant:lead-blue': leadInk(LEAD_BLUE),
      'variant:lead-purple': leadInk(LEAD_PURPLE),
      'variant:lead-orange': leadInk(LEAD_ORANGE),
    },

    section: {base: {borderRadius: '16px'}},

    banner: {base: {borderRadius: '16px', '--border-width': '2px'}},

    // Tags and counts: pills at the element radius, bold.
    token: {
      base: {'--radius-inner': '12px', '--font-weight-medium': '700'},
    },
    badge: {base: {'--radius-inner': '12px', '--font-weight-medium': '700'}},

    // Column headings are labels, which is the one place uppercase belongs.
    // Row rules stay hairline — the 2px the cards carry would turn a table into
    // a grid of boxes — so the inherited `--border-width` is pinned back.
    'table-header-cell': {
      base: {
        textTransform: 'uppercase',
        letterSpacing: LABEL_TRACKING,
        '--text-label-size': '13px',
        '--font-weight-semibold': '800',
        '--color-text-secondary': PENCIL_GRAY,
        '--border-width': '1px',
      },
    },
    'table-cell': {base: {'--border-width': '1px'}},
    divider: {base: {'--border-width': '1px'}},

    // ---- the rail ------------------------------------------------------
    // SideNav sets no border and no font-family of its own, so both land as
    // properties here. The family has to be set on the rail rather than on the
    // heading: SideNavHeading's anchor carries `font-family: inherit` from a
    // layer above this one, so the only way to give the console's name the
    // rounded face is to hand it to the whole rail — which is also where the
    // reference puts it.
    'side-nav': {
      base: {
        backgroundColor: PAPER_WHITE,
        fontFamily: 'var(--font-family-heading)',
        borderInlineEndWidth: '2px',
        borderInlineEndStyle: 'solid',
        borderInlineEndColor: RULE_GRAY,
      },
    },

    // The staff nav is a rail now, so the selected-state treatment belongs
    // here. The row reads `--text-label-size`, `--font-weight-normal`,
    // `--color-text-primary` and — when selected — `--color-neutral` and
    // `--font-weight-medium`; setting those is the only way to reach it.
    // `position: relative` is free (SideNavItem sets no position), which is
    // what lets the selected row carry a ring without a border.
    'side-nav-item': {
      base: {
        borderRadius: '12px',
        textTransform: 'uppercase',
        letterSpacing: LABEL_TRACKING,
        position: 'relative',
        '--text-label-size': NAV_LABEL_SIZE,
        '--font-weight-normal': '700',
        '--color-text-primary': PENCIL_GRAY,
      },
      selected: {
        '--color-neutral': STORYBOOK_GREEN,
        '--color-text-primary': DEEP_GREEN,
        '--font-weight-medium': '800',
        '::before': {
          content: '""',
          position: 'absolute',
          inset: '0',
          borderRadius: 'inherit',
          borderWidth: '2px',
          borderStyle: 'solid',
          borderColor: FRESH_LEAF,
          pointerEvents: 'none',
        },
      },
    },

    // The console's name has to fit the 260px rail. At the stock large size it
    // ran past the 228px the heading row leaves and ellipsed to "CU check-in
    // cons…", so it drops a step and takes the rounded face instead — a
    // narrower, heavier line that reads as the product name and still fits.
    // Renaming the console was the other way out and is not this file's call.
    'side-nav-heading': {
      base: {
        letterSpacing: '-0.01em',
        '--text-large-size': '17px',
        '--text-large-leading': '1.3',
        '--font-weight-semibold': '800',
        '--color-text-primary': CHARCOAL,
      },
    },

    // Rail group headings — "Set up", "Cohort", "Queues".
    'side-nav-section': {
      base: {
        textTransform: 'uppercase',
        letterSpacing: LABEL_TRACKING,
        '--text-supporting-size': '12px',
        '--font-weight-semibold': '800',
        // Not #afafaf: a group heading is text, and #afafaf on paper is 2.19:1.
        '--color-text-secondary': PENCIL_GRAY,
      },
    },

    // Kept for the fellow's page, which still wears a title bar.
    'top-nav': {
      base: {
        backgroundColor: PAPER_WHITE,
        // Same reason as the rail: TopNavHeading inherits its family, so the
        // bar is where the rounded face has to be set.
        fontFamily: 'var(--font-family-heading)',
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
        '--text-label-size': NAV_LABEL_SIZE,
        '--font-weight-normal': '700',
        '--color-text-primary': PENCIL_GRAY,
      },
      selected: {'--color-neutral': STORYBOOK_GREEN, '--color-text-primary': DEEP_GREEN},
    },
    'top-nav-heading': {
      base: {'--font-weight-semibold': '800'},
    },

    // ---- the funnel ----------------------------------------------------
    // Thick and fully rounded, because a progress bar is the one place this
    // system earns the word "progress". The track's 8px is a literal in the
    // component's own layer, so `height` here would lose — `min-height` is a
    // different property and wins the used height outright, and the fill
    // resolves its 100% against that.
    progressbar: {
      base: {'--font-weight-medium': '800', '--font-weight-normal': '800'},
    },
    'progressbar-track': {
      base: {minHeight: '16px', '--color-background-muted': TRACK_GRAY},
    },
    // Green, deliberately. `astryx theme build` runs a contrast pass that
    // re-pins `--color-accent` to its own blue on an accent progress bar,
    // because Eager Green against a light track measures 1.66:1 and misses the
    // 3:1 floor for a graphical object. The brief names green as the colour of
    // progress, so it is restored here — on the fill, which is closer to the
    // element than the build's rule on the bar, and therefore wins.
    'progressbar-fill': {base: {'--color-accent': EAGER_GREEN}},
  },
})
