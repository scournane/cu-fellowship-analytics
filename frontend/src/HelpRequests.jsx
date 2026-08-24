import {Banner} from '@astryxdesign/core/Banner'
import {Button} from '@astryxdesign/core/Button'
import {Card} from '@astryxdesign/core/Card'
import {EmptyState} from '@astryxdesign/core/EmptyState'
import {Heading} from '@astryxdesign/core/Heading'
import {Stack} from '@astryxdesign/core/Stack'
import {Tab, TabList} from '@astryxdesign/core/TabList'
import {Text} from '@astryxdesign/core/Text'
import {TextInput} from '@astryxdesign/core/TextInput'
import {Token} from '@astryxdesign/core/Token'
import {useState} from 'react'

import {PageHeader, PostForm} from './AppFrame.jsx'
import {helpRequestsUrl} from './CohortFilter.jsx'
import {fmtStamp} from './format.js'

const TABS = [
  {value: 'open', label: 'Open'},
  {value: 'acknowledged', label: 'Picked up'},
  {value: 'closed', label: 'Closed'},
]

const STATUS_COLOR = {open: 'red', acknowledged: 'orange', closed: 'green'}

/** One request, as a card rather than a table row.
 *
 *  Deliberately not the compact table every other screen uses. This is not a
 *  queue of work items to be cleared at speed — it is a small number of people
 *  who asked to be contacted, and the layout should not make them feel like
 *  rows.
 */
function Request({row, status}) {
  const [note, setNote] = useState('')

  return (
    <Card padding={5}>
      <Stack gap={3}>
        <Stack direction="horizontal" gap={3} align="center" wrap="wrap">
          <Heading level={2}>{row.full_name || row.submitted_email}</Heading>
          <Token
            label={row.status}
            color={STATUS_COLOR[row.status] || 'default'}
            size="sm"
          />
        </Stack>

        <Stack gap={1}>
          <Text type="supporting">
            {`Asked after ${row.session_title || 'a session we could not match'}`}
          </Text>
          <Text type="supporting">{`Submitted ${fmtStamp(row.submitted_at_utc)}`}</Text>
          {!row.fellow_id ? (
            <Text type="supporting">
              {`This address is not on the roster: ${row.submitted_email}. The request was recorded anyway — an out-of-date roster must never lose someone asking for contact.`}
            </Text>
          ) : null}
        </Stack>

        {row.acknowledged_by ? (
          <Stack gap={1}>
            <Text type="supporting">
              {`Picked up by ${row.acknowledged_by} on ${fmtStamp(row.acknowledged_at)}`}
            </Text>
            {row.note ? <Text>{row.note}</Text> : null}
          </Stack>
        ) : null}

        {row.status !== 'closed' ? (
          <PostForm action={`/help-requests/${row.help_request_id}/ack`} direction="vertical" gap={2}>
            <input type="hidden" name="status" value={status} />
            <TextInput
              label="Note"
              htmlName="note"
              size="sm"
              placeholder="What you did, or what happens next"
              description="Written by you, for the next person to read. Nothing the fellow typed on the form is stored here."
              value={note}
              onChange={setNote}
            />
            <Stack direction="horizontal" gap={2} wrap="wrap">
              {row.status === 'open' ? (
                <Button
                  label="I'm picking this up"
                  variant="primary"
                  type="submit"
                  name="action"
                  value="ack"
                />
              ) : null}
              <Button label="Close" type="submit" name="action" value="close" />
            </Stack>
          </PostForm>
        ) : null}
      </Stack>
    </Card>
  )
}

export function HelpRequests({
  rows = [],
  status = 'open',
  open_count = 0,
  routing = {},
  access_list = [],
  access_from_allowlist = false,
  notice,
}) {
  return (
    <Stack gap={4}>
      <PageHeader title="Help requests">
        Fellows who ticked “I&apos;d like someone to check in with me” at the end of a
        session.
      </PageHeader>

      <Banner
        status="warning"
        title="This screen is not routine data"
        description="These are young people who raised their hand. This list is restricted to the people named for it, appears in no report or export, and is never used in any count, rate or score — ticking that box costs a fellow nothing, and it only keeps working while that stays true."
      />

      {notice ? <Banner status="success" title="Done" description={notice} /> : null}

      {!routing.has_recipient ? (
        <Banner
          status="error"
          title="Nobody is configured to receive these"
          description={routing.reason_omitted || 'config/help_routing.json names no recipient.'}
        />
      ) : (
        <Card padding={5}>
          <Stack gap={2}>
            <Heading level={2}>Where these go</Heading>
            <Text type="supporting">
              {`Emailed the moment the request lands — not on a weekly run — to: ${(routing.recipients || []).map((r) => `${r.name || r.email}`).join(', ')}.`}
            </Text>
            <Text type="supporting">
              The email carries the fellow&apos;s name and the session, and nothing else.
              Not their takeaway, not their confidence rating, not who they thanked. If they
              want to tell you any of that, it is theirs to say.
            </Text>
            <Text type="supporting">
              {`Who can open this screen: ${access_list.join(', ') || '(nobody — set CUFA_HELP_ALLOWLIST or name a recipient)'}.`}
            </Text>
            <Text type="supporting">
              {access_from_allowlist
                ? 'That list comes from CUFA_HELP_ALLOWLIST in .env. Change it there to change who has access.'
                : 'CUFA_HELP_ALLOWLIST is not set, so access falls back to the recipients above. To let other staff in — without making them recipients of the emails — set CUFA_HELP_ALLOWLIST in .env and restart the console.'}
            </Text>
          </Stack>
        </Card>
      )}

      <TabList value={status} hasDivider>
        {TABS.map((tab) => (
          <Tab
            key={tab.value}
            value={tab.value}
            label={tab.value === 'open' ? `${tab.label} (${open_count})` : tab.label}
            href={helpRequestsUrl({status: tab.value})}
          />
        ))}
      </TabList>

      {!rows.length ? (
        <Card padding={5}>
          <EmptyState
            title={status === 'open' ? 'Nothing open' : 'Nothing here'}
            description="Requests appear the moment a Part B form is pulled."
            headingLevel={2}
          />
        </Card>
      ) : (
        <Stack gap={3}>
          {rows.map((row) => (
            <Request key={row.help_request_id} row={row} status={status} />
          ))}
        </Stack>
      )}

      <Text type="supporting">
        How long these records are kept has not been decided. That is deliberate — see
        docs/safeguarding.md — and it is the one open question on this table that somebody
        at CU has to answer rather than this system guessing.
      </Text>
    </Stack>
  )
}
