import {Banner} from '@astryxdesign/core/Banner'
import {Card} from '@astryxdesign/core/Card'
import {Collapsible} from '@astryxdesign/core/Collapsible'
import {Heading} from '@astryxdesign/core/Heading'
import {Link} from '@astryxdesign/core/Link'
import {MetadataList, MetadataListItem} from '@astryxdesign/core/MetadataList'
import {Stack} from '@astryxdesign/core/Stack'
import {Table, TableCell, TableHeaderCell, TableRow} from '@astryxdesign/core/Table'
import {Text} from '@astryxdesign/core/Text'
import {Token} from '@astryxdesign/core/Token'

import {Notices, PageHeader} from './AppFrame.jsx'
import {CohortFilter, rotationUrl} from './CohortFilter.jsx'
import {Prose} from './Prose.jsx'

const KIND_LABEL = {
  teacher_question: "Teacher's own question",
  muddiest_point: 'Muddiest point',
  application: 'Application',
}

const KIND_COLOR = {
  teacher_question: 'purple',
  muddiest_point: 'blue',
  application: 'green',
}

export function Rotation({
  schedule = {},
  preview = [],
  cohorts = [],
  selected_cohort,
  survey_rationale,
  error,
}) {
  // Only weeks that have a session are chased. A future week with no session
  // yet still shows "not set" in the table, but warning about it would be
  // alarming about nothing — there is no session to put a question on.
  const missing = preview.filter((row) => row.needs_teacher_question && row.session_id)

  return (
    <Stack gap={4}>
      <PageHeader title="Rotation">
        One question on the end-of-session form changes each week. Everything else stays
        the same, so nobody ever faces more than four questions at once.
      </PageHeader>

      <Notices error={error} errorTitle="The rotation schedule could not be read" />

      {missing.length ? (
        <Banner
          status="warning"
          title={`${missing.length} week(s) need a question written`}
          description="Provisioning refuses a teacher-question week with no question set — it will not substitute a generic one, because the teacher's question is the only item on the form that depends on content someone had to be present to know. Set it on the session before that week arrives."
          defaultIsExpanded
        >
          <Stack gap={1}>
            {missing.map((row) => (
              <Text key={row.week_index} type="supporting">
                {`Week ${row.week_index} — ${row.session_title} · `}
                <Link href={`/sessions/${row.session_id}/edit`}>{'set it now'}</Link>
              </Text>
            ))}
          </Stack>
        </Banner>
      ) : null}

      <Stack direction="horizontal" gap={3} align="end" wrap="wrap">
        <CohortFilter
          cohorts={cohorts}
          selected={selected_cohort}
          hrefFor={(cohort) => rotationUrl({cohort})}
        />
      </Stack>

      <Card padding={5}>
        <Stack gap={3}>
          <Heading level={2}>The schedule</Heading>
          {/* Single column: a two-column list puts a label at the end of one row
              and its value at the start of the next, which reads as the wrong
              pairing at a glance. */}
          <MetadataList columns="single">
            <MetadataListItem label="Owner">{schedule.owner || '—'}</MetadataListItem>
            <MetadataListItem label="Status">{schedule.status || '—'}</MetadataListItem>
            <MetadataListItem label="Version">{schedule.version || '—'}</MetadataListItem>
            <MetadataListItem label="Weeks covered">
              {schedule.weeks ? `${schedule.weeks}, then ${schedule.wrap ? 'wraps' : 'stops'}` : '—'}
            </MetadataListItem>
          </MetadataList>
          <Text type="supporting">
            {`This file is owned by the Director of Programs, not by this code. It lives at ${schedule.source || 'config/rotation.json'} and is edited there. A malformed schedule — a gap or two kinds claiming one week — is rejected when it loads, not halfway through a batch of forms.`}
          </Text>
        </Stack>
      </Card>

      <Card padding={5}>
        <Stack gap={3}>
          <Heading level={2}>What each week asks</Heading>
          <Table density="compact" dividers="rows">
            <TableRow isHeaderRow>
              <TableHeaderCell>Week</TableHeaderCell>
              <TableHeaderCell>Kind</TableHeaderCell>
              <TableHeaderCell>Question</TableHeaderCell>
              <TableHeaderCell>Session</TableHeaderCell>
            </TableRow>
            {preview.map((row) => (
              <TableRow key={row.week_index}>
                <TableCell>
                  <Stack gap={0.5}>
                    <Text hasTabularNumbers>{row.week_index}</Text>
                    {row.wrapped ? (
                      <Token label={`repeats wk ${row.schedule_week}`} color="default" size="sm" />
                    ) : null}
                  </Stack>
                </TableCell>
                <TableCell>
                  {row.kind ? (
                    <Token
                      label={KIND_LABEL[row.kind] || row.kind}
                      color={KIND_COLOR[row.kind] || 'default'}
                      size="sm"
                    />
                  ) : (
                    <Text type="supporting">{row.error}</Text>
                  )}
                </TableCell>
                <TableCell>
                  {row.needs_teacher_question ? (
                    <Token label="not set — provisioning will refuse" color="orange" size="sm" />
                  ) : (
                    <Text>{row.text || '—'}</Text>
                  )}
                </TableCell>
                <TableCell>
                  {row.session_id ? (
                    <Link href={`/sessions/${row.session_id}`}>{row.session_title}</Link>
                  ) : (
                    <Text type="supporting">no session yet</Text>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </Table>
          <Text type="supporting">
            The teacher&apos;s question comes up most often because it is the only genuinely
            unfakeable one — it depends on content only someone present would know. The week
            comes from the number typed on the session, never from the calendar: sessions get
            rescheduled and skipped, and a date-derived week would slide the whole rotation
            without saying so.
          </Text>
        </Stack>
      </Card>

      <Card padding={5}>
        <Collapsible trigger="Why the form is six fields and not seven">
          <Prose text={survey_rationale} />
        </Collapsible>
      </Card>
    </Stack>
  )
}
