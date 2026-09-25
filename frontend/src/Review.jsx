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

/** "12 min", "3 h", "2 days" — a gap a reviewer can take in at a glance. */
function gap(minutes) {
  if (minutes < 120) return `${minutes} min`
  if (minutes < 48 * 60) return `${Math.round(minutes / 60)} h`
  return `${Math.round(minutes / (24 * 60))} days`
}

function timingText(row) {
  const t = row.timing || {}
  if (t.relation === 'before') return `${gap(t.minutes)} before the window`
  if (t.relation === 'after') return `${gap(t.minutes)} after the window`
  if (t.relation === 'inside') return 'inside the window'
  return row.session_id ? 'window not known' : 'inside no session’s window'
}

/** "answered 5 of 8": how much of the exit ticket came back. A count, never a
 *  judgement — a one-word answer and an essay both count once. */
function answeredText(row) {
  if (row.questions_answered === null || row.questions_answered === undefined) return null
  if (row.answer_total === null || row.answer_total === undefined) {
    return `answered ${row.questions_answered}`
  }
  return `answered ${row.questions_answered} of ${row.answer_total}`
}

/** When it arrived against the session window, and how much was answered. */
function Timing({row}) {
  const answered = answeredText(row)
  return (
    <Stack gap={0.5}>
      <Text type="supporting">{fmtStamp(row.submitted_at_utc)}</Text>
      <Token
        label={timingText(row)}
        color={row.timing && row.timing.relation === 'inside' ? 'green' : 'orange'}
        size="sm"
      />
      {answered ? <Text type="supporting">{answered}</Text> : null}
    </Stack>
  )
}

const RULE_LABEL = {
  outside_session_window: 'outside its session’s window',
  outside_all_windows: 'inside no session’s window',
}

function NeedsReview({rows, tab, cohort}) {
  if (!rows.length) return <Empty title="Nothing is waiting for a human" />
  return (
    <Stack gap={2}>
      <Table density="compact" dividers="rows">
        <TableRow isHeaderRow>
          <TableHeaderCell>Who</TableHeaderCell>
          <TableHeaderCell>Session</TableHeaderCell>
          <TableHeaderCell>Submitted</TableHeaderCell>
          <TableHeaderCell>Why it is here</TableHeaderCell>
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
              <Timing row={row} />
            </TableCell>
            <TableCell>
              <Stack gap={0.5}>
                <Text type="code">{row.rule_name || '—'}</Text>
                <Text type="supporting">
                  {row.source === 'csv'
                    ? 'CSV export — the address was typed, not confirmed by Google'
                    : 'the form — address confirmed by Google'}
                </Text>
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

/** Submitted, but not while the lesson was on.
 *
 *  Recorded as not attended by the rules, with less than full confidence, and
 *  listed here so a person can see how far out each one was. A fellow who
 *  filled it in the next morning was probably not in the room; one who was
 *  three minutes late with a slow connection probably was — the timing is
 *  shown so that call is made by someone, rather than by the cut-off.
 */
function OutsideWindow({rows, tab, cohort}) {
  if (!rows.length) return <Empty title="Nothing arrived outside a session window" />
  return (
    <Stack gap={2}>
      <Table density="compact" dividers="rows">
        <TableRow isHeaderRow>
          <TableHeaderCell>Who</TableHeaderCell>
          <TableHeaderCell>Session</TableHeaderCell>
          <TableHeaderCell>Submitted</TableHeaderCell>
          <TableHeaderCell>Recorded as</TableHeaderCell>
          <TableHeaderCell>Override</TableHeaderCell>
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
              <Text type="supporting">{row.session_title || 'no session matched'}</Text>
            </TableCell>
            <TableCell>
              <Timing row={row} />
            </TableCell>
            <TableCell>
              <Stack gap={0.5}>
                <StatusToken value={row.status} fallback="orange" />
                <Text type="supporting">{RULE_LABEL[row.rule_name] || row.rule_name}</Text>
                <Text type="supporting">confidence {row.confidence}</Text>
              </Stack>
            </TableCell>
            <TableCell>
              <DecideForm checkinId={row.checkin_id} tab={tab} cohort={cohort} withNote />
            </TableCell>
          </TableRow>
        ))}
      </Table>
      <Text type="supporting">
        Overriding one takes it off this list: it is then your decision, not the rule’s, and
        no later automated pass changes it.
      </Text>
    </Stack>
  )
}

function AiDecisions({rows, tab, cohort}) {
  if (!rows.length) return <Empty title="No AI decisions recorded yet" />
  return (
    <Table density="compact" dividers="rows">
      <TableRow isHeaderRow>
        <TableHeaderCell>Who</TableHeaderCell>
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
// table itself. Adding a tab is one entry here, not an entry plus a blurb plus
// another arm of a ternary. `legacyOnly` tabs appear only while there is
// something from before the change to show.
const TABS = [
  {
    value: 'needs_review',
    label: 'Needs review',
    Body: NeedsReview,
    blurb:
      'Oldest first — the longest-waiting judgment is the most overdue. Mostly check-ins from a CSV export, whose address was typed rather than confirmed by Google, and ones that fit two sessions at once. Needs review is not “did not attend”: nothing here has been counted either way.',
  },
  {
    value: 'outside_window',
    label: 'Outside the window',
    Body: OutsideWindow,
    blurb:
      'Exit tickets submitted before a session’s window opened or after it closed (the scheduled time, widened by the grace minutes on both sides). The rules recorded them as not attended, with confidence below 1, and they are listed with how far out they were so a person can correct the ones that were plainly there.',
  },
  {
    value: 'ai',
    label: 'AI decisions',
    Body: AiDecisions,
    legacyOnly: true,
    blurb:
      'Decisions a model made while check-ins were judged by passphrase. That tier is retired and makes no new decisions; these are kept, with the reasoning given, so they can still be read and overridden.',
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
  has_ai_decisions = false,
  cohorts = [],
  selected_cohort,
  straightline_note,
  notice,
}) {
  const tabs = TABS.filter((t) => !t.legacyOnly || has_ai_decisions)
  const current = tabs.find((t) => t.value === tab) ?? tabs[0]
  const Body = current.Body

  return (
    <Stack gap={4}>
      <PageHeader title="Review" />
      <Notices notice={notice} />

      <TabList value={current.value} hasDivider>
        {tabs.map((t) => (
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
        tab={current.value}
        cohort={selected_cohort}
        note={straightline_note}
      />
    </Stack>
  )
}
