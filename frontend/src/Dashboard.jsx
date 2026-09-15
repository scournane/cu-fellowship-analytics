import {Blockquote} from '@astryxdesign/core/Blockquote'
import {Button} from '@astryxdesign/core/Button'
import {Card} from '@astryxdesign/core/Card'
import {Divider} from '@astryxdesign/core/Divider'
import {EmptyState} from '@astryxdesign/core/EmptyState'
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

import {InlineField, Notices, PageHeader, PostForm} from './AppFrame.jsx'
import {CohortFilter} from './CohortFilter.jsx'
import {Mascot} from './Mascot.jsx'
import {fmtDateTime, fmtStamp} from './format.js'

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

// What each row of `received` is. The keys are the names the sync module uses;
// anything it grows later falls back to its own key rather than disappearing.
const SOURCE_LABELS = {
  slack_message: 'Slack messages',
  slack_sync: 'Slack sync',
  part_a: 'Check-ins (part A)',
  part_b: 'Exit tickets (part B)',
}

/** What puts a name on each leaderboard, for the boards that have none yet.
 *
 *  An empty board used to say "No data yet.", which tells a staff member
 *  nothing they could act on — five identical shrugs side by side. These say
 *  what the board is waiting for, and they are one sentence each because the
 *  card they sit in is a third of a column wide. Keyed by `ranks`' own keys
 *  (`cufa.slack.badges.RANK_KEYS`); a board that set grows later still gets a
 *  sentence, from the fallback at the point of use. */
const RANK_WAITING_FOR = {
  checkins: 'Fills as fellows check in to sessions.',
  streak: 'Fills once somebody attends two sessions running.',
  messages: 'Fills from the fellow-facing Slack channels.',
  shoutouts_given: 'Fills when a fellow names someone who helped them.',
  exit_tickets: 'Fills as end-of-session forms come in.',
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

/** "1 session" / "3 sessions". */
function count(n, noun) {
  return `${n} ${noun}${n === 1 ? '' : 's'}`
}

// ---- time, as a reader feels it ----------------------------------------
//
// Every stamp on this screen is printed absolute and labelled UTC, which is
// right for a record and useless for the one question a queue asks: how long
// has this been sitting there. So the same value is read a second way.
//
// The parsing is `format.js`'s, deliberately: the same five fields off the
// front of the string, anchored to UTC, so the relative reading and the
// printed stamp can never disagree about which instant they mean. Nothing
// here uses the browser's clock — the comparison is always against the page's
// own `now`, the instant the server rendered, so a tab left open overnight
// does not quietly age its own numbers.

const AT = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/

/** One of the server's stamps as milliseconds, or null when it is not one. */
function instant(value) {
  const m = AT.exec(String(value == null ? '' : value))
  if (!m) return null
  return Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]), Number(m[4]), Number(m[5]))
}

/** "2 days ago", from a millisecond reading. Null in, null out: a caller with
 *  no answer says nothing rather than guessing at one. Whole units only —
 *  "2 days" is the truth a person acts on, and "2.4 days" is a measurement
 *  nobody asked for. */
function since(ms, now) {
  const end = instant(now)
  if (ms === null || end === null) return null
  const hours = Math.floor((end - ms) / 3600000)
  if (hours < 1) return 'in the last hour'
  if (hours < 24) return `${count(hours, 'hour')} ago`
  return `${count(Math.floor(hours / 24), 'day')} ago`
}

/** "2 days ago", from one of the server's stamps. */
function ago(then, now) {
  return since(instant(then), now)
}

/** How long the oldest thing in a queue has been waiting. The rows arrive in
 *  `created_at` order, but the minimum is taken rather than the first, so this
 *  stays right if that order ever changes. */
function oldest(rows, now) {
  const times = rows.map((row) => instant(row.created_at)).filter((t) => t !== null)
  return times.length ? since(Math.min(...times), now) : null
}

// ---- what the numbers mean ---------------------------------------------

/** The line under the attendance percentage.
 *
 *  "67%" is accurate and says nothing about whether it is good, and there is
 *  nothing in this data to say that with: no target, no previous week, no
 *  other cohort. What there is, is the spread the average is hiding — the
 *  per-fellow rates the list below is sorted by. A cohort on 67% where
 *  everybody is on 67% and one where half the room is at 20% are the same
 *  number and completely different Mondays, and that is the honest thing to
 *  add. No names: this is the shape of the cohort, not a ranking of it. */
function attendanceDetail(stats, fellows) {
  if (!stats.sessions_held) {
    return 'Nothing to measure until the first session is held.'
  }
  const possible = (stats.active_fellows || 0) * stats.sessions_held
  const counted = `${stats.attended} of ${possible}: ${stats.active_fellows} fellows across ${count(stats.sessions_held, 'session')}`
  const rates = fellows
    .map((row) => row.attendance_rate)
    .filter((value) => value !== null && value !== undefined)
  if (!rates.length) return counted
  const low = Math.min(...rates)
  const high = Math.max(...rates)
  if (low === high) return `${counted}. Every fellow is on ${percent(low)}.`
  return `${counted}. Fellow by fellow it runs ${percent(low)} to ${percent(high)}.`
}

/** A region of the screen: a heading, the line under it, then the content on
 *  white.
 *
 *  This used to open with a saturated band carrying an eyebrow as well. Six
 *  bands down one page is wallpaper, so colour is now the exception: the four
 *  stat blocks at the top keep it and are the only filled thing above the
 *  primary buttons. The eyebrows were assembled counts — "QUEUE · 2 OPEN",
 *  "6 FELLOWS" — and are gone rather than reworded; where a count earns its
 *  place it is a sentence in the supporting line.
 *
 *  `AppFrame` exports a `Region` of its own, but it paints the heading accent
 *  green, and six green headings down a page is the band's rhythm in a thinner
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

/** What a region says when it has nothing in it.
 *
 *  An empty region is the first thing a staff member sees on a cohort's first
 *  morning, and a bare "No data yet." tells them neither what goes here nor
 *  whether something is broken. Two lines: what will fill this, and what they
 *  can do about it now. Supporting type, because an empty region should not
 *  shout louder than a full one. */
function Waiting({children}) {
  return (
    <Stack gap={1}>
      {[].concat(children).map((line) => (
        <Text key={line} type="supporting">{line}</Text>
      ))}
    </Stack>
  )
}

/** One headline number on its own colour, what it counts, and how it was
 *  arrived at. Four of these open the screen, and they are the whole of its
 *  colour.
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

/** A count against the count it is out of, drawn as the bar this whole screen
 *  is built around. A zero denominator would divide by nothing, so it draws an
 *  empty bar and still says what it is out of. */
function Meter({label, value, max, detail}) {
  return (
    <Stack gap={1}>
      <ProgressBar
        label={label}
        value={value || 0}
        max={max || 1}
        hasValueLabel
        formatValueLabel={(current) => `${current} / ${max || 0}`}
        xstyle={styles.meter}
      />
      {detail ? <Text type="supporting">{detail}</Text> : null}
    </Stack>
  )
}

/** The "has anyone spoken to them?" toggle, one fellow's worth.
 *
 *  A note field on the way in and nothing but a clear button on the way out:
 *  what was said is recorded once, at the moment somebody knows it. */
function Outreach({row, cohort}) {
  return (
    <PostForm action={`/dashboard/fellow/${row.fellow_id}/outreach`}>
      <input type="hidden" name="cohort" value={cohort || ''} />
      {row.reached_out ? (
        <>
          <Token label="reached out" />
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
            width={160}
          />
          <Button label="Reached out" size="sm" type="submit" variant="primary" />
        </>
      )}
    </PostForm>
  )
}

/** One fellow in the attention list: the index and the name, the three
 *  measurements the index is made of, then the one action there is to take.
 *  A card rather than a table row, because seven columns is a shape that only
 *  exists on a wide screen.
 *
 *  Four of the captions have gone. The number beside the name is the sort key
 *  of a region headed "most attention-worthy first", and the paragraph under
 *  that heading says what it is made of; the two bars label themselves; and
 *  the Slack count says what it counts on the line under it, where a caption
 *  over a number was saying it twice. Every number is still here.
 *
 *  What is added is only ever an explanation of a nothing. On the cohort's
 *  first morning every bar here reads "0 / 0" and the mean is 0, and a zero
 *  denominator is not a bad score — it is a session nobody has held and a form
 *  nobody has sent. The lines below say which, and say nothing at all once
 *  there is something to count. */
function FellowCard({row, cohort}) {
  const flags = row.flags || []
  return (
    <Card padding={5}>
      <Stack gap={4}>
        <Stack direction="horizontal" gap={4} align="center" wrap="wrap">
          <Text type="display-3" hasTabularNumbers>{row.attention_index}</Text>
          <Stack gap={0.5}>
            <Link href={`/dashboard/fellow/${row.fellow_id}`} isStandalone>
              {row.full_name}
            </Link>
            <Text type="code">{row.fellow_id}</Text>
          </Stack>
        </Stack>

        {flags.length || row.open_check_in_requests ? (
          <Stack direction="horizontal" gap={1} wrap="wrap">
            {flags.map((flag) => (
              <Token key={flag} label={flag} size="sm" />
            ))}
            {row.open_check_in_requests ? (
              <Token label="check-in request" size="sm" />
            ) : null}
          </Stack>
        ) : null}

        <Grid columns={{minWidth: 220, repeat: 'fit'}} gap={4}>
          <Meter
            label="Attendance"
            value={row.attended}
            max={row.sessions_held}
            detail={
              row.needs_review
                ? `${row.needs_review} being checked`
                : row.sessions_held
                  ? undefined
                  : 'no sessions held yet'
            }
          />
          <Meter
            label="Exit tickets"
            value={row.forms_submitted}
            max={row.forms_expected}
            detail={
              row.form_completeness === null || row.form_completeness === undefined
                ? row.forms_expected
                  ? undefined
                  : 'no forms sent yet'
                : `${percent(row.form_completeness)} complete`
            }
          />
          <Stack gap={1}>
            <Text type="display-3" hasTabularNumbers>{row.messages_7d}</Text>
            <Text type="supporting">
              Slack messages in the last 7 days · {row.messages} total ·{' '}
              {row.cohort_mean_messages
                ? `mean ${row.cohort_mean_messages}`
                : 'no cohort mean yet — nobody has posted'}
            </Text>
          </Stack>
        </Grid>

        <Outreach row={row} cohort={cohort} />
      </Stack>
    </Card>
  )
}

/** One fellow's score on one assignment, and the field staff type into.
 *  Scores come from a human with a rubric; the system only stores them.
 *
 *  A card, and not a row of a Table, for two reasons. A four-column row whose
 *  last column holds two text fields and a button has no narrow form to fall
 *  back to. And a Table bleeds out to the padding of whatever container it is
 *  in, reading `--container-padding-inline-*` off the nearest one that
 *  publishes them: in a Card that is the card's own edge, which is the point,
 *  but loose on the page it is LayoutContent's, so the table hung 16px past
 *  the content column on each side at every width — the constant overflow this
 *  screen used to have. Table belongs inside a Card here or nowhere. */
function ScoreCard({assignment, row, cohort}) {
  return (
    <Card padding={5}>
      <Stack gap={3}>
        <Stack gap={0.5}>
          <Heading level={4}>{row.full_name}</Heading>
          <Text type="supporting">
            {row.submitted_at_utc
              ? `Handed in ${fmtDateTime(row.submitted_at_utc)}`
              : 'Not handed in yet'}
          </Text>
        </Stack>
        {/* No "Score" caption: the field under it is placeholdered "score",
            and the region it sits in is headed "Assignments and scores". */}
        <Stack gap={0}>
          <Text type="display-3" hasTabularNumbers>
            {row.score === null || row.score === undefined
              ? '—'
              : `${row.score}${assignment.max_score ? ` / ${assignment.max_score}` : ''}`}
          </Text>
          {row.graded_by ? <Text type="supporting">entered by {row.graded_by}</Text> : null}
        </Stack>
        <PostForm action="/dashboard/score">
          <input type="hidden" name="cohort" value={cohort || ''} />
          <input type="hidden" name="assignment_id" value={assignment.assignment_id} />
          <input type="hidden" name="fellow_id" value={row.fellow_id} />
          <InlineField
            label={`Score for ${row.full_name}`}
            name="score"
            placeholder="score"
            width={80}
          />
          <InlineField
            label={`Note on ${row.full_name}'s score`}
            name="note"
            placeholder="note"
            width={120}
          />
          <Button label="Save" size="sm" type="submit" />
        </PostForm>
      </Stack>
    </Card>
  )
}

/** One assignment: what it is, how far the cohort has got with it, and a card
 *  per fellow to score. */
function Assignment({assignment, cohort}) {
  const rows = assignment.rows || []
  const seats = rows.length

  return (
    <Stack gap={4}>
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
        <Grid columns={{minWidth: 240, repeat: 'fit'}} gap={4}>
          <Meter label="Handed in" value={assignment.submitted} max={seats} />
          <Meter label="Scored" value={assignment.scored} max={seats} />
        </Grid>
      </Stack>
      {seats ? (
        <Grid columns={{minWidth: 300, repeat: 'fit'}} gap={3}>
          {rows.map((row) => (
            <ScoreCard key={row.fellow_id} assignment={assignment} row={row} cohort={cohort} />
          ))}
        </Grid>
      ) : (
        <Waiting>
          {[
            'Nobody on the roster to score yet.',
            'Load the fellows and a card appears here for each of them, whether they have handed anything in or not.',
          ]}
        </Waiting>
      )}
    </Stack>
  )
}

/** One open check-in request: somebody typed `/checkin` in Slack and asked to
 *  be spoken to.
 *
 *  This is the most human object on the page and it used to be laid out like a
 *  log line — the category chip first, then a heading, then a timestamp with
 *  the person's own words trailing after an em dash. Three changes, none of
 *  them colour and none of them a new control:
 *
 *  * The sentence comes first and the chip moves under it. A log leads with
 *    what kind of row it is; a message to somebody leads with what happened.
 *  * The note is the fellow's own writing — `cmd_checkin` records exactly what
 *    they typed — so it is set as the quotation it is, attributed to them,
 *    instead of being appended to a timestamp as metadata.
 *  * It says how long it has been waiting. That is the one fact that makes a
 *    queue feel like a queue, it is computed from the page's own `now`, and
 *    the absolute stamp stays beside it for the record. */
function CheckInRequest({row, now}) {
  const waited = ago(row.created_at, now)
  return (
    <Card padding={5}>
      <Stack gap={3}>
        <Stack gap={1}>
          <Heading level={3}>{row.full_name} asked to be checked in on</Heading>
          {/* The horizontal stack is what stops a lone Token stretching to the
              card and reading as a bar. */}
          <Stack direction="horizontal" gap={2} align="center" wrap="wrap">
            <Token label="check-in request" size="sm" />
            <Text type="supporting">
              {waited ? `asked ${waited} · ${fmtDateTime(row.created_at)}` : fmtDateTime(row.created_at)}
            </Text>
          </Stack>
        </Stack>
        {/* The cite names where it came from as well as who: `/checkin` is the
            only thing that writes this note, so saying so is what tells a
            staff member they are reading the fellow rather than reading a
            colleague's summary of the fellow. */}
        {row.note ? (
          <Blockquote cite={`${row.full_name}, /checkin in Slack`}>{row.note}</Blockquote>
        ) : null}
        <Stack direction="horizontal" gap={2} wrap="wrap">
          <Button
            label="Open their page"
            size="sm"
            href={`/dashboard/fellow/${row.fellow_id}`}
          />
        </Stack>
      </Stack>
    </Card>
  )
}

/** One Slack account nobody on the roster answers to.
 *
 *  Same shape as the request above, for the same reason, and the two ways of
 *  closing it are written as the choice they are rather than as one string of
 *  slash commands. Both commands are still here, spelled exactly as they were.
 *  The stamp is when they joined, which is what the heading is about. */
function UnrosteredAlert({row, now}) {
  const who = row.real_name || row.display_name || row.slack_user_id
  const joined = ago(row.joined_at_utc || row.created_at, now)
  const seen = row.joined_at_utc || row.created_at
  return (
    <Card padding={5}>
      <Stack gap={3}>
        <Stack gap={1}>
          <Heading level={3}>{who} joined Slack but is not on the roster</Heading>
          <Stack direction="horizontal" gap={2} align="center" wrap="wrap">
            <Token label="unrostered" size="sm" />
            <Text type="supporting">
              {[row.email, joined ? `joined ${joined}` : null, fmtDateTime(seen)]
                .filter(Boolean)
                .join(' · ')}
            </Text>
          </Stack>
        </Stack>
        <Text type="supporting">
          In Slack: /link @them &lt;fellow&gt; to put them on a roster row, or /alerts resolve
          @them staff to close this without counting anything.
        </Text>
      </Stack>
    </Card>
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
  // Every one of these is server state, and a server with nothing to say about
  // a list may say so with a null rather than an empty array — so each is read
  // through a local default as well as the parameter's.
  const fellows = engagement || []
  const openRequests = requests || []
  const openAlerts = alerts || []
  const active = most_active || []
  const work = assignments || []
  const stats = attendance || {}
  const rankNames = rank_labels || {}
  const arrivals = received || {}
  const boards = Object.entries(ranks || {})
  const stages = Object.entries(stage_labels || {})
  const sources = Object.entries(arrivals)
  const medians = Object.entries((funnel || {}).median_days || {})
  const total = (funnel || {}).fellows || 0
  const queue = openRequests.length + openAlerts.length
  const waitingRequest = oldest(openRequests, now)
  const syncedAgo = ago(arrivals.slack_sync, now)

  return (
    <Stack gap={6}>
      <PageHeader title="Staff dashboard">
        Cohort {cohort_id}, as of {fmtStamp(now)}.
      </PageHeader>

      <Notices notice={notice} error={error} />

      <Stack direction="horizontal" gap={3} align="end" wrap="wrap">
        <CohortFilter
          cohorts={cohorts || []}
          selected={cohort_id}
          includeAll={false}
          hrefFor={(cohort) => dashboardUrl({cohort})}
        />
        <Button label="Export CSV" href={`/dashboard/export.csv?cohort=${cohort_id}`} />
      </Stack>

      {/* The four numbers, each with the sentence that says what it means.
          None of them claims a trend: there is no previous week in this data
          and no target to be under, so the detail lines say what the number is
          made of, what it hides, or what it is costing — never whether anyone
          should be pleased. */}
      <Grid columns={{minWidth: 320, repeat: 'fit'}} gap={3}>
        <StatBlock
          tone="green"
          value={rate(stats.rate)}
          label="Overall attendance"
          detail={attendanceDetail(stats, fellows)}
        />
        <StatBlock
          tone="blue"
          value={stats.active_fellows}
          label="Active fellows"
          detail={
            stats.active_fellows
              ? 'The cohort every rate and mean below is measured against.'
              : 'Nothing below has a denominator until somebody is on the roster.'
          }
        />
        <StatBlock
          tone="purple"
          value={openRequests.length}
          label="Open check-in requests"
          detail={
            openRequests.length
              ? waitingRequest
                ? openRequests.length === 1
                  ? `Asked ${waitingRequest}.`
                  : `The oldest was asked ${waitingRequest}.`
                : undefined
              : 'Nobody is waiting to be checked in on.'
          }
        />
        <StatBlock
          tone="orange"
          value={openAlerts.length}
          label="Unrostered Slack accounts"
          detail={
            openAlerts.length
              ? 'Their messages count towards nobody here until each is linked to a fellow.'
              : arrivals.slack_sync
                ? 'Every Slack account the bot has seen answers to a roster row.'
                : 'Nothing has come from Slack yet, so there is nothing to match.'
          }
        />
      </Grid>

      {/* A heading with nothing under it is a stub, not an empty state: a
          cohort that has never synced anything says so by this region not
          being on the page.

          Each stamp is read twice — printed absolute for the record, and again
          as an age, which is the question this region is actually asked. */}
      {sources.length ? (
        <Region title="Last data in">
          <MetadataList columns="multi">
            {sources.map(([source, at]) => {
              const age = ago(at, now)
              return (
                <MetadataListItem key={source} label={SOURCE_LABELS[source] || source}>
                  {at ? [fmtStamp(at), age].filter(Boolean).join(' · ') : 'never'}
                </MetadataListItem>
              )
            })}
          </MetadataList>
        </Region>
      ) : null}

      {queue ? (
        <Region
          title="Needs a human"
          description={`${queue === 1 ? 'One thing is' : `${queue} things are`} waiting on a person here. Neither of these closes itself: a check-in request closes when somebody is marked as having reached out; an unrostered account closes in Slack.`}
        >
          <Stack gap={6}>
            {openRequests.map((row) => (
              <CheckInRequest key={row.intervention_id} row={row} now={now} />
            ))}
            {openAlerts.map((row) => (
              <UnrosteredAlert key={row.alert_id} row={row} now={now} />
            ))}
          </Stack>
        </Region>
      ) : null}

      <Region
        title="Fellows, most attention-worthy first"
        description="The attention index combines Slack activity against the cohort mean, attendance, and how complete exit tickets are. It is a sorted list for a human, not a grade — the parts are shown so nobody has to trust the number. Asking for help never enters it, and neither do assignment scores."
      >
        {!fellows.length ? (
          // The one place Ding appears, and the only state in which he can:
          // with no roster there is no queue, no leaderboard, no assignment and
          // no funnel either, so this is not a gap in a working screen — it is
          // the whole screen, and there is nothing here for a bell to get in
          // the way of. He is decoration beside a heading that already carries
          // the message, so `alt=""` keeps him out of the reading order rather
          // than announcing a second title.
          <Card padding={5}>
            <EmptyState
              icon={<Mascot size={120} alt="" />}
              title="Nobody on the roster yet"
              description="Every number on this page is built out of the roster, so this is the first thing to do. Load one with cufa load-roster --csv <path> --cohort <id>."
              headingLevel={3}
            />
          </Card>
        ) : (
          <Stack gap={6}>
            {fellows.map((row) => (
              <FellowCard key={row.fellow_id} row={row} cohort={cohort_id} />
            ))}
          </Stack>
        )}
      </Region>

      {/* No card around the list: the heading above it already says what it
          is, and the dividers already separate the rows. */}
      <Region
        title="Most active this week"
        description="Fellow-facing channels only, over the last 7 days."
      >
        {!active.length ? (
          // Empty here has two quite different causes and the staff member
          // cannot tell them apart by looking: a quiet week, or a bot that is
          // not in the channels. `received.slack_sync` is the one fact that
          // separates them, so it is on the page rather than left to be
          // guessed at. It reports the sync's age and stops there — whether
          // that age is too old is the reader's call, not this screen's.
          <Waiting>
            {arrivals.slack_sync
              ? [
                  'Nobody has posted in a fellow-facing channel in the last 7 days.',
                  `Slack itself last synced ${syncedAgo || fmtStamp(arrivals.slack_sync)} — if that is older than you expect, run cufa slack doctor.`,
                ]
              : [
                  'Nothing has come from Slack yet.',
                  'This fills once the bot is in the fellow-facing channels — cufa slack doctor checks it can see them.',
                ]}
          </Waiting>
        ) : (
          <List hasDividers listStyle="decimal">
            {active.map((row) => (
              <ListItem
                key={row.fellow_id}
                label={row.full_name}
                endContent={
                  <Text weight="bold" hasTabularNumbers>{row.messages} messages</Text>
                }
              />
            ))}
          </List>
        )}
      </Region>

      {/* These cards stay: five leaderboards side by side in a grid are five
          separate things, and the card is what tells one from the next. */}
      <Region
        title="Badges and ranks"
        description="Staff view. Fellows see only their own badges, by DM, and can switch them off. Shoutouts are ranked by giving, not receiving."
      >
        {!boards.length ? (
          <Waiting>
            {[
              'Nothing to rank yet.',
              'The first check-in, message, shoutout or exit ticket puts a name on a board.',
            ]}
          </Waiting>
        ) : (
          <Grid columns={{minWidth: 280, repeat: 'fit'}} gap={3}>
            {boards.map(([key, rows]) => (
              <Card key={key} padding={5}>
                <Stack gap={3}>
                  <Heading level={4}>{rankNames[key] || key}</Heading>
                  {(rows || []).length ? (
                    <List hasDividers listStyle="decimal">
                      {(rows || []).map((row) => (
                        <ListItem
                          key={row.fellow_id}
                          label={row.full_name}
                          endContent={<Text weight="bold" hasTabularNumbers>{row.value}</Text>}
                        />
                      ))}
                    </List>
                  ) : (
                    <Text type="supporting">
                      {RANK_WAITING_FOR[key] || 'Nothing on this board yet.'}
                    </Text>
                  )}
                </Stack>
              </Card>
            ))}
          </Grid>
        )}
      </Region>

      <Region
        title="Assignments and scores"
        description="Scores are entered by staff against CU's own rubric. Nothing here is graded by the system."
      >
        {!work.length ? (
          <Card padding={5}>
            <EmptyState
              title="No assignments yet"
              description="Set one and every fellow gets a card here to score, handed in or not. /assignment create in Slack, or cufa assignment create."
              headingLevel={3}
              actions={<Button label="New assignment" href="/assignments/new" />}
            />
          </Card>
        ) : (
          <Stack gap={6}>
            {work.map((assignment) => (
              <Assignment
                key={assignment.assignment_id}
                assignment={assignment}
                cohort={cohort_id}
              />
            ))}
          </Stack>
        )}
      </Region>

      {/* Two cards became none. The bars and the medians are two halves of one
          region, so a divider parts them rather than a second border. */}
      <Region
        title="Funnel"
        description={
          total === 1
            ? 'Where the one fellow in this cohort has got to.'
            : total
              ? `Where ${total} fellows have got to.`
              : 'Nobody is in this cohort yet, so there is nowhere to have got to. It fills in from the roster.'
        }
      >
        {!stages.length ? (
          <Waiting>
            {[
              'No stages came back for this cohort.',
              'The five stages are the same for every cohort, so this is the query finding nothing rather than a cohort that has not started.',
            ]}
          </Waiting>
        ) : (
          <Stack gap={5}>
            {stages.map(([stage, label]) => (
              <ProgressBar
                key={stage}
                label={label}
                value={((funnel || {}).counts || {})[stage] || 0}
                max={total || 1}
                hasValueLabel
                formatValueLabel={(value) => `${value} of ${total}`}
                xstyle={styles.meter}
              />
            ))}
          </Stack>
        )}
        {medians.length ? (
          <Stack gap={4}>
            <Divider />
            <MetadataList columns="multi" title="Median days between stages">
              {medians.map(([pair, days]) => {
                const [from, to] = pair.split('->')
                return (
                  <MetadataListItem
                    key={pair}
                    label={`${(stage_labels || {})[from] || from} → ${(stage_labels || {})[to] || to}`}
                  >
                    {days === null || days === undefined ? '—' : days}
                  </MetadataListItem>
                )
              })}
            </MetadataList>
          </Stack>
        ) : null}
      </Region>
    </Stack>
  )
}
