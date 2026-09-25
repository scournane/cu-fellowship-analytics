/**
 * Classroom — the console's icon set.
 *
 * Astryx ships a handful of minimal inline SVGs as a fallback and expects the
 * theme to replace them (`astryx docs icons`). This is that replacement: every
 * semantic name the design system resolves, drawn from Lucide.
 *
 * Lucide rather than any other set because it is drawn on a 24-unit grid with a
 * 2-unit stroke and round caps — the same weight as the theme's 2px component
 * borders — so an icon beside a sticker button looks like it was cut from the
 * same sheet. It is ISC licensed (`node_modules/lucide-react/LICENSE`): a
 * permissive one-paragraph licence, no attribution required in the UI. A
 * handful of its glyphs are inherited from Feather, MIT, on the same terms.
 *
 * NOTHING IS FETCHED. `lucide-react` is a dependency, the named imports below
 * are the only glyphs Rollup keeps, and they end up inline in `console.js`.
 * Importing the whole set to use thirty-eight of two thousand would be the easy
 * mistake here, which is why every icon is named one at a time rather than
 * pulled in as a namespace.
 *
 * Two kinds of key live in this registry:
 *
 *   * The twenty-eight names in Astryx's `IconName` that its own components
 *     look up — a Table's sort arrow, a Banner's status glyph, a Select's
 *     chevron. Those are fixed, and the set has to be complete: a name left out
 *     falls back to the placeholder SVG and the page ends up with two icon
 *     styles on it.
 *   * Ten `nav:*` keys of our own, one per staff destination. Astryx's registry
 *     takes arbitrary string keys for exactly this (`ExtendedIconName`), so the
 *     rail can say `icon="nav:roster"` and get an icon the theme owns, rather
 *     than importing Lucide into a screen. A SideNavItem with no icon is hidden
 *     when the rail collapses, so these are what make it collapsible.
 *
 * This file is imported by `classroomTheme.js` (`icons: classroomIcons`) and
 * `astryx theme build` copies that import into the generated `classroom.js` —
 * React elements cannot be serialised into a built theme, so the built module
 * re-imports this one by path. Which is why the import there has to name this
 * file WITH its extension: the generated module is plain ESM.
 *
 * And why there is no JSX below. A registry entry is a React element, not a
 * component, so each one has to be constructed — and this build's transform
 * rejects JSX in a `.js` file. `createElement` is the same thing JSX compiles
 * to, and with one element per line it reads as the table it is.
 */

import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  Calendar,
  CalendarDays,
  Check,
  CheckCheck,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  CircleCheck,
  CircleX,
  ClipboardList,
  Clock,
  Columns3,
  Copy,
  Ellipsis,
  ExternalLink,
  EyeOff,
  Funnel,
  Info,
  LayoutDashboard,
  LayoutTemplate,
  LifeBuoy,
  ListChecks,
  Megaphone,
  Menu,
  Mic,
  Plug,
  Repeat,
  Search,
  Square,
  TriangleAlert,
  Users,
  Wrench,
  X,
} from 'lucide-react'
import {createElement} from 'react'

/** Every glyph is drawn the same way, so the props are written once.
 *
 *  `size: '1em'` is what lets Astryx size them: a registry icon is rendered
 *  inside a span whose `font-size` the Icon component sets from its own `size`
 *  prop, so an em-sized SVG lands on 12/16/20/24px without this file knowing
 *  which. The stroke is Lucide's own 2 units, stated rather than inherited
 *  because it is the number that matches the theme's 2px borders. And
 *  `aria-hidden`, because the SVG is never the accessible name — Astryx's
 *  wrapper carries the label on the icons that mean something on their own. */
const glyph = {size: '1em', strokeWidth: 2, 'aria-hidden': true}

/** One entry. */
const drawn = (Glyph) => createElement(Glyph, glyph)

/**
 * The registry, in the order `astryx docs icons` lists the names.
 *
 * Where a name and a Lucide icon disagree the comment says why. The three
 * status glyphs are the only real judgement call: Astryx's fallbacks are solid
 * fills so they hold their colour at 12px, while Lucide is outline-only. Taking
 * Lucide's outlines keeps one drawing style across the console, and the theme
 * gives each status its own saturated colour, so they still read.
 */
export const classroomIcons = {
  close: drawn(X),
  chevronDown: drawn(ChevronDown),
  chevronLeft: drawn(ChevronLeft),
  chevronRight: drawn(ChevronRight),
  chevronsLeft: drawn(ChevronsLeft),
  chevronsRight: drawn(ChevronsRight),
  check: drawn(Check),
  success: drawn(CircleCheck),
  error: drawn(CircleX),
  warning: drawn(TriangleAlert),
  info: drawn(Info),
  calendar: drawn(Calendar),
  clock: drawn(Clock),
  externalLink: drawn(ExternalLink),
  menu: drawn(Menu),
  // Lucide renamed MoreHorizontal to Ellipsis; the old name is a deprecated
  // alias that resolves to a second copy of the file.
  moreHorizontal: drawn(Ellipsis),
  search: drawn(Search),
  arrowUp: drawn(ArrowUp),
  arrowDown: drawn(ArrowDown),
  arrowsUpDown: drawn(ArrowUpDown),
  // Likewise Filter → Funnel, which is also the name Astryx uses.
  funnel: drawn(Funnel),
  eyeSlash: drawn(EyeOff),
  // Three columns rather than two: the name is about column visibility in a
  // table, and two panes read as a split view.
  viewColumns: drawn(Columns3),
  copy: drawn(Copy),
  checkDouble: drawn(CheckCheck),
  wrench: drawn(Wrench),
  stop: drawn(Square),
  microphone: drawn(Mic),

  // ---- the staff rail --------------------------------------------------
  // Namespaced, because these are ours rather than Astryx's, and an unprefixed
  // `roster` could one day collide with a name the system adds.
  /** Connect Google — granting the one staff account's consent. */
  'nav:connect': drawn(Plug),
  /** Templates — the form every session's form is copied from. */
  'nav:templates': drawn(LayoutTemplate),
  /** Sessions — dated meetings, so the calendar with dates on it rather than
   *  the empty one the `calendar` name above already uses. */
  'nav:sessions': drawn(CalendarDays),
  /** Dashboard — the overview, not a report: panes, not a bar chart. */
  'nav:dashboard': drawn(LayoutDashboard),
  /** Assignments — work with a due time on it. */
  'nav:assignments': drawn(ClipboardList),
  /** Roster — who is in the cohort. */
  'nav:roster': drawn(Users),
  /** Rotation — the one question that cycles each week. */
  'nav:rotation': drawn(Repeat),
  /** Shoutouts — a fellow naming someone who helped them. */
  'nav:shoutouts': drawn(Megaphone),
  /** Review — a queue of judgements to work down. Deliberately not a second
   *  clipboard: Assignments already has one, and a collapsed rail is nothing
   *  but the icons. */
  'nav:review': drawn(ListChecks),
  /** Help requests — someone asked to be checked in on. */
  'nav:help-requests': drawn(LifeBuoy),
}

/**
 * The rail's destinations by name, so the frame can ask for `navIcons.roster`
 * instead of spelling a registry key. The values are registry keys, not icons:
 * `<SideNavItem icon={navIcons.roster}>` resolves through the theme, so which
 * drawing a destination gets stays a decision made in this file alone.
 */
export const navIcons = {
  connect: 'nav:connect',
  templates: 'nav:templates',
  sessions: 'nav:sessions',
  dashboard: 'nav:dashboard',
  assignments: 'nav:assignments',
  roster: 'nav:roster',
  rotation: 'nav:rotation',
  shoutouts: 'nav:shoutouts',
  review: 'nav:review',
  helpRequests: 'nav:help-requests',
}
