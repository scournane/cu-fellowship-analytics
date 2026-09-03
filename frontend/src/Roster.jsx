import {Button} from '@astryxdesign/core/Button'
import {Card} from '@astryxdesign/core/Card'
import {EmptyState} from '@astryxdesign/core/EmptyState'
import {Stack} from '@astryxdesign/core/Stack'
import {Table, TableCell, TableHeaderCell, TableRow} from '@astryxdesign/core/Table'
import {Text} from '@astryxdesign/core/Text'
import {TextInput} from '@astryxdesign/core/TextInput'
import {Token} from '@astryxdesign/core/Token'
import {useState} from 'react'

import {Notices, PageHeader, PostForm} from './AppFrame.jsx'
import {CohortFilter} from './CohortFilter.jsx'

function rosterUrl({cohort} = {}) {
  const params = new URLSearchParams()
  if (cohort) params.set('cohort', cohort)
  const query = params.toString()
  return query ? `/roster?${query}` : '/roster'
}

/** One row's zone, saved on its own. Editing the whole roster behind a single
 *  Save would mean one bad zone rejects twenty good ones. */
function TimezoneCell({fellow, cohort, commonZones}) {
  const [value, setValue] = useState(fellow.timezone || '')
  const dirty = value !== (fellow.timezone || '')

  return (
    <PostForm action={`/roster/${fellow.fellow_id}/timezone`}>
      <input type="hidden" name="cohort" value={cohort || ''} />
      <TextInput
        label={`Timezone for ${fellow.full_name}`}
        isLabelHidden
        htmlName="timezone"
        size="sm"
        value={value}
        onChange={setValue}
        // NOT a sample zone. A placeholder that reads as a real value made
        // twenty people with no zone at all look like twenty people in Chicago,
        // which is exactly the misreading this column exists to prevent.
        placeholder="not set"
        list={commonZones.length ? 'common-zones' : undefined}
        spellCheck={false}
      />
      {/* Only when there is something to save. Twenty identical disabled
          buttons is noise that hides the one row being edited. */}
      {dirty ? (
        <Button label="Save" size="sm" type="submit" variant="primary" />
      ) : null}
    </PostForm>
  )
}

export function Roster({
  fellows = [],
  cohorts = [],
  selected_cohort,
  common_zones = [],
  notice,
  error,
}) {
  const missing = fellows.filter((f) => !f.timezone).length

  return (
    <Stack gap={4}>
      <PageHeader title="Roster">
        Who is in the cohort, and what zone each person is in. The zone decides when a
        reminder lands for them; without one, reminders fall back to the session&apos;s zone.
      </PageHeader>

      <Notices notice={notice} error={error} errorTitle="That timezone was not accepted" />

      {/* A datalist, not a Selector: any IANA name is valid, and a picker of
          seven would imply the rest are not allowed. */}
      <datalist id="common-zones">
        {common_zones.map((zone) => <option key={zone} value={zone} />)}
      </datalist>

      <Stack direction="horizontal" gap={3} align="end" wrap="wrap">
        <CohortFilter
          cohorts={cohorts}
          selected={selected_cohort}
          hrefFor={(cohort) => rosterUrl({cohort})}
        />
        {missing ? (
          <Token label={`${missing} without a timezone`} color="orange" size="sm" />
        ) : null}
      </Stack>

      {!fellows.length ? (
        <Card padding={5}>
          <EmptyState
            title="No fellows on the roster"
            description="Load one with cufa load-roster --csv <path> --cohort <id>. A timezone column of IANA names is optional there, and can be filled in here instead."
            headingLevel={2}
          />
        </Card>
      ) : (
        <Stack gap={2}>
          <Table density="compact" dividers="rows">
            <TableRow isHeaderRow>
              <TableHeaderCell>Fellow</TableHeaderCell>
              <TableHeaderCell>Email</TableHeaderCell>
              <TableHeaderCell>Cohort</TableHeaderCell>
              <TableHeaderCell>Status</TableHeaderCell>
              <TableHeaderCell>Timezone</TableHeaderCell>
            </TableRow>
            {fellows.map((fellow) => (
              <TableRow key={fellow.fellow_id}>
                <TableCell>
                  <Stack gap={0.5}>
                    <Text>{fellow.full_name}</Text>
                    <Text type="code">{fellow.fellow_id}</Text>
                  </Stack>
                </TableCell>
                <TableCell><Text type="code">{fellow.primary_email || '—'}</Text></TableCell>
                <TableCell><Text type="supporting">{fellow.cohort_id}</Text></TableCell>
                <TableCell>
                  <Token
                    label={fellow.status || 'active'}
                    color={fellow.status === 'active' ? 'green' : 'default'}
                    size="sm"
                  />
                </TableCell>
                <TableCell>
                  <TimezoneCell
                    fellow={fellow}
                    cohort={selected_cohort}
                    commonZones={common_zones}
                  />
                </TableCell>
              </TableRow>
            ))}
          </Table>
          <Text type="supporting">
            Names, addresses and membership come from the roster CSV, which stays the
            source of truth for who exists. Only the zone is editable here, because it is
            the one field that gets corrected one person at a time.
          </Text>
        </Stack>
      )}
    </Stack>
  )
}
