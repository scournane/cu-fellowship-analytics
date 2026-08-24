import {Button} from '@astryxdesign/core/Button'
import {Card} from '@astryxdesign/core/Card'
import {EmptyState} from '@astryxdesign/core/EmptyState'
import {Stack} from '@astryxdesign/core/Stack'
import {Tab, TabList} from '@astryxdesign/core/TabList'
import {Table, TableCell, TableHeaderCell, TableRow} from '@astryxdesign/core/Table'
import {Text} from '@astryxdesign/core/Text'
import {TextInput} from '@astryxdesign/core/TextInput'
import {Token} from '@astryxdesign/core/Token'
import {useState} from 'react'

import {Notices, PageHeader, PostForm, StatusToken} from './AppFrame.jsx'
import {CohortFilter, reviewUrl} from './CohortFilter.jsx'
import {fmtStamp} from './format.js'

/** The empty state three tabs render, differing only in the sentence. */
function Empty({title}) {
  return (
    <Card padding={5}>
      <EmptyState title={title} headingLevel={2} />
    </Card>
  )
}

/** Two submit buttons sharing one form, each posting its own `status` value —
 *  the same shape the Jinja version used. */
function DecideForm({checkinId, tab, cohort, withNote}) {
  const [note, setNote] = useState('')
  return (
    <PostForm action={`/review/${checkinId}/decide`}>
      <input type="hidden" name="tab" value={tab} />
      <input type="hidden" name="cohort" value={cohort || ''} />
      {withNote ? (
        <TextInput
          label="Note"
          isLabelHidden
          htmlName="note"
          size="sm"
          placeholder="optional note"
          value={note}
          onChange={setNote}
        />
      ) : null}
      <Button label="Attended" size="sm" type="submit" name="status" value="attended" />
      <Button
        label="Not attended"
        size="sm"
        variant="destructive"
        type="submit"
        name="status"
        value="not_attended"
      />
    </PostForm>
  )
}

function NeedsReview({rows, expected, tab, cohort}) {
  if (!rows.length) return <Empty title="Nothing is waiting for a human" />
  return (
    <Stack gap={2}>
      <Table density="compact" dividers="rows">
        <TableRow isHeaderRow>
          <TableHeaderCell>Who</TableHeaderCell>
          <TableHeaderCell>Session</TableHeaderCell>
          <TableHeaderCell>Submitted</TableHeaderCell>
          <TableHeaderCell>Passphrase</TableHeaderCell>
          <TableHeaderCell>Decide</TableHeaderCell>
        </TableRow>
        {rows.map((row) => (
          <TableRow key={row.checkin_id}>
            <TableCell>
              <Stack gap={0.5}>
                {row.full_name ? <Text>{row.full_name}</Text> : null}
                <Text type="code">{row.submitted_email}</Text>
                {!row.fellow_id ? <Token label="not on the roster" color="orange" size="sm" /> : null}
              </Stack>
            </TableCell>
            <TableCell>
              <Stack gap={0.5}>
                <Text type="supporting">{row.session_title || 'no session matched'}</Text>
                {row.session_match !== 'matched' ? (
                  <Token label={row.session_match} color="orange" size="sm" />
                ) : null}
              </Stack>
            </TableCell>
            <TableCell>
              <Stack gap={0.5}>
                <Text type="supporting">{fmtStamp(row.submitted_at_utc)}</Text>
                {row.latency_seconds !== null && row.latency_seconds !== undefined ? (
                  <Text type="supporting">{row.latency_seconds}s after T0</Text>
                ) : null}
              </Stack>
            </TableCell>
            <TableCell>
              <Stack gap={0.5}>
                <Text type="supporting">typed: <Text type="code">{row.passphrase_raw || '(blank)'}</Text></Text>
                <Text type="supporting">
                  expected: <Text type="code">{expected[String(row.session_id)] || '—'}</Text>
                </Text>
                <Stack direction="horizontal" gap={1} align="center" wrap="wrap">
                  <Token label={row.passphrase_match} color="default" size="sm" />
                  {row.edit_distance !== null && row.edit_distance !== undefined ? (
                    <Text type="supporting">distance {row.edit_distance}</Text>
                  ) : null}
                </Stack>
                {row.ai_reasoning ? (
                  <Text type="supporting">model said: {row.ai_reasoning}</Text>
                ) : null}
              </Stack>
            </TableCell>
            <TableCell>
              <DecideForm checkinId={row.checkin_id} tab={tab} cohort={cohort} withNote />
            </TableCell>
          </TableRow>
        ))}
      </Table>
      <Text type="supporting">
        A human decision supersedes whatever the rules or the model said, carries confidence
        1.0 and your address, and is never overwritten by a later automated pass.
      </Text>
    </Stack>
  )
}

function AiDecisions({rows, expected, tab, cohort}) {
  if (!rows.length) return <Empty title="No AI decisions recorded yet" />
  return (
    <Table density="compact" dividers="rows">
      <TableRow isHeaderRow>
        <TableHeaderCell>Who</TableHeaderCell>
        <TableHeaderCell>Typed / expected</TableHeaderCell>
        <TableHeaderCell>Verdict</TableHeaderCell>
        <TableHeaderCell>Reasoning</TableHeaderCell>
        <TableHeaderCell>Model</TableHeaderCell>
        <TableHeaderCell>Override</TableHeaderCell>
      </TableRow>
      {rows.map((row) => (
        <TableRow key={row.checkin_id}>
          <TableCell>
            <Stack gap={0.5}>
              <Text type="supporting">{row.full_name || row.submitted_email}</Text>
              <Text type="supporting">{row.session_title || '—'}</Text>
            </Stack>
          </TableCell>
          <TableCell>
            <Stack gap={0.5}>
              <Text type="code">{row.passphrase_raw || '(blank)'}</Text>
              <Text type="code">{expected[String(row.session_id)] || '—'}</Text>
            </Stack>
          </TableCell>
          <TableCell>
            <Stack gap={0.5}>
              <StatusToken value={row.status} fallback="orange" />
              <Text type="supporting">confidence {row.confidence}</Text>
            </Stack>
          </TableCell>
          <TableCell><Text type="supporting">{row.ai_reasoning || '—'}</Text></TableCell>
          <TableCell>
            <Stack gap={0.5}>
              <Text type="code">{row.ai_model}</Text>
              <Text type="supporting">{row.ai_prompt_version}</Text>
            </Stack>
          </TableCell>
          <TableCell>
            <DecideForm checkinId={row.checkin_id} tab={tab} cohort={cohort} />
          </TableCell>
        </TableRow>
      ))}
    </Table>
  )
}

/** Fellows who gave the same confidence value four or more sessions running.
 *
 *  A data-quality flag on the RESPONSES, not a judgment about the person, and
 *  the wording here has to keep saying so — the number is easy to read as
 *  "disengaged", and fatigued respondents repeating an answer is a fact about
 *  the survey rather than about them. It feeds no count, rate or score.
 */
function StraightLining({rows, note}) {
  if (!rows.length) {
    return <Empty title="No repeated confidence runs" />
  }
  return (
    <Stack gap={2}>
      <Table density="compact" dividers="rows">
        <TableRow isHeaderRow>
          <TableHeaderCell>Fellow</TableHeaderCell>
          <TableHeaderCell>Value</TableHeaderCell>
          <TableHeaderCell>Sessions in a row</TableHeaderCell>
          <TableHeaderCell>Which ones</TableHeaderCell>
        </TableRow>
        {rows.map((row, i) => (
          <TableRow key={`${row.fellow_id}-${row.confidence_raw}-${i}`}>
            <TableCell><Text>{row.full_name || row.fellow_id}</Text></TableCell>
            <TableCell><Text hasTabularNumbers>{row.confidence_raw}</Text></TableCell>
            <TableCell>
              <Token label={`${row.run_length} in a row`} color="orange" size="sm" />
            </TableCell>
            <TableCell>
              <Text type="supporting">{(row.session_titles || []).join(' · ')}</Text>
            </TableCell>
          </TableRow>
        ))}
      </Table>
      <Text type="supporting">{note}</Text>
    </Stack>
  )
}

function Identities({rows}) {
  if (!rows.length) return <Empty title="Every address that has checked in matches the roster" />
  return (
    <Stack gap={2}>
      <Table density="compact" dividers="rows">
        <TableRow isHeaderRow>
          <TableHeaderCell>Address</TableHeaderCell>
          <TableHeaderCell>Cohort</TableHeaderCell>
          <TableHeaderCell>Times seen</TableHeaderCell>
          <TableHeaderCell>First seen</TableHeaderCell>
          <TableHeaderCell>Last seen</TableHeaderCell>
          <TableHeaderCell>Best guess</TableHeaderCell>
        </TableRow>
        {rows.map((row) => (
          <TableRow key={`${row.email}-${row.cohort_id}`}>
            <TableCell><Text type="code">{row.email}</Text></TableCell>
            <TableCell><Text type="supporting">{row.cohort_id}</Text></TableCell>
            <TableCell><Text hasTabularNumbers>{row.occurrence_count}</Text></TableCell>
            <TableCell><Text type="supporting">{fmtStamp(row.first_seen_at)}</Text></TableCell>
            <TableCell><Text type="supporting">{fmtStamp(row.last_seen_at)}</Text></TableCell>
            <TableCell>
              {row.best_guess_fellow_id ? (
                <Stack gap={0.5}>
                  <Text type="supporting">
                    {row.best_guess_fellow_id} ({row.best_guess_score})
                  </Text>
                  <Text type="supporting">advisory only — never auto-linked</Text>
                </Stack>
              ) : <Text type="supporting">—</Text>}
            </TableCell>
          </TableRow>
        ))}
      </Table>
      <Text type="supporting">
        Resolve these by correcting fellow.primary_email in the roster (cufa load-roster),
        not by editing the check-in.
      </Text>
    </Stack>
  )
}

// One table per tab: the link label, the sentence above the table, and the
// table itself. Adding a fourth tab is one entry here, not an entry plus a
// blurb plus another arm of a ternary.
const TABS = [
  {
    value: 'needs_review',
    label: 'Needs review',
    Body: NeedsReview,
    blurb:
      'Oldest first — the longest-waiting judgment is the most overdue. Needs review is not “did not attend.” Absent evidence is not evidence of absence, so nothing here has been counted either way.',
  },
  {
    value: 'ai',
    label: 'AI decisions',
    Body: AiDecisions,
    blurb:
      'Every decision the model made, with the reasoning it gave. This tab exists so a person can sample the model’s judgment rather than trust it. Overriding one here supersedes it permanently.',
  },
  {
    value: 'straightlining',
    label: 'Straight-lining',
    Body: StraightLining,
    blurb:
      'Fellows who submitted an identical confidence value four or more sessions in a row. This is a data-quality flag on the responses, not a finding about a person, and it enters no count, rate or score anywhere in this system.',
  },
  {
    value: 'identities',
    label: 'Unresolved addresses',
    Body: Identities,
    blurb:
      'Addresses that submitted a check-in but match nobody on the roster. The check-in was still recorded — identity never blocks ingest. Fix the roster entry and every historical check-in re-attributes itself, because identity resolves at read time.',
  },
]

export function Review({
  tab = 'needs_review',
  rows = [],
  expected = {},
  cohorts = [],
  selected_cohort,
  straightline_note,
  notice,
}) {
  const current = TABS.find((t) => t.value === tab) ?? TABS[0]
  const Body = current.Body

  return (
    <Stack gap={4}>
      <PageHeader title="Review" />
      <Notices notice={notice} />

      <TabList value={current.value} hasDivider>
        {TABS.map((t) => (
          <Tab
            key={t.value}
            value={t.value}
            label={t.label}
            href={reviewUrl({tab: t.value, cohort: selected_cohort})}
          />
        ))}
      </TabList>

      <Stack direction="horizontal">
        <CohortFilter
          cohorts={cohorts}
          selected={selected_cohort}
          hrefFor={(cohort) => reviewUrl({tab: current.value, cohort})}
        />
      </Stack>

      <Text type="supporting">{current.blurb}</Text>

      <Body
        rows={rows}
        expected={expected}
        tab={current.value}
        cohort={selected_cohort}
        note={straightline_note}
      />
    </Stack>
  )
}
