import {Button} from '@astryxdesign/core/Button'
import {Card} from '@astryxdesign/core/Card'
import {EmptyState} from '@astryxdesign/core/EmptyState'
import {Grid} from '@astryxdesign/core/Grid'
import {Heading} from '@astryxdesign/core/Heading'
import {Link} from '@astryxdesign/core/Link'
import {List, ListItem} from '@astryxdesign/core/List'
import {MetadataList, MetadataListItem} from '@astryxdesign/core/MetadataList'
import {ProgressBar} from '@astryxdesign/core/ProgressBar'
import {Stack} from '@astryxdesign/core/Stack'
import {Table, TableCell, TableHeaderCell, TableRow} from '@astryxdesign/core/Table'
import {Text} from '@astryxdesign/core/Text'
import {Token} from '@astryxdesign/core/Token'

import {InlineField, Notices, PageHeader, PostForm, Region, StatTile} from './AppFrame.jsx'
import {CohortFilter} from './CohortFilter.jsx'
import {fmtDateTime, fmtStamp} from './format.js'

// What each row of `received` is. The keys are the names the sync module uses;
// anything it grows later falls back to its own key rather than disappearing.
const SOURCE_LABELS = {
  slack_message: 'Slack messages',
  slack_sync: 'Slack sync',
  part_a: 'Check-ins (part A)',
  part_b: 'Exit tickets (part B)',
}

function dashboardUrl({cohort} = {}) {
  return cohort ? `/dashboard?cohort=${encodeURIComponent(cohort)}` : '/dashboard'
}

function percent(value) {
  return `${Math.round(100 * (value || 0))}%`
}

/** A rate with nothing behind it is not 0% — it is unknown, and printing 0%
 *  invents a number nobody measured. (Upstream fix F-03, kept here.) */
function rate(value) {
  return value === null || value === undefined ? '—' : percent(value)
}

/** The "has anyone spoken to them?" toggle, one row's worth.
 *
 *  A note field on the way in and nothing but a clear button on the way out:
 *  what was said is recorded once, at the moment somebody knows it. */
function OutreachCell({row, cohort}) {
  return (
    <PostForm action={`/dashboard/fellow/${row.fellow_id}/outreach`}>
      <input type="hidden" name="cohort" value={cohort || ''} />
      {row.reached_out ? (
        <>
          <Token label="yes" color="green" size="sm" />
          <input type="hidden" name="action" value="clear" />
          <Button label="Clear" size="sm" type="submit" />
        </>
      ) : (
        <>
          <input type="hidden" name="action" value="mark" />
          <InlineField
            label={`Note about reaching out to ${row.full_name}`}
            name="note"
            placeholder="note (optional)"
            width={110}
          />
          <Button label="Reached out" size="sm" type="submit" variant="primary" />
        </>
      )}
    </PostForm>
  )
}

/** One assignment: what it is, who has handed it in, and the score column
 *  staff type into. Scores come from a human with a rubric; the system only
 *  stores them. */
function Assignment({assignment, cohort}) {
  return (
    <Stack gap={2}>
      <Stack direction="horizontal" gap={2} align="center" wrap="wrap">
        <Heading level={3}>{assignment.title}</Heading>
        <Token label={assignment.kind.replace(/_/g, ' ')} size="sm" />
      </Stack>
      <Text type="supporting">
        Due {fmtStamp(assignment.due_at_utc)} · {assignment.submitted} submitted ·{' '}
        {assignment.scored} scored
        {assignment.link ? <> · <Link href={assignment.link} isExternalLink>Open</Link></> : null}
      </Text>
      <Table density="compact" dividers="rows">
        <TableRow isHeaderRow>
          <TableHeaderCell>Fellow</TableHeaderCell>
          <TableHeaderCell>Submitted</TableHeaderCell>
          <TableHeaderCell>Score</TableHeaderCell>
          <TableHeaderCell>Enter or change</TableHeaderCell>
        </TableRow>
        {assignment.rows.map((row) => (
          <TableRow key={row.fellow_id}>
            <TableCell>{row.full_name}</TableCell>
            <TableCell>
              <Text type="supporting">
                {row.submitted_at_utc ? fmtDateTime(row.submitted_at_utc) : 'not yet'}
              </Text>
            </TableCell>
            <TableCell>
              <Stack gap={0.5}>
                <Text hasTabularNumbers>
                  {row.score === null || row.score === undefined
                    ? '—'
                    : `${row.score}${assignment.max_score ? ` / ${assignment.max_score}` : ''}`}
                </Text>
                {row.graded_by ? (
                  <Text type="supporting">entered by {row.graded_by}</Text>
                ) : null}
              </Stack>
            </TableCell>
            <TableCell>
              <PostForm action="/dashboard/score">
                <input type="hidden" name="cohort" value={cohort || ''} />
                <input type="hidden" name="assignment_id" value={assignment.assignment_id} />
                <input type="hidden" name="fellow_id" value={row.fellow_id} />
                <InlineField
                  label={`Score for ${row.full_name}`}
                  name="score"
                  placeholder="score"
                  width={72}
                />
                <InlineField
                  label={`Note on ${row.full_name}'s score`}
                  name="note"
                  placeholder="note"
                  width={120}
                />
                <Button label="Save" size="sm" type="submit" />
              </PostForm>
            </TableCell>
          </TableRow>
        ))}
      </Table>
    </Stack>
  )
}

export function Dashboard({
  cohort_id,
  cohorts = [],
  now,
  received = {},
  attendance = {},
  engagement = [],
  most_active = [],
  requests = [],
  alerts = [],
  ranks = {},
  rank_labels = {},
  assignments = [],
  funnel = {},
  stage_labels = {},
  notice,
  error,
}) {
  const total = funnel.fellows || 0

  return (
    <Stack gap={5}>
      <PageHeader title="Staff dashboard">
        Cohort {cohort_id}, as of {fmtStamp(now)}.
      </PageHeader>

      <Notices notice={notice} error={error} />

      <Stack direction="horizontal" gap={3} align="end" wrap="wrap">
        <CohortFilter
          cohorts={cohorts}
          selected={cohort_id}
          includeAll={false}
          hrefFor={(cohort) => dashboardUrl({cohort})}
        />
        <Button label="Export CSV" href={`/dashboard/export.csv?cohort=${cohort_id}`} />
      </Stack>

      <Grid columns={{minWidth: 200, repeat: 'fit'}} gap={3}>
        <StatTile
          value={rate(attendance.rate)}
          label="Overall attendance"
          detail={
            attendance.sessions_held
              ? `${attendance.attended} of ${attendance.active_fellows * attendance.sessions_held}: ${attendance.active_fellows} fellows across ${attendance.sessions_held} sessions`
              : 'no sessions held yet'
          }
        />
        <StatTile value={attendance.active_fellows} label="Active fellows" />
        <StatTile value={requests.length} label="Open check-in requests" />
        <StatTile value={alerts.length} label="Unrostered Slack accounts" />
      </Grid>

      <MetadataList columns="multi" title={<Text type="label">Last data in</Text>}>
        {Object.entries(received).map(([source, at]) => (
          <MetadataListItem key={source} label={SOURCE_LABELS[source] || source}>
            {at ? fmtStamp(at) : 'never'}
          </MetadataListItem>
        ))}
      </MetadataList>

      {requests.length || alerts.length ? (
        <Region
          title="Needs a human"
          description="Neither of these closes itself. A check-in request closes when somebody is marked as having reached out; an unrostered account closes in Slack."
        >
          <List density="compact" hasDividers>
            {requests.map((row) => (
              <ListItem
                key={row.intervention_id}
                label={`${row.full_name} asked to be checked in on`}
                description={
                  row.note
                    ? `${fmtDateTime(row.created_at)} — ${row.note}`
                    : fmtDateTime(row.created_at)
                }
                endContent={<Token label="check-in request" color="orange" size="sm" />}
              />
            ))}
            {alerts.map((row) => (
              <ListItem
                key={row.alert_id}
                label={`${row.real_name || row.display_name || row.slack_user_id} joined Slack but is not on the roster`}
                description={`${row.email ? `${row.email} · ` : ''}In Slack: /link @them <fellow>, or /alerts resolve @them staff`}
                endContent={<Token label="unrostered" color="orange" size="sm" />}
              />
            ))}
          </List>
        </Region>
      ) : null}

      <Region
        title="Fellows, most attention-worthy first"
        description="The attention index combines Slack activity against the cohort mean, attendance, and how complete exit tickets are. It is a sorted list for a human, not a grade — the parts are shown so nobody has to trust the number. Asking for help never enters it, and neither do assignment scores."
      >
        {!engagement.length ? (
          <Card padding={5}>
            <EmptyState
              title="Nobody on the roster yet"
              description="Load one with cufa load-roster --csv <path> --cohort <id>."
              headingLevel={3}
            />
          </Card>
        ) : (
          <Table density="compact" dividers="rows" hasHover>
            <TableRow isHeaderRow>
              <TableHeaderCell>Fellow</TableHeaderCell>
              <TableHeaderCell>Index</TableHeaderCell>
              <TableHeaderCell>Why</TableHeaderCell>
              <TableHeaderCell>Attendance</TableHeaderCell>
              <TableHeaderCell>Exit tickets</TableHeaderCell>
              <TableHeaderCell>Slack (7 days)</TableHeaderCell>
              <TableHeaderCell>Outreach</TableHeaderCell>
            </TableRow>
            {engagement.map((row) => (
              <TableRow key={row.fellow_id}>
                <TableCell>
                  <Stack gap={0.5}>
                    <Link href={`/dashboard/fellow/${row.fellow_id}`}>{row.full_name}</Link>
                    <Text type="code">{row.fellow_id}</Text>
                  </Stack>
                </TableCell>
                <TableCell>
                  <Text weight="semibold" hasTabularNumbers>{row.attention_index}</Text>
                </TableCell>
                <TableCell>
                  <Stack direction="horizontal" gap={1} wrap="wrap">
                    {row.flags.map((flag) => (
                      <Token key={flag} label={flag} color="orange" size="sm" />
                    ))}
                    {row.open_check_in_requests ? (
                      <Token label="check-in request" color="orange" size="sm" />
                    ) : null}
                  </Stack>
                </TableCell>
                <TableCell>
                  <Stack gap={0.5}>
                    <Text hasTabularNumbers>{row.attended} / {row.sessions_held}</Text>
                    {row.needs_review ? (
                      <Text type="supporting">{row.needs_review} being checked</Text>
                    ) : null}
                  </Stack>
                </TableCell>
                <TableCell>
                  <Stack gap={0.5}>
                    <Text hasTabularNumbers>{row.forms_submitted} / {row.forms_expected}</Text>
                    {row.form_completeness === null || row.form_completeness === undefined ? null : (
                      <Text type="supporting">{percent(row.form_completeness)} complete</Text>
                    )}
                  </Stack>
                </TableCell>
                <TableCell>
                  <Stack gap={0.5}>
                    <Text hasTabularNumbers>{row.messages_7d} this week</Text>
                    <Text type="supporting">
                      {row.messages} total · mean {row.cohort_mean_messages}
                    </Text>
                  </Stack>
                </TableCell>
                <TableCell><OutreachCell row={row} cohort={cohort_id} /></TableCell>
              </TableRow>
            ))}
          </Table>
        )}
      </Region>

      <Region title="Most active this week" description="Fellow-facing channels only, over the last 7 days.">
        {!most_active.length ? (
          <Text type="supporting">No Slack messages in the last 7 days.</Text>
        ) : (
          <List density="compact" hasDividers>
            {most_active.map((row) => (
              <ListItem
                key={row.fellow_id}
                label={row.full_name}
                endContent={
                  <Text type="supporting" hasTabularNumbers>
                    {row.messages} messages
                  </Text>
                }
              />
            ))}
          </List>
        )}
      </Region>

      <Region
        title="Badges and ranks"
        description="Staff view. Fellows see only their own badges, by DM, and can switch them off. Shoutouts are ranked by giving, not receiving."
      >
        <Grid columns={{minWidth: 240, repeat: 'fit'}} gap={3}>
          {Object.entries(ranks).map(([key, rows]) => (
            <Card key={key}>
              <Stack gap={2}>
                <Text type="label">{rank_labels[key] || key}</Text>
                {rows.length ? (
                  <List density="compact" listStyle="decimal">
                    {rows.map((row) => (
                      <ListItem
                        key={row.fellow_id}
                        label={row.full_name}
                        endContent={
                          <Text type="supporting" hasTabularNumbers>{row.value}</Text>
                        }
                      />
                    ))}
                  </List>
                ) : (
                  <Text type="supporting">No data yet.</Text>
                )}
              </Stack>
            </Card>
          ))}
        </Grid>
      </Region>

      <Region
        title="Assignments and scores"
        description="Scores are entered by staff against CU's own rubric. Nothing here is graded by the system."
      >
        {!assignments.length ? (
          <Card padding={5}>
            <EmptyState
              title="No assignments yet"
              description="Create one in Slack with /assignment create, or from the command line with cufa assignment create."
              headingLevel={3}
            />
          </Card>
        ) : (
          <Stack gap={5}>
            {assignments.map((assignment) => (
              <Assignment
                key={assignment.assignment_id}
                assignment={assignment}
                cohort={cohort_id}
              />
            ))}
          </Stack>
        )}
      </Region>

      <Region title="Funnel" description={`Where ${total} fellows have got to.`}>
        <Stack gap={3}>
          {Object.entries(stage_labels).map(([stage, label]) => (
            <ProgressBar
              key={stage}
              label={label}
              value={(funnel.counts || {})[stage] || 0}
              max={total || 1}
              hasValueLabel
              formatValueLabel={(value, max) => `${value} of ${max}`}
            />
          ))}
          <MetadataList columns="multi" title={<Text type="label">Median days between stages</Text>}>
            {Object.entries(funnel.median_days || {}).map(([pair, days]) => {
              const [from, to] = pair.split('->')
              return (
                <MetadataListItem
                  key={pair}
                  label={`${stage_labels[from] || from} → ${stage_labels[to] || to}`}
                >
                  {days === null || days === undefined ? '—' : days}
                </MetadataListItem>
              )
            })}
          </MetadataList>
        </Stack>
      </Region>
    </Stack>
  )
}
