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
import {Text} from '@astryxdesign/core/Text'
import {Token} from '@astryxdesign/core/Token'
import * as stylex from '@stylexjs/stylex'

import {InlineField, Notices, PageHeader, PostForm} from './AppFrame.jsx'
import {CohortFilter} from './CohortFilter.jsx'
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

/** The saturated band a region opens with.
 *
 *  `tone` names one of the theme's four lead variants — the only place this
 *  screen names a colour, and it names it rather than setting it. Everything
 *  written on the band asks for `color="inherit"`: a Text or Heading otherwise
 *  paints itself from its own StyleX class and would keep body ink on a
 *  saturated fill, so inheriting lets the card variant own both halves of the
 *  pair. Until the variant exists the card is an unfilled box and the same
 *  inherited ink stays readable on white, which is the fallback by design. */
function Block({tone, eyebrow, title, description, children}) {
  return (
    <Stack gap={3}>
      <Card variant={`lead-${tone}`} padding={5}>
        <Stack gap={1}>
          {eyebrow ? <Text type="label" color="inherit">{eyebrow}</Text> : null}
          <Heading level={2} color="inherit">{title}</Heading>
          {description ? <Text color="inherit">{description}</Text> : null}
        </Stack>
      </Card>
      {children}
    </Stack>
  )
}

/** One headline number on its own colour, what it counts, and how it was
 *  arrived at. Four of these open the screen. */
function StatBlock({tone, value, label, detail}) {
  return (
    <Card variant={`lead-${tone}`} padding={5}>
      <Stack gap={1}>
        <Text type="display-2" color="inherit" hasTabularNumbers>{value}</Text>
        <Text type="label" color="inherit">{label}</Text>
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
          <Token label="reached out" color="green" />
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
 *  exists on a wide screen. */
function FellowCard({row, cohort}) {
  const flags = row.flags || []
  return (
    <Card padding={5}>
      <Stack gap={4}>
        <Stack direction="horizontal" gap={4} align="center" wrap="wrap">
          <Stack gap={0}>
            <Text type="display-3" hasTabularNumbers>{row.attention_index}</Text>
            <Text type="label">Attention index</Text>
          </Stack>
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
              <Token key={flag} label={flag} color="orange" size="sm" />
            ))}
            {row.open_check_in_requests ? (
              <Token label="check-in request" color="orange" size="sm" />
            ) : null}
          </Stack>
        ) : null}

        <Grid columns={{minWidth: 220, repeat: 'fit'}} gap={4}>
          <Meter
            label="Attendance"
            value={row.attended}
            max={row.sessions_held}
            detail={row.needs_review ? `${row.needs_review} being checked` : undefined}
          />
          <Meter
            label="Exit tickets"
            value={row.forms_submitted}
            max={row.forms_expected}
            detail={
              row.form_completeness === null || row.form_completeness === undefined
                ? undefined
                : `${percent(row.form_completeness)} complete`
            }
          />
          <Stack gap={1}>
            <Text type="label">Slack (7 days)</Text>
            <Text type="display-3" hasTabularNumbers>{row.messages_7d}</Text>
            <Text type="supporting">
              {row.messages} total · mean {row.cohort_mean_messages}
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
        <Stack gap={0}>
          <Text type="display-3" hasTabularNumbers>
            {row.score === null || row.score === undefined
              ? '—'
              : `${row.score}${assignment.max_score ? ` / ${assignment.max_score}` : ''}`}
          </Text>
          <Text type="label">Score</Text>
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
        <Text type="supporting">Nobody on the roster to score yet.</Text>
      )}
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
  const boards = Object.entries(ranks || {})
  const stages = Object.entries(stage_labels || {})
  const sources = Object.entries(received || {})
  const medians = Object.entries((funnel || {}).median_days || {})
  const total = (funnel || {}).fellows || 0
  const queue = openRequests.length + openAlerts.length

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

      <Grid columns={{minWidth: 320, repeat: 'fit'}} gap={3}>
        <StatBlock
          tone="green"
          value={rate(stats.rate)}
          label="Overall attendance"
          detail={
            stats.sessions_held
              ? `${stats.attended} of ${stats.active_fellows * stats.sessions_held}: ${stats.active_fellows} fellows across ${stats.sessions_held} sessions`
              : 'no sessions held yet'
          }
        />
        <StatBlock tone="blue" value={stats.active_fellows} label="Active fellows" />
        <StatBlock tone="purple" value={openRequests.length} label="Open check-in requests" />
        <StatBlock tone="orange" value={openAlerts.length} label="Unrostered Slack accounts" />
      </Grid>

      {/* A heading with nothing under it is a stub, not an empty state: a
          cohort that has never synced anything says so by this card not being
          on the page. */}
      {sources.length ? (
        <Card>
          <MetadataList columns="multi" title={<Text type="label">Last data in</Text>}>
            {sources.map(([source, at]) => (
              <MetadataListItem key={source} label={SOURCE_LABELS[source] || source}>
                {at ? fmtStamp(at) : 'never'}
              </MetadataListItem>
            ))}
          </MetadataList>
        </Card>
      ) : null}

      {queue ? (
        <Block
          tone="orange"
          eyebrow={`QUEUE · ${queue} OPEN`}
          title="Needs a human"
          description="Neither of these closes itself. A check-in request closes when somebody is marked as having reached out; an unrostered account closes in Slack."
        >
          <Stack gap={3}>
            {openRequests.map((row) => (
              <Card key={row.intervention_id} padding={5}>
                <Stack gap={3}>
                  <Stack direction="horizontal" gap={2} wrap="wrap">
                    <Token label="check-in request" color="orange" />
                  </Stack>
                  <Heading level={3}>{row.full_name} asked to be checked in on</Heading>
                  <Text type="supporting">
                    {row.note
                      ? `${fmtDateTime(row.created_at)} — ${row.note}`
                      : fmtDateTime(row.created_at)}
                  </Text>
                  <Stack direction="horizontal" gap={2} wrap="wrap">
                    <Button
                      label="Open their page"
                      size="sm"
                      variant="primary"
                      href={`/dashboard/fellow/${row.fellow_id}`}
                    />
                  </Stack>
                </Stack>
              </Card>
            ))}
            {openAlerts.map((row) => (
              <Card key={row.alert_id} padding={5}>
                <Stack gap={3}>
                  <Stack direction="horizontal" gap={2} wrap="wrap">
                    <Token label="unrostered" color="orange" />
                  </Stack>
                  <Heading level={3}>
                    {`${row.real_name || row.display_name || row.slack_user_id} joined Slack but is not on the roster`}
                  </Heading>
                  <Text type="supporting">
                    {`${row.email ? `${row.email} · ` : ''}In Slack: /link @them <fellow>, or /alerts resolve @them staff`}
                  </Text>
                </Stack>
              </Card>
            ))}
          </Stack>
        </Block>
      ) : null}

      <Block
        tone="blue"
        eyebrow={`${fellows.length} FELLOWS`}
        title="Fellows, most attention-worthy first"
        description="The attention index combines Slack activity against the cohort mean, attendance, and how complete exit tickets are. It is a sorted list for a human, not a grade — the parts are shown so nobody has to trust the number. Asking for help never enters it, and neither do assignment scores."
      >
        {!fellows.length ? (
          <Card padding={5}>
            <EmptyState
              title="Nobody on the roster yet"
              description="Load one with cufa load-roster --csv <path> --cohort <id>."
              headingLevel={3}
            />
          </Card>
        ) : (
          <Stack gap={3}>
            {fellows.map((row) => (
              <FellowCard key={row.fellow_id} row={row} cohort={cohort_id} />
            ))}
          </Stack>
        )}
      </Block>

      <Block
        tone="purple"
        eyebrow="LAST 7 DAYS"
        title="Most active this week"
        description="Fellow-facing channels only, over the last 7 days."
      >
        <Card padding={5}>
          {!active.length ? (
            <Text type="supporting">No Slack messages in the last 7 days.</Text>
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
        </Card>
      </Block>

      <Block
        tone="green"
        eyebrow={`${boards.length} LEADERBOARDS`}
        title="Badges and ranks"
        description="Staff view. Fellows see only their own badges, by DM, and can switch them off. Shoutouts are ranked by giving, not receiving."
      >
        {!boards.length ? (
          <Card padding={5}>
            <Text type="supporting">No data yet.</Text>
          </Card>
        ) : (
          <Grid columns={{minWidth: 280, repeat: 'fit'}} gap={3}>
            {boards.map(([key, rows]) => (
              <Card key={key} padding={5}>
                <Stack gap={3}>
                  <Text type="label">{rankNames[key] || key}</Text>
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
                    <Text type="supporting">No data yet.</Text>
                  )}
                </Stack>
              </Card>
            ))}
          </Grid>
        )}
      </Block>

      <Block
        tone="orange"
        eyebrow={`${work.length} SET`}
        title="Assignments and scores"
        description="Scores are entered by staff against CU's own rubric. Nothing here is graded by the system."
      >
        {!work.length ? (
          <Card padding={5}>
            <EmptyState
              title="No assignments yet"
              description="Create one in Slack with /assignment create, or from the command line with cufa assignment create."
              headingLevel={3}
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
      </Block>

      <Block
        tone="green"
        eyebrow={`${total} FELLOWS`}
        title="Funnel"
        description={`Where ${total} fellows have got to.`}
      >
        <Card padding={5}>
          {!stages.length ? (
            <Text type="supporting">No stages to show yet.</Text>
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
        </Card>
        {medians.length ? (
          <Card padding={5}>
            <MetadataList
              columns="multi"
              title={<Text type="label">Median days between stages</Text>}
            >
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
          </Card>
        ) : null}
      </Block>
    </Stack>
  )
}
