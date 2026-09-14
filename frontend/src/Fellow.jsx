import {Button} from '@astryxdesign/core/Button'
import {Card} from '@astryxdesign/core/Card'
import {Grid} from '@astryxdesign/core/Grid'
import {Link} from '@astryxdesign/core/Link'
import {List, ListItem} from '@astryxdesign/core/List'
import {MetadataList, MetadataListItem} from '@astryxdesign/core/MetadataList'
import {Stack} from '@astryxdesign/core/Stack'
import {Table, TableCell, TableHeaderCell, TableRow} from '@astryxdesign/core/Table'
import {Text} from '@astryxdesign/core/Text'
import {Token} from '@astryxdesign/core/Token'

import {InlineField, PageHeader, PostForm, Region, StatTile} from './AppFrame.jsx'
import {fmtDate, fmtDateTime, fmtStamp} from './format.js'

// The reminder lead times the bot offers, in the words a person would use.
const OFFSET_LABELS = {1440: '24 hours', 60: '1 hour', 10: '10 minutes'}

function offsetLabel(minutes) {
  return OFFSET_LABELS[minutes] || `${minutes} minutes`
}

function percent(value) {
  return `${Math.round(100 * (value || 0))}%`
}

/** Yes or not yet, in words rather than a mark that has to be decoded. */
function YesNo({value, yes = 'yes', no = 'not yet'}) {
  return value ? (
    <Token label={yes} color="green" size="sm" />
  ) : (
    <Text type="supporting">{no}</Text>
  )
}

/** One reminder kind, and a button per lead time that posts the opposite of
 *  what it currently is. The state is in the label, not only in the colour. */
function ReminderRow({kind, label, chosen = [], offsets = [], token}) {
  return (
    <Stack gap={1}>
      <Text type="label">{label}</Text>
      <Stack direction="horizontal" gap={2} wrap="wrap">
        {offsets.map((offset) => {
          const on = chosen.includes(offset)
          return (
            <PostForm key={offset} action={`/me/${token}/prefs`}>
              <input type="hidden" name="kind" value={kind} />
              <input type="hidden" name="offset" value={offset} />
              <input type="hidden" name="enabled" value={on ? 'off' : 'on'} />
              <Button
                label={`${offsetLabel(offset)}: ${on ? 'on' : 'off'}`}
                size="sm"
                type="submit"
                variant={on ? 'primary' : 'secondary'}
              />
            </PostForm>
          )
        })}
      </Stack>
    </Stack>
  )
}

/** The staff-only header: the attention index with its parts, and the toggle
 *  that records somebody has spoken to this fellow. */
function StaffSummary({fellow, engagement, aliases = []}) {
  return (
    <Card>
      <Stack gap={3}>
        <MetadataList columns="multi">
          <MetadataListItem label="Attention index">
            {engagement.attention_index}
          </MetadataListItem>
          <MetadataListItem label="Why">
            {engagement.flags.length ? (
              <Stack direction="horizontal" gap={1} wrap="wrap">
                {engagement.flags.map((flag) => (
                  <Token key={flag} label={flag} color="orange" size="sm" />
                ))}
              </Stack>
            ) : (
              'nothing flagged'
            )}
          </MetadataListItem>
          <MetadataListItem label="Slack">
            {engagement.messages} messages, cohort mean {engagement.cohort_mean_messages}
          </MetadataListItem>
          <MetadataListItem label="Reached out">
            {engagement.reached_out ? 'yes' : 'no'}
          </MetadataListItem>
          {engagement.open_check_in_requests ? (
            <MetadataListItem label="Open check-in requests">
              {engagement.open_check_in_requests}
            </MetadataListItem>
          ) : null}
          {aliases.length ? (
            <MetadataListItem label="Other addresses">
              {aliases.map((alias) => `${alias.email} (${alias.kind})`).join(', ')}
            </MetadataListItem>
          ) : null}
        </MetadataList>
        <PostForm action={`/dashboard/fellow/${fellow.fellow_id}/outreach`}>
          <input type="hidden" name="cohort" value={fellow.cohort_id} />
          {engagement.reached_out ? (
            <>
              <input type="hidden" name="action" value="clear" />
              <Button label="Clear reached out" size="sm" type="submit" />
            </>
          ) : (
            <>
              <input type="hidden" name="action" value="mark" />
              <InlineField
                label={`Note about reaching out to ${fellow.full_name}`}
                name="note"
                placeholder="note (optional)"
                width={180}
              />
              <Button label="Mark reached out" size="sm" type="submit" variant="primary" />
            </>
          )}
        </PostForm>
      </Stack>
    </Card>
  )
}

export function Fellow({
  fellow = {},
  now,
  staff_view = false,
  token,
  engagement,
  sessions = [],
  badges = [],
  assignments = [],
  journey = [],
  connected = {},
  slack = [],
  preferences,
  offsets = [],
  aliases = [],
  interventions = [],
  airtime = [],
}) {
  return (
    <Stack gap={5}>
      <PageHeader title={fellow.full_name}>
        {staff_view
          ? `${fellow.fellow_id} · ${fellow.status} · ${fellow.primary_email} · as of ${fmtStamp(now)}`
          : `Your fellowship so far, as of ${fmtStamp(now)}.`}
      </PageHeader>

      {!staff_view ? (
        <Stack direction="horizontal" gap={3} wrap="wrap">
          <Button label="Export my data (CSV)" href={`/me/${token}/export.csv`} />
        </Stack>
      ) : null}

      {staff_view && engagement ? (
        <StaffSummary fellow={fellow} engagement={engagement} aliases={aliases} />
      ) : null}

      {engagement ? (
        <Grid columns={{minWidth: 200, repeat: 'fit'}} gap={3}>
          <StatTile
            value={`${engagement.attended} / ${engagement.sessions_held}`}
            label="Sessions attended"
            detail={engagement.needs_review ? `${engagement.needs_review} still being checked` : undefined}
          />
          <StatTile
            value={`${engagement.forms_submitted} / ${engagement.forms_expected}`}
            label="Exit tickets"
            detail={
              engagement.form_completeness === null || engagement.form_completeness === undefined
                ? undefined
                : `${percent(engagement.form_completeness)} of the fields answered`
            }
          />
          <StatTile
            value={engagement.messages}
            label="Slack messages"
            detail={`${engagement.messages_7d} in the last 7 days`}
          />
        </Grid>
      ) : null}

      <Region title="Sessions">
        <Table density="compact" dividers="rows">
          <TableRow isHeaderRow>
            <TableHeaderCell>Session</TableHeaderCell>
            <TableHeaderCell>When</TableHeaderCell>
            <TableHeaderCell>Attended</TableHeaderCell>
            <TableHeaderCell>Exit ticket</TableHeaderCell>
          </TableRow>
          {sessions.length ? (
            sessions.map((session) => (
              <TableRow key={session.session_id}>
                <TableCell>{session.title}</TableCell>
                <TableCell><Text type="supporting">{fmtStamp(session.scheduled_at_utc)}</Text></TableCell>
                <TableCell>
                  {session.attended ? (
                    <Token label="yes" color="green" size="sm" />
                  ) : session.under_review ? (
                    <Token label="being checked" size="sm" />
                  ) : (
                    <Text type="supporting">no</Text>
                  )}
                </TableCell>
                <TableCell><YesNo value={session.exit_ticket} /></TableCell>
              </TableRow>
            ))
          ) : (
            <TableRow>
              <TableCell><Text type="supporting">No sessions yet.</Text></TableCell>
            </TableRow>
          )}
        </Table>
      </Region>

      <Region title="Badges">
        {badges.length ? (
          <List density="compact" hasDividers>
            {badges.map((badge) => (
              <ListItem
                key={badge.badge_key}
                label={badge.label}
                description={badge.description}
                endContent={
                  <Text type="supporting">
                    level {badge.level} of {badge.max_level}
                  </Text>
                }
              />
            ))}
          </List>
        ) : (
          <Text type="supporting">
            None yet — check in to a session and one appears.
          </Text>
        )}
      </Region>

      {assignments.length ? (
        <Region title="Assignments">
          <Table density="compact" dividers="rows">
            <TableRow isHeaderRow>
              <TableHeaderCell>Assignment</TableHeaderCell>
              <TableHeaderCell>Due</TableHeaderCell>
              <TableHeaderCell>Handed in</TableHeaderCell>
              <TableHeaderCell>Score</TableHeaderCell>
            </TableRow>
            {assignments.map((assignment) => (
              <TableRow key={assignment.assignment_id}>
                <TableCell>{assignment.title}</TableCell>
                <TableCell>
                  <Text type="supporting">{fmtStamp(assignment.due_at_utc)}</Text>
                </TableCell>
                <TableCell><YesNo value={assignment.submitted_at_utc} yes="submitted" /></TableCell>
                <TableCell>
                  <Text hasTabularNumbers>
                    {assignment.score === null || assignment.score === undefined
                      ? '—'
                      : `${assignment.score}${assignment.max_score ? ` / ${assignment.max_score}` : ''}`}
                  </Text>
                </TableCell>
              </TableRow>
            ))}
          </Table>
        </Region>
      ) : null}

      <Region title={staff_view ? 'Journey' : 'Your journey'}>
        <List density="compact" hasDividers>
          {journey.map((step) => (
            <ListItem
              key={step.stage}
              label={step.label}
              endContent={
                <Text type="supporting">
                  {step.at ? fmtDate(step.at) : 'not yet'}
                </Text>
              }
            />
          ))}
        </List>
      </Region>

      <Region title="What is connected">
        <MetadataList columns="multi">
          <MetadataListItem label="Slack">
            <Stack gap={1}>
              <YesNo value={connected.slack} yes="connected" no="not connected" />
              {slack.map((account) => (
                <Text key={account.slack_user_id} type="supporting">
                  {account.real_name || account.display_name} · matched by {account.match_method}
                  {account.tz ? ` · ${account.tz}` : ''}
                </Text>
              ))}
            </Stack>
          </MetadataListItem>
          <MetadataListItem label="Mid-session check-in form">
            <YesNo value={connected.forms_part_a} yes="connected" no="nothing yet" />
          </MetadataListItem>
          <MetadataListItem label="Exit ticket form">
            <YesNo value={connected.forms_part_b} yes="connected" no="nothing yet" />
          </MetadataListItem>
        </MetadataList>
      </Region>

      {staff_view && airtime.length ? (
        <Region title="Airtime on recordings">
          <Table density="compact" dividers="rows">
            <TableRow isHeaderRow>
              <TableHeaderCell>Session</TableHeaderCell>
              <TableHeaderCell>Share of speaking time</TableHeaderCell>
              <TableHeaderCell>Turns</TableHeaderCell>
              <TableHeaderCell>Words</TableHeaderCell>
            </TableRow>
            {airtime.map((row, index) => (
              <TableRow key={`${row.session}-${index}`}>
                <TableCell>{row.session}</TableCell>
                <TableCell><Text hasTabularNumbers>{percent(row.share)}</Text></TableCell>
                <TableCell><Text hasTabularNumbers>{row.turns}</Text></TableCell>
                <TableCell><Text hasTabularNumbers>{row.words}</Text></TableCell>
              </TableRow>
            ))}
          </Table>
        </Region>
      ) : null}

      {staff_view ? (
        <Region title="Interventions">
          <Table density="compact" dividers="rows">
            <TableRow isHeaderRow>
              <TableHeaderCell>When</TableHeaderCell>
              <TableHeaderCell>Kind</TableHeaderCell>
              <TableHeaderCell>By</TableHeaderCell>
              <TableHeaderCell>Note</TableHeaderCell>
              <TableHeaderCell>Resolved</TableHeaderCell>
            </TableRow>
            {interventions.length ? (
              interventions.map((row) => (
                <TableRow key={row.intervention_id}>
                  <TableCell><Text type="supporting">{fmtDateTime(row.created_at)}</Text></TableCell>
                  <TableCell>{row.kind.replace(/_/g, ' ')}</TableCell>
                  <TableCell>
                    <Text type="supporting">{row.by_email || row.by_slack_user || '—'}</Text>
                  </TableCell>
                  <TableCell>{row.note || '—'}</TableCell>
                  <TableCell>
                    <Text type="supporting">
                      {row.resolved_at ? fmtDateTime(row.resolved_at) : 'open'}
                    </Text>
                  </TableCell>
                </TableRow>
              ))
            ) : (
              <TableRow>
                <TableCell><Text type="supporting">None.</Text></TableCell>
              </TableRow>
            )}
          </Table>
        </Region>
      ) : null}

      <Region title="Preferences">
        {!preferences ? (
          <Text type="supporting">
            Join the Slack workspace to get reminders. Preferences live on the Slack account.
          </Text>
        ) : staff_view ? (
          <MetadataList columns="multi">
            <MetadataListItem label="Session reminders">
              {preferences.session_reminders.length
                ? preferences.session_reminders.map(offsetLabel).join(', ') + ' before'
                : 'off'}
            </MetadataListItem>
            <MetadataListItem label="Assignment reminders">
              {preferences.assignment_reminders.length
                ? preferences.assignment_reminders.map(offsetLabel).join(', ') + ' before'
                : 'off'}
            </MetadataListItem>
            <MetadataListItem label="Badge messages">
              {preferences.gamification ? 'on' : 'off'}
            </MetadataListItem>
          </MetadataList>
        ) : (
          <Stack gap={3}>
            <ReminderRow
              kind="session"
              label="Session reminders"
              chosen={preferences.session_reminders}
              offsets={offsets}
              token={token}
            />
            <ReminderRow
              kind="assignment"
              label="Assignment reminders"
              chosen={preferences.assignment_reminders}
              offsets={offsets}
              token={token}
            />
            <Stack gap={1}>
              <Text type="label">Badge messages</Text>
              <PostForm action={`/me/${token}/prefs`}>
                <input type="hidden" name="kind" value="gamification" />
                <input type="hidden" name="enabled" value={preferences.gamification ? 'off' : 'on'} />
                <Button
                  label={`Badge messages: ${preferences.gamification ? 'on' : 'off'}`}
                  size="sm"
                  type="submit"
                  variant={preferences.gamification ? 'primary' : 'secondary'}
                />
              </PostForm>
            </Stack>
            <Text type="supporting">
              Each button turns that reminder on or off. The same settings are in Slack
              with /reminders and /badges.
            </Text>
          </Stack>
        )}
      </Region>

      {!staff_view ? (
        <Text type="supporting">
          This page shows your own data and nobody else&apos;s. The link expires after
          7 days; ask the bot for a new one with /dashboard.
        </Text>
      ) : (
        <Text type="supporting">
          <Link href={`/dashboard?cohort=${fellow.cohort_id}`}>Back to the staff dashboard</Link>
        </Text>
      )}
    </Stack>
  )
}
