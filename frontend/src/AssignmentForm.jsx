import {Banner} from '@astryxdesign/core/Banner'
import {Button} from '@astryxdesign/core/Button'
import {Card} from '@astryxdesign/core/Card'
import {DateTimeInput} from '@astryxdesign/core/DateTimeInput'
import {Selector} from '@astryxdesign/core/Selector'
import {Stack} from '@astryxdesign/core/Stack'
import {Text} from '@astryxdesign/core/Text'
import {TextArea} from '@astryxdesign/core/TextArea'
import {TextInput} from '@astryxdesign/core/TextInput'
import {useState} from 'react'

import {PageHeader} from './AppFrame.jsx'
import {cohortOptions} from './CohortFilter.jsx'

const STATUS_OPTIONS = [
  {value: 'active', label: 'Active'},
  {value: 'cancelled', label: 'Cancelled'},
]

export function AssignmentForm({
  heading,
  action,
  values = {},
  cohorts = [],
  errors = [],
  assignment_id,
}) {
  const [title, setTitle] = useState(values.title || '')
  const [dueAt, setDueAt] = useState(values.due_at || '')
  const [cohort, setCohort] = useState(values.cohort_id || '')
  const [description, setDescription] = useState(values.description || '')
  const [url, setUrl] = useState(values.url || '')
  const [status, setStatus] = useState(values.status || 'active')
  const [timezone, setTimezone] = useState(() => {
    if (values.timezone) return values.timezone
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone || ''
    } catch {
      return ''
    }
  })

  return (
    <Stack gap={4}>
      <PageHeader title={heading} />

      {errors.length ? (
        <Banner status="error" title="Nothing was saved" defaultIsExpanded>
          <Stack gap={1}>
            {errors.map((m, i) => <Text key={i}>{m}</Text>)}
          </Stack>
        </Banner>
      ) : null}

      <Card padding={5}>
        <Stack as="form" method="post" action={action} gap={4}>
          <TextInput
            label="Title"
            htmlName="title"
            isRequired
            value={title}
            onChange={setTitle}
            placeholder="Community interview notes"
            autoComplete="off"
          />

          <Stack direction="horizontal" gap={4} wrap="wrap" align="start">
            <Stack gap={0}>
              <DateTimeInput
                label="Due at"
                isRequired
                value={dueAt}
                onChange={(v) => setDueAt(v || '')}
                description="Local wall-clock time, in the zone beside it."
              />
              {/* DateTimeInput has no htmlName, so the value is mirrored into a
                  hidden field for the post — the same shape the session form
                  uses, and what datetime.fromisoformat expects. */}
              <input type="hidden" name="due_at" value={dueAt} />
            </Stack>
            <TextInput
              label="Timezone"
              htmlName="timezone"
              isRequired
              value={timezone}
              onChange={setTimezone}
              placeholder="America/Chicago"
              description="IANA name. Defaults to this browser's zone, and is stored beside the local time so a wrong zone stays visible."
            />
          </Stack>

          <Selector
            label="Cohort"
            htmlName="cohort_id"
            value={cohort}
            onChange={setCohort}
            options={cohortOptions(cohorts, {includeAll: false})}
          />

          <TextInput
            label="Link"
            htmlName="url"
            isOptional
            value={url}
            onChange={setUrl}
            placeholder="https://classroom.example.org/interview"
            description="Included in the reminder so nobody has to go looking for it."
          />

          <TextArea
            label="Description"
            htmlName="description"
            isOptional
            value={description}
            onChange={setDescription}
            rows={4}
          />

          {assignment_id ? (
            <Selector
              label="Status"
              htmlName="status"
              value={status}
              onChange={setStatus}
              options={STATUS_OPTIONS}
              description="Cancelled assignments stop appearing in reminders and the digest, and are kept rather than deleted."
            />
          ) : (
            <input type="hidden" name="status" value="active" />
          )}

          <Stack direction="horizontal" gap={2} wrap="wrap">
            <Button label="Save assignment" variant="primary" type="submit" />
            <Button label="Cancel" href="/assignments" />
          </Stack>
        </Stack>
      </Card>
    </Stack>
  )
}
