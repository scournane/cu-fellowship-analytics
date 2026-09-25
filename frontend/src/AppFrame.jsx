import {AppShell} from '@astryxdesign/core/AppShell'
import {Banner} from '@astryxdesign/core/Banner'
import {Button} from '@astryxdesign/core/Button'
import {Card} from '@astryxdesign/core/Card'
import {Divider} from '@astryxdesign/core/Divider'
import {Heading} from '@astryxdesign/core/Heading'
import {Layout, LayoutContent} from '@astryxdesign/core/Layout'
import {SideNav, SideNavHeading, SideNavItem, SideNavSection} from '@astryxdesign/core/SideNav'
import {Stack} from '@astryxdesign/core/Stack'
import {Text} from '@astryxdesign/core/Text'
import {TextInput} from '@astryxdesign/core/TextInput'
import {Token} from '@astryxdesign/core/Token'
import {TopNav, TopNavHeading} from '@astryxdesign/core/TopNav'
import {colorVars} from '@astryxdesign/core/theme/tokens.stylex'
import * as stylex from '@stylexjs/stylex'

import {navIcons} from './theme/icons.js'
import {useState} from 'react'

// The one style this file sets by hand, and it sets it from a token.
// Section headings are green in the theme's own words — the colour it
// calls progress — but a Heading's colour comes from the component's own
// StyleX class, which lands in a later cascade layer than any theme rule
// can reach. `xstyle` is the documented way through, and it carries the
// token rather than a hex, so the theme still owns the value.
const styles = stylex.create({
  sectionHeading: {color: colorVars['--color-accent']},
})

/** Every staff destination, in rail order.
 *
 *  `section` is the heading an entry sits under, and the rail reads the
 *  sections back off this list — so there is no second list of groups to drift
 *  out of step with the items. Screens that want only the destinations
 *  (Simple.jsx) still get a flat array. */
export const NAV = [
  {href: '/', label: 'Connect Google', section: 'Set up', icon: navIcons.connect, match: (p) => p === '/'},
  {href: '/template', label: 'Templates', section: 'Set up', icon: navIcons.templates, match: (p) => p.startsWith('/template')},
  {href: '/sessions', label: 'Sessions', section: 'Cohort', icon: navIcons.sessions, match: (p) => p.startsWith('/sessions')},
  {href: '/dashboard', label: 'Dashboard', section: 'Cohort', icon: navIcons.dashboard, match: (p) => p.startsWith('/dashboard')},
  {href: '/assignments', label: 'Assignments', section: 'Cohort', icon: navIcons.assignments, match: (p) => p.startsWith('/assignments')},
  {href: '/roster', label: 'Roster', section: 'Cohort', icon: navIcons.roster, match: (p) => p.startsWith('/roster')},
  {href: '/rotation', label: 'Rotation', section: 'Cohort', icon: navIcons.rotation, match: (p) => p.startsWith('/rotation')},
  // All three hold something waiting on a staff decision, which is why they
  // group: a queue is a different kind of errand from a screen you visit.
  {href: '/shoutouts', label: 'Shoutouts', section: 'Queues', icon: navIcons.shoutouts, match: (p) => p.startsWith('/shoutouts')},
  {href: '/review', label: 'Review', section: 'Queues', icon: navIcons.review, match: (p) => p.startsWith('/review')},
  // Only shown to the people allowed to open it. The server enforces the gate
  // regardless — this just stops the console offering a door that answers 403.
  {
    href: '/help-requests',
    label: 'Help requests',
    section: 'Queues',
    icon: navIcons.helpRequests,
    match: (p) => p.startsWith('/help-requests'),
    requiresHelpAccess: true,
  },
]

/** NAV as the rail draws it: the entries this user may open, gathered into
 *  their sections. Runs of one section are contiguous in NAV, so the grouping
 *  is the order the array is already written in. */
function navSections(user) {
  const sections = []
  for (const item of NAV) {
    if (item.requiresHelpAccess && !user.mayReadHelp) continue
    const open = sections[sections.length - 1]
    if (open && open.title === item.section) open.items.push(item)
    else sections.push({title: item.section, items: [item]})
  }
  return sections
}

/** Plain form posts, kept for the same reason the sign-in screen keeps them:
 *  the server answers with 303s and owns the redirect. */
export function PostForm({
  action,
  children,
  confirm,
  method = 'post',
  direction = 'horizontal',
  gap = 2,
  // A form carrying a file has to say so, or the browser sends the filename
  // and not the file. Only the roster upload needs it, so it is opt-in rather
  // than the default: every other form on the console is a handful of fields
  // and urlencoded is the right thing for those.
  encType,
}) {
  return (
    <Stack
      as="form"
      method={method}
      action={action}
      encType={encType}
      direction={direction}
      gap={gap}
      align={direction === 'horizontal' ? 'center' : undefined}
      wrap="wrap"
      onSubmit={confirm ? (e) => { if (!window.confirm(confirm)) e.preventDefault() } : undefined}
    >
      {children}
    </Stack>
  )
}

/** The page frame: nav, the two standing warnings, and the footer.
 *  Replaces base.html. */
export function AppFrame({user, path = '/', fakeGoogle, noAllowlist, children}) {
  const banners = []
  if (fakeGoogle) {
    banners.push(
      <Banner
        key="fake"
        container="section"
        status="info"
        title="Fake Google client"
        description="No Google calls will be made. Forms, publish states and responses are simulated in memory so every screen can be used offline."
      />,
    )
  }
  if (noAllowlist) {
    banners.push(
      <Banner
        key="allowlist"
        container="section"
        status="warning"
        title="No console allowlist is configured"
        description="Anyone who can reach this address can sign in. Set CUFA_CONSOLE_ALLOWLIST in .env before this console leaves your laptop."
      />,
    )
  }

  // A rail, not a top bar: ten destinations do not fit across a laptop, and
  // down the page is the direction there is room in. Below AppShell's md
  // breakpoint the whole rail — heading, items and footer — becomes its drawer.
  const nav = (
    <SideNav
      header={<SideNavHeading heading="CU check-in console" headingHref="/sessions" />}
      footer={
        user ? (
          <Stack gap={2}>
            {/* The footer slot is inset to where the item boxes start, not
                where their labels do, so the two text lines take one more step
                to land on the labels' line. The button is a box and already
                sits on it. Most addresses are wider than the rail, so the whole
                one goes to a tooltip. */}
            <Stack gap={0.5} paddingInline={2}>
              <Text type="supporting" maxLines={1}>{user.email}</Text>
              {user.isDevBypass ? <Text type="supporting" color="accent">dev bypass</Text> : null}
            </Stack>
            <PostForm action="/signout">
              <Button label="Sign out" size="sm" type="submit" />
            </PostForm>
          </Stack>
        ) : null
      }
    >
      {user
        ? navSections(user).map((section) => (
            <SideNavSection key={section.title} title={section.title}>
              {section.items.map((item) => (
                <SideNavItem
                  key={item.href}
                  href={item.href}
                  label={item.label}
                  icon={item.icon}
                  isSelected={item.match(path)}
                />
              ))}
            </SideNavSection>
          ))
        : null}
    </SideNav>
  )

  return (
    <AppShell
      sideNav={nav}
      variant="section"
      banner={banners.length ? <Stack gap={0}>{banners}</Stack> : undefined}
    >
      <Layout
        contentWidth={960}
        padding={4}
        content={
          <LayoutContent>
            <Stack gap={5} paddingBlock={5}>
              {children}
              <Divider />
              <Text type="supporting">
                Everything here is also available from the command line — cufa --help.
                The console is a convenience layer, not the only door.
              </Text>
            </Stack>
          </LayoutContent>
        }
      />
    </AppShell>
  )
}

/** Heading + supporting line, the shape every screen opens with. */
export function PageHeader({title, children}) {
  return (
    <Stack gap={1}>
      <Heading level={1}>{title}</Heading>
      {children ? <Text type="supporting">{children}</Text> : null}
    </Stack>
  )
}

// Outcomes that mean the same thing wherever they appear. Anything not listed
// falls back to the caller's choice, because "unknown" reads as a warning on
// the review queue and as neutral in the provisioning log.
const TOKEN_COLORS = {
  success: 'green',
  attended: 'green',
  failure: 'red',
  not_attended: 'red',
}

/** The label + colour + size trio five screens were spelling out by hand. */
export function StatusToken({value, label, fallback = 'default'}) {
  return (
    <Token
      label={label ?? String(value ?? '').replace(/_/g, ' ')}
      color={TOKEN_COLORS[value] ?? fallback}
      size="sm"
    />
  )
}

/** The server's `notice` / `error` pair, rendered the same way on every screen. */
export function Notices({notice, error, errorTitle = 'That did not work'}) {
  return (
    <>
      {notice ? <Banner status="success" title="Done" description={notice} /> : null}
      {error ? <Banner status="error" title={errorTitle} description={error} /> : null}
    </>
  )
}

/** The frame a fellow sees on their own page.
 *
 *  Deliberately not `AppFrame`: every link in the staff nav answers 403 to a
 *  fellow, and the two standing warnings are about a console they are not
 *  signed in to. They have one page, reached by a signed link, so the frame is
 *  a title bar and a column. */
export function FellowFrame({children}) {
  return (
    <AppShell
      topNav={
        <TopNav
          label="Main"
          heading={<TopNavHeading heading="Civic Innovators fellowship" />}
        />
      }
    >
      <Layout
        contentWidth={960}
        padding={4}
        content={
          <LayoutContent>
            <Stack gap={5} paddingBlock={5}>{children}</Stack>
          </LayoutContent>
        }
      />
    </AppShell>
  )
}

/** A region of a long screen: one heading, an optional line under it, then the
 *  content. A heading and spacing rather than a card per block — a dashboard
 *  wrapped in cards reads as a stack of unrelated widgets. */
export function Region({title, description, children}) {
  return (
    <Stack gap={2}>
      <Heading level={2} xstyle={styles.sectionHeading}>{title}</Heading>
      {description ? <Text type="supporting">{description}</Text> : null}
      {children}
    </Stack>
  )
}

/** One headline number, what it counts, and how it was arrived at. */
export function StatTile({value, label, detail}) {
  return (
    <Card>
      <Stack gap={1}>
        <Text type="display-3" hasTabularNumbers>{value}</Text>
        <Text type="label">{label}</Text>
        {detail ? <Text type="supporting">{detail}</Text> : null}
      </Stack>
    </Card>
  )
}

/** A text field inside a `PostForm`.
 *
 *  TextInput is a controlled component, and these forms are ordinary posts the
 *  server reads off the request — so the state exists only to satisfy that
 *  contract, and is read by nothing else. */
export function InlineField({label, name, placeholder, size = 'sm', width}) {
  const [value, setValue] = useState('')
  return (
    <TextInput
      label={label}
      isLabelHidden
      htmlName={name}
      size={size}
      width={width}
      value={value}
      onChange={setValue}
      placeholder={placeholder}
    />
  )
}
