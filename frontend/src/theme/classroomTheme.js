/**
 * Classroom — the console's theme.
 *
 * Built to the app the team is copying, not its marketing page. Three things
 * carry that look, and everything else here is in service of them:
 *
 *   * The primary action sits on a solid bevel. A filled green button has a 4px
 *     band of darker green directly beneath it, square-cut, no blur; pressing
 *     drops the face onto the band. This is the most recognisable element on
 *     those screens, and it is the *primary* action's signature — see "THE
 *     TRIM" below for why nothing else gets one.
 *   * A region opens with a block of one saturated colour carrying white text
 *     — `<Card variant="lead-green">` and its blue, purple and orange siblings.
 *     Not a tint: the fill is the hue at full strength, stepped down in value
 *     only as far as white needs to stay readable on it (see LEAD_* below).
 *   * The rounded display face carries the numbers and the headings. Those are
 *     the page's loud voice; everything around them is quiet.
 *
 * Surfaces stay flat otherwise: white cards, 16px corners, no outline, and no
 * drop shadows anywhere. The bevel is the one exception, and it is a solid
 * offset rather than a blur, which is why `--shadow-*` are all still `none`.
 *
 * Green is for progress — the funnel, the primary action, the selected page.
 * Blue is for links and the outlined button. Neither carries small gray UI text.
 *
 * Light only. The reference is a light design, so `main.jsx` pins `mode="light"`
 * and every token below is a single value.
 *
 *
 * THE TRIM — what this theme deliberately does NOT do
 * ---------------------------------------------------
 * The first cut of this file put chrome on everything at once: caps with
 * tracking on nav rows, column headings, stat captions and buttons; a 2px
 * outline on cards, inputs and the rail; a bevel under every button; and 700–800
 * from the rail to the field labels. Four of each on one screen, and none of
 * them meant anything any more. The ornament now has one home each:
 *
 *   * UPPERCASE + tracking: buttons only. A nav row, a column heading and a
 *     stat caption read better in sentence case, and the tracking that makes
 *     caps legible is most of what made the page feel loud.
 *   * BORDERS: one weight — 1px — and only where an edge does work. Inputs get
 *     one (you have to aim at them, so it is also the one neutral dark enough
 *     to clear 3:1). The rail keeps a hairline because the page scrolls under
 *     it. Table rules stay hairline. Cards get none: a white card on a white
 *     page beside other white cards is separated by the gap between them.
 *   * THE BEVEL: `variant="primary"` and `variant="destructive"`. A secondary,
 *     a "Clear" and a preference toggle raised off the page at the same time
 *     are four objects competing for the one press.
 *   * WEIGHT: the display numbers and the section headings are heavy; nothing
 *     else is. Body copy sits at the face's own 400 so there is light material
 *     to rest on, and labels, tokens and nav rows sit at 600.
 *
 * Adding any of these back somewhere new means taking it off somewhere else.
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
// surfaces get a lighter step of the same neutral. #afafaf is now the wash
// under a pressed control rather than a line: at 2.19:1 on paper it is not a
// border you can find, so the edges that have to be aimed at (an input, an
// outlined button) take PENCIL_GRAY instead — see CONTROL_EDGE below.
const RULE_GRAY = '#e5e5e5'
const WASH_GRAY = '#f7f7f7'
const TRACK_GRAY = '#e5e5e5'

// The one border weight. Anything in this file that still draws a line draws
// it at this width; there is no second, heavier rule any more.
const HAIRLINE = '1px'

// The edge of something you have to aim at — a text field, an outlined button.
// It is the only place a line has to be findable rather than decorative, which
// is a 3:1 job, and PENCIL_GRAY is the lightest neutral in the palette that
// does it: #777777 on paper measures 4.48:1, against #afafaf's 2.19:1. Thinner
// than the 2px #afafaf it replaces, and actually visible.
const CONTROL_EDGE = PENCIL_GRAY

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
// no text, so they have no contrast floor of their own. There is no gray band
// any more: only a filled button is raised, and a filled button is either the
// accent or the destructive red.
const GREEN_BEVEL = '#46a302'
const RED_BEVEL = '#e03d3d'

// The three text controls are the same object, so they are written once. See
// the note on `text-input` in `components` for why the colour token is the
// emphasized one.
const INPUT_EDGE = {
  borderRadius: '12px',
  '--border-width': HAIRLINE,
  '--color-border-emphasized': CONTROL_EDGE,
}

/** The raised band and the press that goes with it, for the one or two
 *  variants that earn it. `band` is a colour or a `var()` — primary takes
 *  `--accent-bevel` so a lead block can hand its buttons a band of its own
 *  instead of a green one. */
function bevel(band) {
  return {
    '::after': {
      content: '""',
      position: 'absolute',
      insetInlineStart: '0',
      insetInlineEnd: '0',
      top: '100%',
      height: '4px',
      backgroundColor: band,
      borderEndStartRadius: '12px',
      borderEndEndRadius: '12px',
      pointerEvents: 'none',
    },
    ':active': {translate: '0 4px'},
    ':active::after': {height: '0px'},
  }
}

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

// Button text, and nothing else: uppercase at the reference's tracking. Nav
// rows, column headings and stat captions used to carry this too, which is
// four things in caps on one screen and the main reason the console read as
// shouting. See "THE TRIM" at the top of the file.
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
    //
    // 700, not 800: a heading is meant to be the heavy thing on a card, and at
    // 800 every card title was competing with the display numbers above it.
    // Nunito at 700 is still unmistakably the rounded, chunky face.
    heading: {
      family: 'Nunito Variable',
      fallbacks: 'Nunito, ui-rounded, "SF Pro Rounded", system-ui, sans-serif',
      weight: 700,
    },
    // 400 is this face's reading weight. At 500 there was no light material
    // anywhere on the page for the eye to rest on — every run of prose sat a
    // half-step up from where a paragraph belongs, which is most of why the
    // screens read as dense rather than as text.
    body: {
      family: 'Nunito Sans Variable',
      fallbacks: '"Nunito Sans", "Segoe UI", system-ui, -apple-system, sans-serif',
      weight: 400,
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
    // One step above a neutral theme, not two. These are read by tokens,
    // badges, field labels and nav rows — the supporting cast — and at 700/800
    // they were as heavy as the headings, so nothing on the page looked more
    // important than anything else. 600 still separates a label from its value
    // without asking to be read first.
    '--font-weight-medium': '600',
    '--font-weight-semibold': '700',
    '--font-weight-bold': '700',
    // The stat-tile caption sits under a 40px number. It does not also need to
    // be the boldest text in its own tile.
    '--text-label-weight': '600',
    '--text-label-size': '15px',
    // The number on a stat tile is a headline, not a paragraph. The type scale
    // leaves the display weights at the body weight, which is not what a
    // rounded display face is for. These stay at 800 while everything around
    // them comes down — they are the thing the eye is supposed to land on.
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
      // `type:label` used to be uppercase with tracking. It is the caption
      // under a stat number — "Attention index", "Slack (7 days)" — and there
      // were a dozen of them on the dashboard alone, next to a nav rail and a
      // table that were also in caps. Sentence case at `--text-label-weight`
      // is still clearly a caption and stops the screen shouting; nothing is
      // set here now, so the entry is gone rather than left empty.
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
    //
    // This lives on the variants rather than on `base` now. Every button used
    // to sit on a band, which put a secondary, a "Clear", a preference toggle
    // and a form's Save on the page as four raised objects at once — and a
    // raised object reads as "press me", so four of them is no signal at all.
    // It is the primary action's signature, and a destructive one is equally
    // the point of its row; nothing else is raised off the page.
    button: {
      base: {
        borderRadius: '12px',
        // The one place caps survive. A button label is short, it is an
        // instruction rather than something you read, and the tracking is what
        // makes the face's round caps legible at 15px.
        textTransform: 'uppercase',
        letterSpacing: LABEL_TRACKING,
      },
      'variant:primary': bevel('var(--accent-bevel)'),
      'variant:destructive': bevel(RED_BEVEL),
      // The outlined button: white face, one hairline ring, blue label, flat
      // on the page. The ring is a `::before` because Button zeroes its own
      // border width from a later layer, so a themed border never shows.
      //
      // Now that there is no band under it, the ring is the only thing marking
      // this as a control, which makes it a 3:1 edge rather than decoration —
      // hence CONTROL_EDGE (4.48:1) where this used to draw 2px of #afafaf
      // (2.19:1). Half the width, and findable.
      'variant:secondary': {
        '--color-neutral': PAPER_WHITE,
        '--color-text-primary': LINK_BLUE,
        '--color-icon-primary': LINK_BLUE,
        '::before': {
          content: '""',
          position: 'absolute',
          inset: '0',
          borderRadius: 'inherit',
          borderWidth: HAIRLINE,
          borderStyle: 'solid',
          borderColor: CONTROL_EDGE,
          pointerEvents: 'none',
        },
      },
      // Ghost is the quiet one — no ring, no band, nothing to press onto.
      'variant:ghost': {},
    },

    // The controls, not the field wrapper: a border on `field` would draw a box
    // around the label as well as the input.
    //
    // This is the one surface that keeps an edge, because you have to put a
    // cursor in it — and with the cards around it unoutlined, it is now the
    // only box on a form, which is the point. `--color-border-EMPHASIZED`, not
    // `--color-border`: checked in the browser, the wrapper `.astryx-text-input`
    // paints #afafaf from the emphasized token whatever `--color-border` says,
    // so the old `--color-border: RULE_GRAY` line here never painted anything.
    // A dead rule, exactly the kind the note above warns about.
    'text-input': {base: {...INPUT_EDGE}},
    selector: {base: {...INPUT_EDGE}},
    textarea: {base: {...INPUT_EDGE}},
    'field-label': {
      base: {'--font-weight-medium': '600', '--color-text-secondary': PENCIL_GRAY},
    },

    // ---- cards ---------------------------------------------------------
    // White, 16px, no outline, never shadowed. `--border-width` rather than
    // `border-width`: Card draws its border from the token, so zeroing the
    // token is what actually removes the line.
    //
    // The outline used to be 2px, and on a dashboard where every fellow, every
    // session and every badge is a card, that was twenty boxes drawn on white
    // — at which point the box stops saying "these things belong together" and
    // just says "furniture". The gap between two cards already says it. What
    // is left is the 20px of padding, which is what actually groups the
    // contents, and it does not change.
    //
    // A card that has to be separated from the page still can be: that is what
    // the four lead fills below are for.
    card: {
      base: {
        borderRadius: '16px',
        padding: '20px',
        '--border-width': '0px',
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

    // A banner is the one card-shaped thing that is interrupting you, and it
    // is already carrying a fill; a hairline is all the edge it needs.
    banner: {base: {borderRadius: '16px', '--border-width': HAIRLINE}},

    // Tags and counts: pills at the element radius. 600, not 700 — a token is
    // a value, and a page of them at 700 was a page of small bold objects.
    token: {
      base: {'--radius-inner': '12px', '--font-weight-medium': '600'},
    },
    badge: {base: {'--radius-inner': '12px', '--font-weight-medium': '600'}},

    // A column heading names the column; it is not the first thing in the
    // table you should read. Sentence case at 600 in pencil gray does that and
    // leaves the numbers underneath as the loud part. Row rules are hairline,
    // which is now the only rule weight in the file.
    'table-header-cell': {
      base: {
        // No size of its own. The 13px here was sized against tracked caps,
        // which take more room than they need; in sentence case it read as
        // fine print under 17px body copy, so the heading takes the label
        // size like every other label on the page.
        '--font-weight-semibold': '600',
        '--color-text-secondary': PENCIL_GRAY,
        '--border-width': HAIRLINE,
      },
    },
    'table-cell': {base: {'--border-width': HAIRLINE}},
    divider: {base: {'--border-width': HAIRLINE}},

    // ---- the rail ------------------------------------------------------
    // SideNav sets no border and no font-family of its own, so both land as
    // properties here. The family has to be set on the rail rather than on the
    // heading: SideNavHeading's anchor carries `font-family: inherit` from a
    // layer above this one, so the only way to give the console's name the
    // rounded face is to hand it to the whole rail — which is also where the
    // reference puts it.
    //
    // The rail keeps its rule where the cards lost theirs, and the difference
    // is that this one separates two regions rather than two items: the page
    // scrolls underneath it while the rail stays put, so the line is telling
    // you where the scrolling stops. At 1px it says that without fencing.
    'side-nav': {
      base: {
        backgroundColor: PAPER_WHITE,
        fontFamily: 'var(--font-family-heading)',
        borderInlineEndWidth: HAIRLINE,
        borderInlineEndStyle: 'solid',
        borderInlineEndColor: RULE_GRAY,
      },
    },

    // The staff nav is a rail now, so the selected-state treatment belongs
    // here. The row reads `--text-label-size`, `--font-weight-normal`,
    // `--color-text-primary` and — when selected — `--color-neutral` and
    // `--font-weight-medium`; setting those is the only way to reach it.
    //
    // Sentence case, and no ring on the selected row. Ten nav labels in caps
    // with tracking was the largest single block of shouting on the screen,
    // and a green pill with green ink in it does not also need to be outlined
    // in a third green to read as the page you are on — the fill says it, the
    // weight step confirms it.
    'side-nav-item': {
      base: {
        borderRadius: '12px',
        '--text-label-size': NAV_LABEL_SIZE,
        '--font-weight-normal': '600',
        '--color-text-primary': PENCIL_GRAY,
      },
      selected: {
        '--color-neutral': STORYBOOK_GREEN,
        '--color-text-primary': DEEP_GREEN,
        '--font-weight-medium': '700',
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
        '--font-weight-semibold': '700',
        '--color-text-primary': CHARCOAL,
      },
    },

    // Rail group headings — "Set up", "Cohort", "Queues". Sentence case: these
    // sat in caps directly above nav rows that were also in caps, so the group
    // and its contents looked like the same kind of thing. Small, gray and
    // lighter than the rows under them is what makes a group heading read.
    'side-nav-section': {
      base: {
        '--text-supporting-size': '12px',
        '--font-weight-semibold': '700',
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
        // Same rule, same reason, same weight as the rail's.
        borderBottomWidth: HAIRLINE,
        borderBottomStyle: 'solid',
        borderBottomColor: RULE_GRAY,
      },
    },
    'top-nav-item': {
      base: {
        borderRadius: '12px',
        '--text-label-size': NAV_LABEL_SIZE,
        '--font-weight-normal': '600',
        '--color-text-primary': PENCIL_GRAY,
      },
      selected: {'--color-neutral': STORYBOOK_GREEN, '--color-text-primary': DEEP_GREEN},
    },
    'top-nav-heading': {
      base: {'--font-weight-semibold': '700'},
    },

    // ---- the funnel ----------------------------------------------------
    // Thick and fully rounded, because a progress bar is the one place this
    // system earns the word "progress". The track's 8px is a literal in the
    // component's own layer, so `height` here would lose — `min-height` is a
    // different property and wins the used height outright, and the fill
    // resolves its 100% against that.
    //
    // The bar's own label and value — "Attendance", "2 / 5" — rendered at 800,
    // which put two more pieces of heavy type on every fellow row next to the
    // heavy number that row already leads with. They name a bar you can read at
    // a glance; 600 and 500 are enough.
    progressbar: {
      base: {'--font-weight-medium': '600', '--font-weight-normal': '500'},
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
