import {Banner} from '@astryxdesign/core/Banner'
import {Button} from '@astryxdesign/core/Button'
import {Card} from '@astryxdesign/core/Card'
import {Collapsible} from '@astryxdesign/core/Collapsible'
import {Divider} from '@astryxdesign/core/Divider'
import {EmptyState} from '@astryxdesign/core/EmptyState'
import {Heading} from '@astryxdesign/core/Heading'
import {Link} from '@astryxdesign/core/Link'
import {Stack} from '@astryxdesign/core/Stack'
import {Table, TableCell, TableHeaderCell, TableRow} from '@astryxdesign/core/Table'
import {Text} from '@astryxdesign/core/Text'
import {TextArea} from '@astryxdesign/core/TextArea'
import {TextInput} from '@astryxdesign/core/TextInput'
import {Token} from '@astryxdesign/core/Token'
import {useEffect, useRef, useState} from 'react'

import {InlineField, Notices, PageHeader, PostForm} from './AppFrame.jsx'
import {CohortFilter} from './CohortFilter.jsx'
import {QuestionListEditor, toQuestions, toRows} from './QuestionListEditor.jsx'
import {fmtStamp} from './format.js'

const SOURCE_LABEL = {
  seed_file: 'the approved file',
  import_form: 'imported from a form',
  console: 'console',
  cli: 'command line',
}

/** "default v3" / "custom v1" — the same words the Sessions list uses. */
export function scopeLabel(set) {
  if (!set) return 'none'
  return `${set.scope === 'session' ? 'custom' : 'default'} v${set.version}`
}

/** The placeholders filled the way provisioning fills them — including a
 *  session with no week number, where "{lesson}" and the space before it go. */
function fill(text, placeholders) {
  if (!placeholders) return text
  let out = String(text || '')
  out = placeholders.lesson
    ? out.replaceAll('{lesson}', placeholders.lesson)
    : out.replaceAll(' {lesson}', '').replaceAll('{lesson}', '')
  return out.replaceAll('{session_title}', placeholders.session_title || '')
}

function History({history = []}) {
  if (!history.length) return null
  return (
    <Card padding={5}>
      <Collapsible trigger={`Version history (${history.length})`} defaultIsOpen={false}>
        <Stack gap={2}>
          <Text type="supporting">
            Every save is a new version and none is ever edited in place, so what a published
            form asked can always be read back.
          </Text>
          <Table density="compact" dividers="rows">
            <TableRow isHeaderRow>
              <TableHeaderCell>Version</TableHeaderCell>
              <TableHeaderCell>Questions</TableHeaderCell>
              <TableHeaderCell>From</TableHeaderCell>
              <TableHeaderCell>By</TableHeaderCell>
              <TableHeaderCell>Saved</TableHeaderCell>
            </TableRow>
            {history.map((set) => (
              <TableRow key={set.question_set_id}>
                <TableCell>
                  <Stack direction="horizontal" gap={1} align="center">
                    <Text hasTabularNumbers>{`v${set.version}`}</Text>
                    {set.superseded_at ? null : <Token label="current" color="green" size="sm" />}
                  </Stack>
                </TableCell>
                <TableCell><Text hasTabularNumbers>{set.answerable_count}</Text></TableCell>
                <TableCell>
                  <Stack gap={0.5}>
                    <Text type="supporting">{SOURCE_LABEL[set.source] || set.source}</Text>
                    {set.source_ref ? <Text type="code">{set.source_ref}</Text> : null}
                  </Stack>
                </TableCell>
                <TableCell><Text type="supporting">{set.created_by || '—'}</Text></TableCell>
                <TableCell><Text type="supporting">{fmtStamp(set.created_at)}</Text></TableCell>
              </TableRow>
            ))}
          </Table>
        </Stack>
      </Collapsible>
    </Card>
  )
}

/** Where a cohort's default can come from other than typing it in. No card of
 *  its own: it sits inside the Templates card and inside this screen's. */
export function StartFrom({cohortId, current, seedAction, importAction}) {
  return (
    <Stack gap={4}>
      <Text type="supporting">
        Either one saves a new version of this cohort’s default. The versions before it
        stay in the history.
      </Text>

      <Stack gap={2}>
        <Text type="label">The approved exit ticket</Text>
        <Text type="supporting">
          The week-1 exit ticket the programme team approved, kept in
          config/part_a_default_questions.json.
        </Text>
        <PostForm
          action={seedAction}
          confirm={current ? 'Replace the current default with the approved exit ticket? The current version stays in the history.' : undefined}
        >
          <input type="hidden" name="cohort" value={cohortId} />
          <Button
            label={current ? 'Reset to the approved exit ticket' : 'Use the approved exit ticket'}
            variant={current ? undefined : 'primary'}
            type="submit"
          />
        </PostForm>
      </Stack>

      <Divider />

      <Stack gap={2}>
        <Text type="label">Copy the questions from a Google Form</Text>
        <Text type="supporting">
          Paste the form’s edit link — the address in the Forms editor, ending /edit. Only
          the questions are read; branching, grids, dates and file uploads have no
          equivalent here and are listed if they are skipped.
        </Text>
        <PostForm action={importAction}>
          <input type="hidden" name="cohort" value={cohortId} />
          <InlineField
            label="Form edit link or id"
            name="form"
            size="md"
            width={420}
            placeholder="https://docs.google.com/forms/d/…/edit"
          />
          <Button label="Import" type="submit" />
        </PostForm>
      </Stack>
    </Stack>
  )
}

function SessionScope({session, override, baseDefault, provisioned, locked, lockReason, revertAction, defaultsUrl}) {
  return (
    <Card padding={5}>
      <Stack gap={3}>
        <Stack direction="horizontal" gap={2} align="center" wrap="wrap">
          <Heading level={2}>This session asks</Heading>
          <Token
            label={override ? scopeLabel(override) : baseDefault ? scopeLabel(baseDefault) : 'no questions'}
            color={override ? 'purple' : baseDefault ? 'blue' : 'orange'}
            size="sm"
          />
        </Stack>
        <Text>
          {override
            ? `Its own questions, version ${override.version}. Changes to the ${session.cohort_id} default do not reach it.`
            : baseDefault
              ? `The ${session.cohort_id} default, version ${baseDefault.version}.` +
                (locked ? '' : ' Saving below gives this session its own copy; the default stays as it is.')
              : `The ${session.cohort_id} cohort has no default questions yet, so this session has nothing to provision. Set the default, or write this session’s own below.`}
        </Text>
        {provisioned ? (
          <Text type="supporting">
            {`Its Part A form was built from ${scopeLabel(provisioned)}.`}
          </Text>
        ) : null}
        <Stack direction="horizontal" gap={3} align="center" wrap="wrap">
          {override ? (
            <PostForm
              action={revertAction}
              confirm="Drop this session’s own questions and follow the cohort default again? Its versions stay in the history."
            >
              <Button
                label="Revert to the cohort default"
                type="submit"
                isDisabled={locked}
                tooltip={locked ? lockReason || 'Locked' : undefined}
              />
            </PostForm>
          ) : null}
          <Link href={defaultsUrl}>{`Edit the ${session.cohort_id} default instead`}</Link>
        </Stack>
      </Stack>
    </Card>
  )
}

export function QuestionSetEditor({
  scope = 'default',
  cohort_id,
  cohorts = [],
  session,
  current,
  override,
  base_default,
  content,
  base_id,
  history = [],
  usage,
  locked,
  lock_reason,
  provisioned,
  placeholders,
  action,
  revert_action,
  seed_action,
  import_action,
  defaults_url,
  question_types,
  errors = [],
  warnings = [],
  notice,
  error,
}) {
  const isSession = scope === 'session'
  const start = content || {schema_version: 1, title: '', description: '', questions: []}
  const [title, setTitle] = useState(start.title || '')
  const [description, setDescription] = useState(start.description || '')
  const [rows, setRows] = useState(() => toRows(start.questions))
  // A page redrawn after a refusal is holding an edit that was not saved.
  const [dirty, setDirty] = useState(errors.length > 0)
  const submitting = useRef(false)

  // Leaving with unsaved edits asks first. The questions live only in this
  // page until Save posts them, so a stray click on the nav loses all of it.
  useEffect(() => {
    const guard = (e) => {
      if (!dirty || submitting.current) return undefined
      e.preventDefault()
      e.returnValue = ''
      return ''
    }
    window.addEventListener('beforeunload', guard)
    return () => window.removeEventListener('beforeunload', guard)
  }, [dirty])

  const touch = (setter) => (value) => {
    setDirty(true)
    setter(value)
  }

  const payload = JSON.stringify({
    schema_version: start.schema_version || 1,
    title,
    description,
    questions: toQuestions(rows, question_types),
  })

  const back = isSession
    ? {href: `/sessions/${session.session_id}`, label: '← Back to the session'}
    : {href: `/template?cohort=${encodeURIComponent(cohort_id || '')}`, label: '← Templates'}

  if (!isSession && !cohort_id) {
    return (
      <Stack gap={4}>
        <Link href={back.href}>{back.label}</Link>
        <PageHeader title="Default exit ticket questions" />
        <Card padding={5}>
          <EmptyState
            title="No cohorts yet"
            description="Questions belong to a cohort. Load a roster or a schedule first, then come back."
            headingLevel={2}
          />
        </Card>
      </Stack>
    )
  }

  return (
    <Stack gap={4}>
      <Link href={back.href}>{back.label}</Link>

      {isSession ? (
        <PageHeader title={`Part A — exit ticket: ${session.title}`}>
          The questions on this session’s exit ticket. Submitting it inside the session window
          is what records someone as present; the answers are counted and read, never graded.
        </PageHeader>
      ) : (
        <PageHeader title="Default exit ticket questions">
          {`Part A — the exit ticket for every session in ${cohort_id}, unless a session has its own. Fellows answer it at the end of the lesson; submitting inside the session window with a Google-verified address is what records them as present. The answers are counted and read, never graded.`}
        </PageHeader>
      )}

      <Notices notice={notice} error={error} />

      {errors.length ? (
        <Banner status="error" title="Nothing was saved" defaultIsExpanded>
          <Stack gap={1}>
            {errors.map((m, i) => <Text key={i}>{m}</Text>)}
          </Stack>
        </Banner>
      ) : null}
      {warnings.length ? (
        <Banner status="warning" title="Worth a second look" defaultIsExpanded>
          <Stack gap={1}>
            {warnings.map((m, i) => <Text key={i}>{m}</Text>)}
          </Stack>
        </Banner>
      ) : null}

      {locked ? (
        <Banner
          status="info"
          title="These questions are locked"
          description={`${lock_reason || 'The Part A form for this session has been published, so its questions are locked.'} Shown read-only.`}
        />
      ) : null}

      {isSession ? (
        <SessionScope
          session={session}
          override={override}
          baseDefault={base_default}
          provisioned={provisioned}
          locked={locked}
          lockReason={lock_reason}
          revertAction={revert_action}
          defaultsUrl={defaults_url}
        />
      ) : (
        <Stack gap={3}>
          <Stack direction="horizontal" gap={3} align="end" wrap="wrap">
            <CohortFilter
              cohorts={cohorts}
              selected={cohort_id}
              includeAll={false}
              hrefFor={(cohort) => `/template/questions?cohort=${encodeURIComponent(cohort)}`}
            />
            <Token label={current ? scopeLabel(current) : 'no default yet'} color={current ? 'blue' : 'orange'} size="sm" />
          </Stack>
          {usage ? (
            <Text type="supporting">
              {`${usage.following} of ${usage.sessions} session${usage.sessions === 1 ? '' : 's'} in ${cohort_id} follow this default; ${usage.customised} ha${usage.customised === 1 ? 's' : 've'} its own questions. A session whose form is already published keeps the questions it was published with.`}
            </Text>
          ) : null}
          <Card padding={5}>
            <Stack gap={3}>
              <Heading level={2}>Start from an existing set</Heading>
              <StartFrom
                cohortId={cohort_id}
                current={current}
                seedAction={seed_action}
                importAction={import_action}
              />
            </Stack>
          </Card>
        </Stack>
      )}

      <Card padding={5}>
        <Stack
          as="form"
          method="post"
          action={action}
          gap={4}
          onSubmit={() => {
            submitting.current = true
          }}
        >
          <input type="hidden" name="questions_json" value={payload} />
          <input type="hidden" name="base_id" value={base_id || ''} />
          {isSession ? null : <input type="hidden" name="cohort" value={cohort_id} />}

          <Stack gap={1}>
            <Heading level={2}>The form</Heading>
            <Text type="supporting">
              {isSession && placeholders
                ? `{lesson} is filled in as “${placeholders.lesson || '(no week set)'}” and {session_title} as “${placeholders.session_title}” on this session’s form.`
                : 'Write {lesson} for the session’s week number and {session_title} for its title; each session’s form fills them in.'}
            </Text>
          </Stack>

          <TextInput
            label="Form title"
            isRequired
            value={title}
            isReadOnly={locked}
            onChange={touch(setTitle)}
            autoComplete="off"
            description={isSession && placeholders ? `Fellows see: ${fill(title, placeholders)}` : undefined}
          />
          <TextArea
            label="Introduction"
            isOptional
            rows={6}
            value={description}
            isReadOnly={locked}
            onChange={touch(setDescription)}
            description="Shown under the title, above the first question."
          />

          <Divider />

          <Heading level={2}>Questions</Heading>
          <QuestionListEditor
            rows={rows}
            types={question_types}
            isReadOnly={Boolean(locked)}
            onChange={touch(setRows)}
          />

          <Divider />

          <Stack direction="horizontal" gap={2} align="center" wrap="wrap">
            <Button
              label={isSession && !override ? 'Save as this session’s own questions' : 'Save a new version'}
              variant="primary"
              type="submit"
              isDisabled={Boolean(locked)}
              tooltip={locked ? lock_reason || 'Locked' : undefined}
            />
            <Button label="Cancel" href={back.href} />
            {dirty && !locked ? <Text type="supporting">Unsaved changes.</Text> : null}
          </Stack>
        </Stack>
      </Card>

      <History history={history} />
    </Stack>
  )
}
