import {AppShell} from '@astryxdesign/core/AppShell'
import {Banner} from '@astryxdesign/core/Banner'
import {Button} from '@astryxdesign/core/Button'
import {Divider} from '@astryxdesign/core/Divider'
import {Heading} from '@astryxdesign/core/Heading'
import {Layout, LayoutContent} from '@astryxdesign/core/Layout'
import {Stack} from '@astryxdesign/core/Stack'
import {Text} from '@astryxdesign/core/Text'
import {Token} from '@astryxdesign/core/Token'
import {TopNav, TopNavHeading, TopNavItem} from '@astryxdesign/core/TopNav'

export const NAV = [
  {href: '/', label: 'Connect Google', match: (p) => p === '/'},
  {href: '/template', label: 'Templates', match: (p) => p.startsWith('/template')},
  {href: '/sessions', label: 'Sessions', match: (p) => p.startsWith('/sessions')},
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
      heading={<TopNavHeading href="/sessions">CU check-in console</TopNavHeading>}
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
