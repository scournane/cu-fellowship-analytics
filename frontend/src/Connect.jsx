import {Banner} from '@astryxdesign/core/Banner'
import {Button} from '@astryxdesign/core/Button'
import {Card} from '@astryxdesign/core/Card'
import {Heading} from '@astryxdesign/core/Heading'
import {MetadataList, MetadataListItem} from '@astryxdesign/core/MetadataList'
import {Stack} from '@astryxdesign/core/Stack'
import {Text} from '@astryxdesign/core/Text'
import {Token} from '@astryxdesign/core/Token'

import {Notices, PageHeader, PostForm} from './AppFrame.jsx'
import {fmtStamp} from './format.js'

function Scopes({scopes}) {
  return (
    <Stack gap={0.5}>
      {(scopes || []).map((scope) => (
        <Text key={scope} type="code">{scope}</Text>
      ))}
    </Stack>
  )
}

export function Connect({status = {}, required_scopes = [], notice, error, google_ready, fakeGoogle}) {
  const connected = Boolean(status.connected)

  return (
    <Stack gap={4}>
      <PageHeader title="Connect Google">
        One CU staff account grants access once. Every form this system creates is then
        owned by that account&apos;s Drive, which is where CU wants its work product.
      </PageHeader>

      <Notices notice={notice} error={error} />

      <Card padding={5}>
        <Stack gap={3}>
          <Stack direction="horizontal" gap={2} align="center">
            <Heading level={2}>Status</Heading>
            <Token
              label={connected ? 'connected' : 'not connected'}
              color={connected ? 'green' : 'orange'}
              size="sm"
            />
          </Stack>

          {connected ? (
            <>
              <MetadataList columns="single">
                <MetadataListItem label="Account">{status.account_email}</MetadataListItem>
                <MetadataListItem label="Connected">{fmtStamp(status.connected_at)}</MetadataListItem>
                <MetadataListItem label="Last refreshed">
                  {status.last_refreshed_at ? fmtStamp(status.last_refreshed_at) : 'not yet'}
                </MetadataListItem>
                <MetadataListItem label="Scopes">
                  <Scopes scopes={status.scopes} />
                </MetadataListItem>
              </MetadataList>

              {!status.has_required_scopes ? (
                <Banner
                  status="error"
                  title="A required scope is missing"
                  description="Disconnect and connect again, granting both scopes."
                  defaultIsExpanded
                >
                  <Scopes scopes={required_scopes} />
                </Banner>
              ) : null}
            </>
          ) : (
            <Stack gap={2}>
              <Text>No Google account is connected yet.</Text>
              <Text type="supporting">
                Two scopes are requested and no more: forms.body to create, edit and publish
                forms, and drive.file to copy the template. drive.file is enough precisely
                because this app created the template, so it stays in scope — a broader Drive
                scope would give this tool reach over a staff member&apos;s whole Drive to do a
                job that never needs it.
              </Text>
            </Stack>
          )}
        </Stack>
      </Card>

      <Card padding={5}>
        <Stack gap={3}>
          <Heading level={2}>Actions</Heading>

          {fakeGoogle ? (
            <Text type="supporting">
              The fake Google client is switched on, so Connect records a simulated connection
              and contacts nothing. Everything downstream — template, provisioning, pulling
              responses — runs against the in-memory fake.
            </Text>
          ) : null}

          {!fakeGoogle && !google_ready ? (
            <Banner
              status="warning"
              title="No OAuth client is configured"
              description="Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env first — see docs/setup/google-cloud.md."
            />
          ) : null}

          <Stack direction="horizontal" gap={2} wrap="wrap">
            <PostForm action="/google/connect">
              <Button
                label={connected ? 'Reconnect' : 'Connect Google'}
                variant="primary"
                type="submit"
                isDisabled={!fakeGoogle && !google_ready}
              />
            </PostForm>
            {connected ? (
              <PostForm
                action="/google/disconnect"
                confirm="Disconnect the Google account? Provisioning and pulling responses will stop working until you reconnect."
              >
                <Button label="Disconnect" variant="destructive" type="submit" />
              </PostForm>
            ) : null}
          </Stack>

          <Text type="supporting">
            The refresh token is encrypted before it is written. A raw
            {' '}select * from google_credential returns ciphertext.
          </Text>
        </Stack>
      </Card>
    </Stack>
  )
}
