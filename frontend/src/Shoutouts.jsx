import {Button} from '@astryxdesign/core/Button'
import {Card} from '@astryxdesign/core/Card'
import {EmptyState} from '@astryxdesign/core/EmptyState'
import {Stack} from '@astryxdesign/core/Stack'
import {Table, TableCell, TableHeaderCell, TableRow} from '@astryxdesign/core/Table'
import {Text} from '@astryxdesign/core/Text'
import {Token} from '@astryxdesign/core/Token'

import {Notices, PageHeader, PostForm} from './AppFrame.jsx'
import {CohortFilter, shoutoutsUrl} from './CohortFilter.jsx'
import {fmtStamp} from './format.js'

/** One button per candidate, each posting that fellow id.
 *
 *  A button rather than a dropdown-and-submit: the whole cost of resolving one
 *  of these should be a single click, or the queue does not get worked. */
function LinkButtons({shoutoutId, candidates, cohort}) {
  if (!candidates.length) {
    return (
      <Text type="supporting">
        No roster entry looks like this. That is normal — guest speakers, teachers and
        people outside the cohort get thanked too.
      </Text>
    )
  }
  return (
    <PostForm action={`/shoutouts/${shoutoutId}/link`} direction="vertical" gap={1}>
      <input type="hidden" name="cohort" value={cohort || ''} />
      {candidates.map((candidate) => (
        <Button
          key={candidate.fellow_id}
          label={`${candidate.full_name}`}
          size="sm"
          type="submit"
          name="fellow_id"
          value={candidate.fellow_id}
        />
      ))}
    </PostForm>
  )
}

export function Shoutouts({
  rows = [],
  candidates = {},
  cohorts = [],
  selected_cohort,
  notice,
}) {
  return (
    <Stack gap={4}>
      <PageHeader title="Shoutout review">
        Names a fellow typed when asked who helped them, that did not resolve to exactly
        one person on the roster.
      </PageHeader>

      <Notices notice={notice} />

      <Stack direction="horizontal" gap={3} align="end" wrap="wrap">
        <CohortFilter
          cohorts={cohorts}
          selected={selected_cohort}
          hrefFor={(cohort) => shoutoutsUrl({cohort})}
        />
      </Stack>

      {!rows.length ? (
        <Card padding={5}>
          <EmptyState
            title="Nothing waiting"
            description="Every name typed so far either matched exactly one fellow, or has already been linked by a person."
            headingLevel={2}
          />
        </Card>
      ) : (
        <Stack gap={2}>
          <Table density="compact" dividers="rows">
            <TableRow isHeaderRow>
              <TableHeaderCell>As typed</TableHeaderCell>
              <TableHeaderCell>Session</TableHeaderCell>
              <TableHeaderCell>When</TableHeaderCell>
              <TableHeaderCell>Link to</TableHeaderCell>
            </TableRow>
            {rows.map((row) => (
              <TableRow key={row.shoutout_id}>
                <TableCell>
                  <Stack gap={0.5}>
                    <Text type="code">{row.raw_text}</Text>
                    <Token label="unresolved" color="orange" size="sm" />
                  </Stack>
                </TableCell>
                <TableCell>
                  <Text type="supporting">{row.session_title || '—'}</Text>
                </TableCell>
                <TableCell>
                  <Text type="supporting">{fmtStamp(row.submitted_at_utc)}</Text>
                </TableCell>
                <TableCell>
                  <LinkButtons
                    shoutoutId={row.shoutout_id}
                    candidates={candidates[String(row.shoutout_id)] || []}
                    cohort={selected_cohort}
                  />
                </TableCell>
              </TableRow>
            ))}
          </Table>
          <Text type="supporting">
            Two things land here, and only one of them is a problem. A name matching two
            fellows is never linked automatically — attributing someone&apos;s praise to the
            wrong person is worse than leaving it unattached, because a wrong link is
            invisible and nobody ever finds out. A name matching nobody is not an error at
            all; leave it.
          </Text>
          <Text type="supporting">
            Linking records your address against the decision. There is deliberately no
            leaderboard, ranking or points table anywhere in this system — shoutouts are
            collected and resolved, and that is all.
          </Text>
        </Stack>
      )}
    </Stack>
  )
}
