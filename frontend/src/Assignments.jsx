import {Button} from '@astryxdesign/core/Button'
import {Card} from '@astryxdesign/core/Card'
import {EmptyState} from '@astryxdesign/core/EmptyState'
import {Link} from '@astryxdesign/core/Link'
import {Stack} from '@astryxdesign/core/Stack'
import {Table, TableCell, TableHeaderCell, TableRow} from '@astryxdesign/core/Table'
import {Text} from '@astryxdesign/core/Text'
import {Token} from '@astryxdesign/core/Token'

import {Notices, PageHeader} from './AppFrame.jsx'
import {CohortFilter} from './CohortFilter.jsx'
import {fmtDateTime} from './format.js'

function assignmentsUrl({cohort} = {}) {
  const params = new URLSearchParams()
  if (cohort) params.set('cohort', cohort)
  const query = params.toString()
  return query ? `/assignments?${query}` : '/assignments'
}

export function Assignments({
  assignments = [],
  cohorts = [],
  selected_cohort,
  notice,
}) {
  return (
    <Stack gap={4}>
      <PageHeader title="Assignments">
        What the Slack reminders and the weekly digest count down to. Due times are the
        local wall-clock value that was typed, with the zone it was typed in.
      </PageHeader>
      <Notices notice={notice} />

      <Stack direction="horizontal" gap={3} align="end" wrap="wrap">
        <Button label="New assignment" variant="primary" href="/assignments/new" />
        <CohortFilter
          cohorts={cohorts}
          selected={selected_cohort}
          hrefFor={(cohort) => assignmentsUrl({cohort})}
        />
      </Stack>

      {!assignments.length ? (
        <Card padding={5}>
          <EmptyState
            title="No assignments yet"
            description="Create one here, or from the command line with cufa assignment create."
            headingLevel={2}
          />
        </Card>
      ) : (
        <Table density="compact" dividers="rows" hasHover>
          <TableRow isHeaderRow>
            <TableHeaderCell>Assignment</TableHeaderCell>
            <TableHeaderCell>Due</TableHeaderCell>
            <TableHeaderCell>Cohort</TableHeaderCell>
            <TableHeaderCell>Link</TableHeaderCell>
            <TableHeaderCell>Status</TableHeaderCell>
          </TableRow>
          {assignments.map((row) => (
            <TableRow key={row.assignment_id}>
              <TableCell>
                <Stack gap={0.5}>
                  <Link href={`/assignments/${row.assignment_id}/edit`}>{row.title}</Link>
                  {row.description ? (
                    <Text type="supporting" maxLines={2}>{row.description}</Text>
                  ) : null}
                </Stack>
              </TableCell>
              <TableCell>
                <Stack gap={0.5}>
                  <Text>{fmtDateTime(row.due_at_local)}</Text>
                  <Text type="supporting">{row.timezone}</Text>
                </Stack>
              </TableCell>
              <TableCell><Text type="supporting">{row.cohort_id}</Text></TableCell>
              <TableCell>
                {row.url ? (
                  <Link href={row.url} isExternalLink>{'Open'}</Link>
                ) : <Text type="supporting">—</Text>}
              </TableCell>
              <TableCell>
                <Token
                  label={row.status}
                  color={row.status === 'active' ? 'green' : 'default'}
                  size="sm"
                />
              </TableCell>
            </TableRow>
          ))}
        </Table>
      )}
    </Stack>
  )
}
