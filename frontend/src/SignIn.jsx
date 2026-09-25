import {Banner} from '@astryxdesign/core/Banner'
import {Button} from '@astryxdesign/core/Button'
import {Card} from '@astryxdesign/core/Card'
import {Center} from '@astryxdesign/core/Center'
import {Divider} from '@astryxdesign/core/Divider'
import {Heading} from '@astryxdesign/core/Heading'
import {Stack} from '@astryxdesign/core/Stack'
import {Text} from '@astryxdesign/core/Text'
import {TextInput} from '@astryxdesign/core/TextInput'
import {useState} from 'react'

// Native form posts, not fetch. The sign-in endpoints answer with 303s that
// set the session cookie and the PKCE cookie; letting the browser follow them
// keeps the OAuth round trip exactly as the server already implements it.
// PostForm is the same wrapper the framed screens use, stacked vertically.
import {PostForm} from './AppFrame.jsx'

export function SignIn({
  error,
  nextPath = '/',
  googleReady = false,
  devSignin = false,
  passwordSignin = false,
  fakeGoogle = false,
  allowlist = [],
}) {
  const [email, setEmail] = useState(allowlist[0] ?? '')
  const [password, setPassword] = useState('')

  return (
    <Center minHeight="100vh" padding={4}>
      <Stack gap={4} width="100%" maxWidth={480}>
        <Stack gap={1}>
          <Heading level={1}>CU check-in console</Heading>
          <Text type="supporting">
            Sign in to provision session forms, pull responses, and work the review queue.
          </Text>
        </Stack>

        {error ? <Banner status="error" title="Not signed in" description={error} /> : null}

        <Card padding={5}>
          <Stack gap={3}>
            <Heading level={2}>Sign in</Heading>
            <Text type="supporting">
              The console checks your address against the allowlist in CUFA_CONSOLE_ALLOWLIST.
            </Text>

            {googleReady ? (
              <PostForm action="/signin/google" method="get" direction="vertical" gap={3}>
                <input type="hidden" name="next" value={nextPath} />
                <Button label="Sign in with Google" variant="primary" type="submit" width="100%" />
              </PostForm>
            ) : (
              <Banner
                status="warning"
                title="Google sign-in is unavailable"
                description="GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are not set. See docs/setup/google-cloud.md."
              />
            )}
          </Stack>
        </Card>

        {devSignin ? (
          <Card padding={5} variant="muted">
            <Stack gap={3}>
              <Heading level={2}>Developer sign-in</Heading>
              <Banner
                status="warning"
                title="This is a bypass, not authentication"
                description={
                  fakeGoogle
                    ? 'This door is open because the fake Google client is switched on. It contacts Google not at all, which is what lets the demo and the test suite click through every screen offline. Do not expose this console to a network while it is available.'
                    : 'This door is open because no allowlist is configured. It contacts Google not at all. Do not expose this console to a network while it is available.'
                }
              />
              <Divider />
              <PostForm action="/signin/dev" direction="vertical" gap={3}>
                <input type="hidden" name="next" value={nextPath} />
                <TextInput
                  label="Your email address"
                  htmlName="email"
                  type="email"
                  value={email}
                  onChange={setEmail}
                  isRequired
                  autoComplete="email"
                  placeholder="you@civicsunplugged.org"
                  description={
                    allowlist.length
                      ? `Must be on the allowlist: ${allowlist.join(', ')}`
                      : 'No allowlist is configured, so any address is accepted. That is the problem this message exists to point at.'
                  }
                />
                <Button label="Sign in without Google" type="submit" width="100%" />
              </PostForm>
            </Stack>
          </Card>
        ) : null}

        {passwordSignin ? (
          <Card padding={5}>
            <Stack gap={3}>
              <Heading level={2}>Site password</Heading>
              <Text type="supporting">
                One password, shared by everyone who has it. It opens the dashboard and
                the session screens. It does not open help requests — those stay with the
                named people in CUFA_HELP_ALLOWLIST, because a shared password cannot say
                who read them.
              </Text>
              <Divider />
              <PostForm action="/signin/password" direction="vertical" gap={3}>
                <input type="hidden" name="next" value={nextPath} />
                <TextInput
                  label="Site password"
                  htmlName="password"
                  type="password"
                  value={password}
                  onChange={setPassword}
                  isRequired
                  autoComplete="current-password"
                />
                <Button label="Enter" variant="primary" type="submit" width="100%" />
              </PostForm>
            </Stack>
          </Card>
        ) : null}
      </Stack>
    </Center>
  )
}
