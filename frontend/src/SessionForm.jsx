import {Banner} from '@astryxdesign/core/Banner'
import {Button} from '@astryxdesign/core/Button'
import {Card} from '@astryxdesign/core/Card'
import {CheckboxInput} from '@astryxdesign/core/CheckboxInput'
import {DateTimeInput} from '@astryxdesign/core/DateTimeInput'
import {Divider} from '@astryxdesign/core/Divider'
import {Heading} from '@astryxdesign/core/Heading'
import {Link} from '@astryxdesign/core/Link'
import {NumberInput} from '@astryxdesign/core/NumberInput'
import {Selector} from '@astryxdesign/core/Selector'
import {Stack} from '@astryxdesign/core/Stack'
import {Text} from '@astryxdesign/core/Text'
import {TextInput} from '@astryxdesign/core/TextInput'
import {Token} from '@astryxdesign/core/Token'
import {useState} from 'react'

import {PageHeader} from './AppFrame.jsx'
import {cohortOptions} from './CohortFilter.jsx'

const KIND_LABEL = {
  teacher_question: "your own question",
  muddiest_point: 'the muddiest point',
  application: 'an application prompt',
}

function toNumber(value, fallback) {
  const n = Number(value)
  return Number.isFinite(n) ? n : fallback
}

export function SessionForm({
  heading,
  action,
  values = {},
  cohorts = [],
  guidance,
  errors = [],
  reuse_warnings = [],
  session_id,
  rotation = {},
}) {
  const [title, setTitle] = useState(values.title || '')
  const [scheduledAt, setScheduledAt] = useState(values.scheduled_at || '')
  const [duration, setDuration] = useState(toNumber(values.duration_minutes, 60))
  const [grace, setGrace] = useState(toNumber(values.grace_minutes, 15))
  const [passphrase, setPassphrase] = useState(values.passphrase || '')
  const [cohort, setCohort] = useState(values.cohort_id || '')
  const [week, setWeek] = useState(values.week_index || '')
  const [teacherQuestion, setTeacherQuestion] = useState(values.teacher_question || '')

  // The rotation hint is computed server-side for the week that was SAVED. When
  // the number in the box changes the hint goes stale, so the teacher-question
  // field stays visible from that point on rather than disappearing mid-edit —
  // hiding a field someone is typing in is worse than showing one they do not
  // need.
  const weekChanged = String(week) !== String(values.week_index || '')
  const needsTeacherQuestion = rotation.needs_teacher_question && !teacherQuestion.trim()
  const showTeacherQuestion =
    weekChanged || rotation.kind === 'teacher_question' || Boolean(teacherQuestion)
  const [timezone, setTimezone] = useState(() => {
    if (values.timezone) return values.timezone
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone || ''
    } catch {
      return ''
    }
  })
  const [suggestStatus, setSuggestStatus] = useState('')

  const suggest = () => {
    setSuggestStatus('Choosing…')
    fetch('/api/passphrase/suggest', {headers: {Accept: 'application/json'}})
      .then((r) => r.json())
      .then((data) => {
        setPassphrase(data.passphrase)
        setSuggestStatus(
          `Suggested “${data.passphrase}”. Check it is not in this week's slides.`,
        )
      })
      .catch(() => setSuggestStatus('Could not reach the server — type a word instead.'))
  }

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

      {reuse_warnings.length ? (
        <Banner
          status="warning"
          title="This passphrase has been used before"
          description="Nothing was saved. Change the passphrase, or tick the box at the bottom of the form to save it anyway."
          defaultIsExpanded
        >
          <Stack gap={1}>
            {reuse_warnings.map((m, i) => <Text key={i}>{m}</Text>)}
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
            placeholder="Week 3 — Deliberation"
            autoComplete="off"
          />

          <Stack direction="horizontal" gap={4} wrap="wrap" align="start">
            <Stack gap={0}>
              <DateTimeInput
                label="Scheduled at"
                isRequired
                value={scheduledAt}
                onChange={(v) => setScheduledAt(v || '')}
                description="Local wall-clock time, in the zone beside it."
              />
              {/* DateTimeInput has no htmlName, so the value is mirrored into a
                  hidden field for the form post. The server parses it with
                  datetime.fromisoformat, which takes exactly this shape. */}
              <input type="hidden" name="scheduled_at" value={scheduledAt} />
            </Stack>
            <TextInput
              label="Timezone"
              htmlName="timezone"
              isRequired
              value={timezone}
              onChange={setTimezone}
              placeholder="America/New_York"
              description="Defaults to this browser's zone. Stored alongside the local time so a wrong zone stays visible instead of silently shifting every check-in."
            />
          </Stack>

          <Stack direction="horizontal" gap={4} wrap="wrap" align="start">
            <NumberInput
              label="Duration in minutes"
              htmlName="duration_minutes"
              isRequired
              min={1}
              step={1}
              value={duration}
              onChange={setDuration}
            />
            <NumberInput
              label="Grace in minutes"
              htmlName="grace_minutes"
              min={0}
              step={1}
              value={grace}
              onChange={setGrace}
              description="Widens the matching window on both sides. Default 15."
            />
          </Stack>

          <Selector
            label="Cohort"
            htmlName="cohort_id"
            value={cohort}
            onChange={setCohort}
            options={cohortOptions(cohorts, {includeAll: false})}
          />

          <Stack gap={2}>
            <TextInput
              label="Passphrase"
              htmlName="passphrase"
              isOptional
              value={passphrase}
              onChange={setPassphrase}
              autoComplete="off"
              spellCheck={false}
              description={guidance}
            />
            <Stack direction="horizontal" gap={2} align="center" wrap="wrap">
              <Button label="Suggest a passphrase" size="sm" onClick={suggest} />
              <Text type="supporting" aria-live="polite">{suggestStatus}</Text>
            </Stack>
          </Stack>

          <Divider />

          <Stack gap={3}>
            <Heading level={2}>End-of-session form (Part B)</Heading>
            <Text type="supporting">
              One question on the end-of-session form changes each week. The week decides
              which — and it is typed here rather than worked out from the date, because
              sessions get rescheduled, skipped and doubled up, and a date-derived week
              would slide the whole rotation without saying so.
            </Text>

            <Stack direction="horizontal" gap={4} wrap="wrap" align="start">
              <TextInput
                label="Week of the fellowship"
                htmlName="week_index"
                isOptional
                value={String(week)}
                onChange={setWeek}
                inputMode="numeric"
                autoComplete="off"
                placeholder="3"
                description="Leave blank if you are not running Part B for this session."
              />
              {rotation.kind && !weekChanged ? (
                <Stack gap={1}>
                  <Text type="supporting">This week asks:</Text>
                  <Token
                    label={KIND_LABEL[rotation.kind] || rotation.kind}
                    color={rotation.kind === 'teacher_question' ? 'purple' : 'blue'}
                    size="sm"
                  />
                  {rotation.wrapped ? (
                    <Text type="supporting">
                      {`Past the end of the schedule, so it repeats week ${rotation.schedule_week}.`}
                    </Text>
                  ) : null}
                  <Link href="/rotation">{'See the whole rotation'}</Link>
                </Stack>
              ) : null}
            </Stack>

            {rotation.error ? (
              <Banner status="warning" title="The rotation could not be read" description={rotation.error} />
            ) : null}

            {needsTeacherQuestion ? (
              <Banner
                status="warning"
                title="This week needs your own question, and provisioning will refuse without it"
                description="No generic question is substituted. Your question is the only item on the form that depends on content someone had to be present to know — a stand-in would collect data that looks the same and means nothing."
              />
            ) : null}

            {showTeacherQuestion ? (
              <TextInput
                label="Your question for this week"
                htmlName="teacher_question"
                isOptional
                value={teacherQuestion}
                onChange={setTeacherQuestion}
                autoComplete="off"
                placeholder="What surprised you about the budget we looked at?"
                description="Asked on teacher-question weeks. Saved even on weeks that do not need it, so a question typed once is not lost when the schedule changes."
              />
            ) : null}
          </Stack>

          {reuse_warnings.length ? (
            <CheckboxInput
              htmlName="confirm_reuse"
              label="I understand this passphrase was already used in this cohort, and everyone who attended that session knows it. Save it anyway."
            />
          ) : null}

          <Stack direction="horizontal" gap={2} wrap="wrap">
            <Button label="Save session" variant="primary" type="submit" />
            <Button
              label="Cancel"
              href={session_id ? `/sessions/${session_id}` : '/sessions'}
            />
          </Stack>
        </Stack>
      </Card>
    </Stack>
  )
}
