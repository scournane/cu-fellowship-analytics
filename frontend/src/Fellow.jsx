import {Button} from '@astryxdesign/core/Button'
import {Card} from '@astryxdesign/core/Card'
import {Grid} from '@astryxdesign/core/Grid'
import {Heading} from '@astryxdesign/core/Heading'
import {Link} from '@astryxdesign/core/Link'
import {List, ListItem} from '@astryxdesign/core/List'
import {MetadataList, MetadataListItem} from '@astryxdesign/core/MetadataList'
import {ProgressBar} from '@astryxdesign/core/ProgressBar'
import {Stack} from '@astryxdesign/core/Stack'
import {Text} from '@astryxdesign/core/Text'
import {Token} from '@astryxdesign/core/Token'
import * as stylex from '@stylexjs/stylex'

import {InlineField, PageHeader, PostForm} from './AppFrame.jsx'
import {fmtDate, fmtDateTime, fmtStamp} from './format.js'

// The one thing this screen sets by hand, and it sets a keyword rather than a
// measurement. The theme's chunky bar puts its height on the progress bar's
// ROOT, but the root is a column with the label row stacked above the track —
// so the track is the flex child that gets squeezed, all the way to nothing,
// and the bar vanishes wherever its label is shown. A screen cannot reach the
// track (that is a theme target), but it can stop the root dictating a height
// the label already spends. The bar's own size then comes back from the theme,
// and this line stays right if the theme moves that height onto
// `progressbar-track`, which is where a thick bar belongs.
const styles = stylex.create({
  meter: {height: 'auto'},
})

// The reminder lead times the bot offers, in the words a person would use.
const OFFSET_LABELS = {1440: '24 hours', 60: '1 hour', 10: '10 minutes'}

function offsetLabel(minutes) {
  return OFFSET_LABELS[minutes] || `${minutes} minutes`
}

function percent(value) {
  return `${Math.round(100 * (value || 0))}%`
}

/** A region of the screen: a heading, the line under it, then the content on
 *  white.
 *
 *  This used to open with a saturated band carrying an eyebrow as well — "6 SO
 *  FAR", "3 EARNED", "ACCOUNTS". Six bands down one page is wallpaper, so
 *  colour is now the exception: the stat blocks near the top keep it and
 *  nothing below them is filled. The eyebrows were counts assembled to fill a
 *  slot, and every one of them repeated something the list beneath it already
 *  showed, so none of them needed rescuing into the supporting line.
 *
 *  `AppFrame` exports a `Region` of its own, but it paints the heading accent
 *  green, and green headings all down a page is the band's rhythm in a thinner
 *  coat. Every other screen in the console writes a plain `Heading level={2}`,
 *  which is what this is. */
function Region({title, description, children}) {
  return (
    <Stack gap={3}>
      <Stack gap={1}>
        <Heading level={2}>{title}</Heading>
        {description ? <Text type="supporting">{description}</Text> : null}
      </Stack>
      {children}
    </Stack>
  )
}

/** One headline number on its own colour, what it counts, and how it was
 *  arrived at. These are the whole of the screen's colour.
 *
 *  `tone` names one of the theme's four lead variants — the only place this
 *  screen names a colour, and it names it rather than setting it. Everything
 *  written on the block asks for `color="inherit"`: a Text otherwise paints
 *  itself from its own StyleX class and would keep body ink on a saturated
 *  fill, so inheriting lets the card variant own both halves of the pair.
 *  Until the variant exists the card is an unfilled box and the same inherited
 *  ink stays readable on white, which is the fallback by design. */
function StatBlock({tone, value, label, detail}) {
  return (
    <Card variant={`lead-${tone}`} padding={5}>
      <Stack gap={1}>
        <Text type="display-2" color="inherit" hasTabularNumbers>{value}</Text>
        <Text color="inherit">{label}</Text>
        {detail ? <Text type="supporting" color="inherit">{detail}</Text> : null}
      </Stack>
    </Card>
  )
}

/** Yes or not yet, in words rather than a mark that has to be decoded.
 *  Off a table there is no column heading to say what the yes is about, so
 *  both words carry the thing they are about.
 *
 *  Green survives the trim here and nowhere else. This is the page a fellow
 *  opens to see how they are doing, and on it the colour is the feedback —
 *  the same reason the staff list, where eleven tinted pills were scanning
 *  noise, keeps its pills plain. */
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
    <Stack gap={2}>
      <Text weight="bold">{label}</Text>
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
 *  that records somebody has spoken to this fellow.
 *
 *  Nothing in here ever reaches a fellow's own copy of this page — the one
 *  caller renders it behind `staff_view`, and everything it shows (the index,
 *  the flags, the cohort mean, whether anyone has reached out) is triage that
 *  belongs to staff. It stays deliberately plain: a colour block would make
 *  the staff apparatus the loudest thing on a page about one person. */
function StaffSummary({fellow, engagement, aliases = []}) {
  const flags = engagement.flags || []
  return (
    <Card padding={5}>
      <Stack gap={4}>
        <Stack direction="horizontal" gap={4} align="center" wrap="wrap">
          {/* The caption stays here, unlike on the dashboard: this number has
              no region heading above it to say what it is. */}
          <Stack gap={0}>
            <Text type="display-3" hasTabularNumbers>{engagement.attention_index}</Text>
            <Text type="supporting">Attention index</Text>
          </Stack>
          {flags.length ? (
            <Stack direction="horizontal" gap={1} wrap="wrap">
              {flags.map((flag) => (
                <Token key={flag} label={flag} size="sm" />
              ))}
            </Stack>
          ) : (
            <Text type="supporting">nothing flagged</Text>
          )}
        </Stack>
        <MetadataList columns="multi">
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

/** One session, as the person who sat in it would read it back. */
function SessionCard({session}) {
  return (
    <Card padding={5}>
      <Stack gap={3}>
        <Stack gap={0.5}>
          <Heading level={3}>{session.title}</Heading>
          <Text type="supporting">{fmtStamp(session.scheduled_at_utc)}</Text>
        </Stack>
        <Stack direction="horizontal" gap={2} wrap="wrap" align="center">
          {session.attended ? (
            <Token label="attended" color="green" />
          ) : session.under_review ? (
            <Token label="being checked" />
          ) : (
            <Text type="supporting">not attended</Text>
          )}
          <YesNo value={session.exit_ticket} yes="exit ticket in" no="no exit ticket yet" />
        </Stack>
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
  // A server with nothing to say about a list may say so with a null rather
  // than an empty array, so each is read through a local default as well as
  // the parameter's.
  const held = sessions || []
  const earned = badges || []
  const set = assignments || []
  const steps = journey || []
  const logged = interventions || []
  const recordings = airtime || []
  const accounts = slack || []
  const links = connected || {}
  const reached = steps.filter((step) => step.at).length

  return (
    <Stack gap={6}>
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
        <StaffSummary fellow={fellow} engagement={engagement} aliases={aliases || []} />
      ) : null}

      {engagement ? (
        <Grid columns={{minWidth: 280, repeat: 'fit'}} gap={3}>
          <StatBlock
            tone="green"
            value={`${engagement.attended} / ${engagement.sessions_held}`}
            label="Sessions attended"
            detail={
              engagement.needs_review
                ? `${engagement.needs_review} still being checked`
                : undefined
            }
          />
          <StatBlock
            tone="blue"
            value={`${engagement.forms_submitted} / ${engagement.forms_expected}`}
            label="Exit tickets"
            detail={
              engagement.form_completeness === null ||
              engagement.form_completeness === undefined
                ? undefined
                : `${percent(engagement.form_completeness)} of the fields answered`
            }
          />
          <StatBlock
            tone="purple"
            value={engagement.messages}
            label="Slack messages"
            detail={`${engagement.messages_7d} in the last 7 days`}
          />
        </Grid>
      ) : null}

      <Region title="Sessions">
        {held.length ? (
          <Stack gap={6}>
            {held.map((session) => (
              <SessionCard key={session.session_id} session={session} />
            ))}
          </Stack>
        ) : (
          <Text type="supporting">No sessions yet.</Text>
        )}
      </Region>

      <Region title="Badges">
        {earned.length ? (
          <Grid columns={{minWidth: 300, repeat: 'fit'}} gap={3}>
            {earned.map((badge) => (
              <Card key={badge.badge_key} padding={5}>
                <Stack gap={3}>
                  <Stack gap={0.5}>
                    <Heading level={3}>{badge.label}</Heading>
                    <Text type="supporting">{badge.description}</Text>
                  </Stack>
                  <ProgressBar
                    label={`level ${badge.level} of ${badge.max_level}`}
                    value={badge.level}
                    max={badge.max_level || 1}
                    xstyle={styles.meter}
                  />
                </Stack>
              </Card>
            ))}
          </Grid>
        ) : (
          <Text type="supporting">
            None yet — check in to a session and one appears.
          </Text>
        )}
      </Region>

      {set.length ? (
        <Region title="Assignments">
          <Stack gap={6}>
            {set.map((assignment) => (
              <Card key={assignment.assignment_id} padding={5}>
                <Stack direction="horizontal" gap={4} wrap="wrap" justify="between" align="center">
                  <Stack gap={2}>
                    <Stack gap={0.5}>
                      <Heading level={3}>{assignment.title}</Heading>
                      <Text type="supporting">Due {fmtStamp(assignment.due_at_utc)}</Text>
                    </Stack>
                    <Stack direction="horizontal" gap={2} wrap="wrap" align="center">
                      <YesNo value={assignment.submitted_at_utc} yes="submitted" />
                    </Stack>
                  </Stack>
                  <Stack gap={0}>
                    <Text type="display-3" hasTabularNumbers>
                      {assignment.score === null || assignment.score === undefined
                        ? '—'
                        : `${assignment.score}${assignment.max_score ? ` / ${assignment.max_score}` : ''}`}
                    </Text>
                    <Text type="supporting">Score</Text>
                  </Stack>
                </Stack>
              </Card>
            ))}
          </Stack>
        </Region>
      ) : null}

      {/* The card this region used to sit in has gone: a heading with one card
          under it was two containers doing one container's work, and the bar
          and the list are already parted by the list's own dividers. */}
      <Region title={staff_view ? 'Journey' : 'Your journey'}>
        <Stack gap={4}>
          {steps.length ? (
            <ProgressBar
              label="Steps reached"
              value={reached}
              max={steps.length}
              hasValueLabel
              formatValueLabel={(value) => `${value} / ${steps.length}`}
              xstyle={styles.meter}
            />
          ) : null}
          {steps.length ? (
            <List hasDividers>
              {steps.map((step) => (
                <ListItem
                  key={step.stage}
                  label={step.label}
                  endContent={
                    step.at ? (
                      <Text weight="bold" hasTabularNumbers>{fmtDate(step.at)}</Text>
                    ) : (
                      <Text type="supporting">not yet</Text>
                    )
                  }
                />
              ))}
            </List>
          ) : (
            <Text type="supporting">Nothing recorded yet.</Text>
          )}
        </Stack>
      </Region>

      <Region title="What is connected">
        <MetadataList columns="multi">
          <MetadataListItem label="Slack">
            {/* align, or the token stretches to the column and stops
                reading as a pill. */}
            <Stack gap={1} align="start">
              <YesNo value={links.slack} yes="connected" no="not connected" />
              {accounts.map((account) => (
                <Text key={account.slack_user_id} type="supporting">
                  {account.real_name || account.display_name} · matched by{' '}
                  {account.match_method}
                  {account.tz ? ` · ${account.tz}` : ''}
                </Text>
              ))}
            </Stack>
          </MetadataListItem>
          <MetadataListItem label="Mid-session check-in form">
            <YesNo value={links.forms_part_a} yes="connected" no="nothing yet" />
          </MetadataListItem>
          <MetadataListItem label="Exit ticket form">
            <YesNo value={links.forms_part_b} yes="connected" no="nothing yet" />
          </MetadataListItem>
        </MetadataList>
      </Region>

      {staff_view && recordings.length ? (
        <Region title="Airtime on recordings">
          <Stack gap={6}>
            {recordings.map((row, index) => (
              <Card key={`${row.session}-${index}`} padding={5}>
                <Stack
                  direction="horizontal"
                  gap={4}
                  wrap="wrap"
                  justify="between"
                  align="center"
                >
                  <Stack gap={0.5}>
                    <Heading level={3}>{row.session}</Heading>
                    <Text type="supporting">
                      {row.turns} turns · {row.words} words
                    </Text>
                  </Stack>
                  <Stack gap={0}>
                    <Text type="display-3" hasTabularNumbers>{percent(row.share)}</Text>
                    <Text type="supporting">Share of speaking time</Text>
                  </Stack>
                </Stack>
              </Card>
            ))}
          </Stack>
        </Region>
      ) : null}

      {staff_view ? (
        <Region title="Interventions">
          {logged.length ? (
            <Stack gap={6}>
              {logged.map((row) => (
                <Card key={row.intervention_id} padding={5}>
                  <Stack gap={3}>
                    <Stack direction="horizontal" gap={2} wrap="wrap" align="center">
                      <Token label={row.kind.replace(/_/g, ' ')} size="sm" />
                      <Text type="supporting">{fmtDateTime(row.created_at)}</Text>
                    </Stack>
                    <Text>{row.note || '—'}</Text>
                    <MetadataList columns="multi">
                      <MetadataListItem label="By">
                        {row.by_email || row.by_slack_user || '—'}
                      </MetadataListItem>
                      <MetadataListItem label="Resolved">
                        {row.resolved_at ? fmtDateTime(row.resolved_at) : 'open'}
                      </MetadataListItem>
                    </MetadataList>
                  </Stack>
                </Card>
              ))}
            </Stack>
          ) : (
            <Text type="supporting">None.</Text>
          )}
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
          <Stack gap={4}>
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
            {/* No caption over this one: the button says "Badge messages: on",
                so a label above it was the same words twice. */}
            <PostForm action={`/me/${token}/prefs`}>
              <input type="hidden" name="kind" value="gamification" />
              <input
                type="hidden"
                name="enabled"
                value={preferences.gamification ? 'off' : 'on'}
              />
              <Button
                label={`Badge messages: ${preferences.gamification ? 'on' : 'off'}`}
                size="sm"
                type="submit"
                variant={preferences.gamification ? 'primary' : 'secondary'}
              />
            </PostForm>
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
