import {AppShell} from '@astryxdesign/core/AppShell'
import {Banner} from '@astryxdesign/core/Banner'
import {Button} from '@astryxdesign/core/Button'
import {Card} from '@astryxdesign/core/Card'
import {Divider} from '@astryxdesign/core/Divider'
import {Heading} from '@astryxdesign/core/Heading'
import {Layout, LayoutContent} from '@astryxdesign/core/Layout'
import {Stack} from '@astryxdesign/core/Stack'
import {Text} from '@astryxdesign/core/Text'
import {TextInput} from '@astryxdesign/core/TextInput'
import {Token} from '@astryxdesign/core/Token'
import {TopNav, TopNavHeading, TopNavItem} from '@astryxdesign/core/TopNav'
import {useState} from 'react'

export const NAV = [
  // 'Google' rather than 'Connect Google': ten items is what fits across a
  // 1280-wide laptop, and this is the only one that names a once-per-install
  // action rather than a place. The screen itself still says Connect Google.
  {href: '/', label: 'Google', match: (p) => p === '/'},
  {href: '/template', label: 'Templates', match: (p) => p.startsWith('/template')},
  {href: '/sessions', label: 'Sessions', match: (p) => p.startsWith('/sessions')},
  {href: '/dashboard', label: 'Dashboard', match: (p) => p.startsWith('/dashboard')},
  {href: '/assignments', label: 'Assignments', match: (p) => p.startsWith('/assignments')},
  {href: '/roster', label: 'Roster', match: (p) => p.startsWith('/roster')},
  {href: '/rotation', label: 'Rotation', match: (p) => p.startsWith('/rotation')},
  {href: '/shoutouts', label: 'Shoutouts', match: (p) => p.startsWith('/shoutouts')},
  {href: '/review', label: 'Review', match: (p) => p.startsWith('/review')},
  // Only shown to the people allowed to open it. The server enforces the gate
  // regardless — this just stops the console offering a door that answers 403.
  {
    href: '/help-requests',
    label: 'Help requests',
    match: (p) => p.startsWith('/help-requests'),
    requiresHelpAccess: true,
  },
]

/** Plain form posts, kept for the same reason the sign-in screen keeps them:
 *  the server answers with 303s and owns the redirect. */
export function PostForm({
  action,
  children,
  confirm,
  method = 'post',
  direction = 'horizontal',
  gap = 2,
}) {
  return (
    <Stack
      as="form"
      method={method}
      action={action}
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

  const nav = (
    <TopNav
      label="Main"
      heading={<TopNavHeading heading="CU check-in console" headingHref="/sessions" />}
      endContent={
        user ? (
          <Stack direction="horizontal" gap={2} align="center">
            <Text type="supporting">{user.email}</Text>
            {user.isDevBypass ? <Text type="supporting" color="accent">dev bypass</Text> : null}
            <PostForm action="/signout">
              <Button label="Sign out" size="sm" type="submit" />
            </PostForm>
          </Stack>
        ) : null
      }
    >
      {user
        ? NAV.filter((item) => !item.requiresHelpAccess || user.mayReadHelp).map((item) => (
            <TopNavItem
              key={item.href}
              href={item.href}
              label={item.label}
              isSelected={item.match(path)}
            />
          ))
        : null}
    </TopNav>
  )

  return (
    <AppShell topNav={nav} banner={banners.length ? <Stack gap={0}>{banners}</Stack> : undefined}>
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
      <Heading level={2}>{title}</Heading>
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
