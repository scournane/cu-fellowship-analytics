import {Button} from '@astryxdesign/core/Button'
import {Card} from '@astryxdesign/core/Card'
import {EmptyState} from '@astryxdesign/core/EmptyState'
import {Heading} from '@astryxdesign/core/Heading'
import {Link} from '@astryxdesign/core/Link'
import {Stack} from '@astryxdesign/core/Stack'
import {Table, TableCell, TableHeaderCell, TableRow} from '@astryxdesign/core/Table'
import {Text} from '@astryxdesign/core/Text'
import {Token} from '@astryxdesign/core/Token'
import {useState} from 'react'

import {Notices, PageHeader, PostForm} from './AppFrame.jsx'
import {CohortFilter, sessionsUrl} from './CohortFilter.jsx'
import {fmtDateTime} from './format.js'

function FormState({row}) {
  if (row.publish_verified_at) return <Token label="ready" color="green" size="sm" />
  if (row.form_id) return <Token label="not verified" color="orange" size="sm" />
  return <Token label="none" color="default" size="sm" />
}

/** A term's schedule, from the spreadsheet it was typed into.
 *
 *  Twelve sessions is twelve trips through the New session form, which nobody
 *  does twice. The template download is the part that makes this usable without
 *  documentation: a roster is exported from somewhere and arrives with the right
 *  columns already, but a schedule is typed by hand, so without a starting file
 *  the first attempt is a guess at what the columns are called.
 */
function LoadSessions() {
  const [name, setName] = useState('')

  return (
    <Card padding={5}>
      <Stack gap={3}>
        <Stack gap={1}>
          <Heading level={2}>Load a schedule</Heading>
          <Text type="supporting">
            One row per session. A session already on the list — same cohort, title and
            time — is left alone, so re-loading a corrected file adds what is new rather
            than duplicating what is there.
          </Text>
        </Stack>

        <PostForm action="/sessions/upload" encType="multipart/form-data" direction="vertical" gap={3}>
          <Stack direction="horizontal" gap={3} align="end" wrap="wrap">
            <Stack gap={1}>
              <Text type="label">Spreadsheet</Text>
              <input
                type="file"
                name="file"
                accept=".csv,text/csv"
                required
                onChange={(e) => setName(e.target.files?.[0]?.name || '')}
              />
            </Stack>
            <Button label="Load" type="submit" variant="primary" />
            <Link href="/sessions/template.csv">Download a template</Link>
          </Stack>
          {name ? <Text type="supporting">Ready to load {name}.</Text> : null}
        </PostForm>
      </Stack>
    </Card>
  )
}

export function Sessions({sessions = [], cohorts = [], selected_cohort, notice, error}) {
  return (
    <Stack gap={4}>
      <PageHeader title="Sessions" />
      <Notices notice={notice} error={error} errorTitle="That file was not loaded" />

      <Stack direction="horizontal" gap={3} align="end" wrap="wrap">
        <Button label="New session" variant="primary" href="/sessions/new" />
        <CohortFilter
          cohorts={cohorts}
          selected={selected_cohort}
          hrefFor={(cohort) => sessionsUrl({cohort})}
        />
      </Stack>

      <LoadSessions />

      {!sessions.length ? (
        <Card padding={5}>
          <EmptyState
            title="No sessions yet"
            description="Use New session for one, or load a whole term from a spreadsheet above — there is a template to start from."
            headingLevel={2}
          />
        </Card>
      ) : (
        <Stack gap={2}>
          <Table density="compact" dividers="rows" hasHover>
            <TableRow isHeaderRow>
              <TableHeaderCell>Session</TableHeaderCell>
              <TableHeaderCell>Local time</TableHeaderCell>
              <TableHeaderCell>Cohort</TableHeaderCell>
              <TableHeaderCell>Length</TableHeaderCell>
              <TableHeaderCell>Passphrase</TableHeaderCell>
              <TableHeaderCell>Form</TableHeaderCell>
              <TableHeaderCell>Check-ins</TableHeaderCell>
            </TableRow>
            {sessions.map((row) => (
              <TableRow key={row.session_id}>
                <TableCell>
                  <Link href={`/sessions/${row.session_id}`}>{row.title}</Link>
                </TableCell>
                <TableCell>
                  <Stack gap={0.5}>
                    <Text>{fmtDateTime(row.scheduled_at_local)}</Text>
                    <Text type="supporting">{row.timezone}</Text>
                    {row.announced_at_utc ? <Token label="announced" color="green" size="sm" /> : null}
                  </Stack>
                </TableCell>
                <TableCell><Text type="supporting">{row.cohort_id}</Text></TableCell>
                <TableCell>
                  <Stack gap={0.5}>
                    <Text hasTabularNumbers>{row.duration_minutes}m</Text>
                    <Text type="supporting">+{row.grace_minutes}m grace</Text>
                  </Stack>
                </TableCell>
                <TableCell>
                  {row.passphrase
                    ? <Text type="code">{row.passphrase}</Text>
                    : <Token label="none set" color="default" size="sm" />}
                </TableCell>
                <TableCell><FormState row={row} /></TableCell>
                <TableCell><Text hasTabularNumbers>{row.response_count}</Text></TableCell>
              </TableRow>
            ))}
          </Table>
          <Text type="supporting">
            Times are shown as the local wall-clock value that was typed, with the IANA zone
            it was typed in. The UTC instant is derived from both.
          </Text>
        </Stack>
      )}
    </Stack>
  )
}
