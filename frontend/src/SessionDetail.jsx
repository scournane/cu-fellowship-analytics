import {Banner} from '@astryxdesign/core/Banner'
import {Button} from '@astryxdesign/core/Button'
import {Card} from '@astryxdesign/core/Card'
import {CheckboxInput} from '@astryxdesign/core/CheckboxInput'
import {Collapsible} from '@astryxdesign/core/Collapsible'
import {Divider} from '@astryxdesign/core/Divider'
import {Heading} from '@astryxdesign/core/Heading'
import {Link} from '@astryxdesign/core/Link'
import {MetadataList, MetadataListItem} from '@astryxdesign/core/MetadataList'
import {Stack} from '@astryxdesign/core/Stack'
import {Table, TableCell, TableHeaderCell, TableRow} from '@astryxdesign/core/Table'
import {Text} from '@astryxdesign/core/Text'
import {Token} from '@astryxdesign/core/Token'
import * as stylex from '@stylexjs/stylex'
import {useCallback, useEffect, useState} from 'react'

import {Notices, PageHeader, PostForm, StatusToken} from './AppFrame.jsx'
import {Prose} from './Prose.jsx'
import {fmtLong, fmtStamp} from './format.js'

const KIND_LABEL = {
  teacher_question: "the teacher's own question",
  muddiest_point: 'the muddiest point',
  application: 'an application prompt',
}

const styles = stylex.create({
  // The QR is a server-generated SVG. It needs a fixed box so it does not grow
  // to the width of the column it sits in.
  qr: {width: 200, height: 200},
  count: {fontSize: '3rem', lineHeight: 1, fontVariantNumeric: 'tabular-nums'},
})

function Qr({markup}) {
  // The markup comes from this app's own qr_svg(), not from user input or Google.
  return <div {...stylex.props(styles.qr)} dangerouslySetInnerHTML={{__html: markup}} />
}

function Responses({sessionId, initialCount, ready}) {
  const [count, setCount] = useState(initialCount ?? 0)
  const [latest, setLatest] = useState('')
  const [autopull, setAutopull] = useState(false)
  const [pullStatus, setPullStatus] = useState('')

  const refresh = useCallback(() => {
    fetch(`/sessions/${sessionId}/responses.json`, {headers: {Accept: 'application/json'}})
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!data || typeof data.responses !== 'number') return
        setCount(data.responses)
        setLatest(
          data.latest_submission_utc
            ? `Most recent submission ${data.latest_submission_utc} (UTC).`
            : '',
        )
      })
      .catch(() => {
        /* a dropped poll is not worth shouting about */
      })
  }, [sessionId])

  // A hidden tab is a tab nobody is reading the count off. Skipping the poll
  // while hidden costs nothing — the visibilitychange handler refreshes the
  // moment the tab comes back, so it is never stale when it is looked at.
  useEffect(() => {
    const tick = () => { if (!document.hidden) refresh() }
    const timer = setInterval(tick, 5000)
    document.addEventListener('visibilitychange', tick)
    return () => {
      clearInterval(timer)
      document.removeEventListener('visibilitychange', tick)
    }
  }, [refresh])

  useEffect(() => {
    if (!autopull) {
      setPullStatus('Automatic pulling is off.')
      return undefined
    }
    setPullStatus('Pulling every 20 seconds…')
    const timer = setInterval(() => {
      // Each pull is a metered Google API call. Do not spend one on a tab
      // sitting behind three others.
      if (document.hidden) return
      fetch(`/sessions/${sessionId}/pull.json`, {method: 'POST'})
        .then((r) => r.json())
        .then((data) => {
          if (data.error) {
            setPullStatus(`Pull failed: ${data.error}`)
            return
          }
          setPullStatus(
            `Last pull: ${data.rows_read} read, ${data.rows_written} new, ${data.rows_skipped} already recorded.`,
          )
          refresh()
        })
        .catch(() => setPullStatus('Pull failed — the server did not answer.'))
    }, 20000)
    return () => clearInterval(timer)
  }, [autopull, sessionId, refresh])

  return (
    <Card padding={5}>
      <Stack gap={3}>
        <Heading level={2}>Responses</Heading>
        <Text as="p" aria-live="polite" xstyle={styles.count}>{count}</Text>
        <Text type="supporting">
          Check-ins recorded for this session, refreshed every five seconds. New submissions
          appear after a pull — the Forms API is polled on demand rather than every few
          seconds, so the count is what has been stored, not what Google has this instant.
        </Text>
        {latest ? <Text type="supporting">{latest}</Text> : null}

        <PostForm action={`/sessions/${sessionId}/pull`}>
          <input type="hidden" name="part" value="a" />
          <Button label="Pull responses" variant="primary" type="submit" isDisabled={!ready} />
        </PostForm>
        {!ready ? (
          <Text type="supporting">Pulling needs a provisioned, verified form.</Text>
        ) : null}

        <CheckboxInput
          label="Keep pulling automatically every 20 seconds while this page is open"
          description="Off by default: each pull is a Google API call."
          value={autopull}
          onChange={setAutopull}
        />
        <Text type="supporting" aria-live="polite">{pullStatus}</Text>
      </Stack>
    </Card>
  )
}

/** Part B: the end-of-session form.
 *
 *  Its own card rather than a second column on Part A's, because the two are
 *  independent observations released at different moments. Showing them as one
 *  thing with two states invites reading a Part B submission as evidence about
 *  Part A, which it is not.
 */
function PartB({
  session,
  templateBlocked,
  ready,
  formUrl,
  qr,
  qrError,
  rotation,
  rotationError,
  questionMap,
  helpRouting,
  helpOption,
  surveyRationale,
}) {
  const sessionId = session.session_id

  return (
    <Card padding={5}>
      <Stack gap={3}>
        <Stack direction="horizontal" gap={3} align="center" wrap="wrap">
          <Heading level={2}>Part B — the end-of-session form</Heading>
          <Token
            label={ready ? 'published and verified' : session.b_form_id ? 'not ready' : 'not provisioned'}
            color={ready ? 'green' : session.b_form_id ? 'orange' : 'default'}
            size="sm"
          />
        </Stack>

        <Text type="supporting">
          Six fields, released at the end of the lesson. Part A proves someone was here;
          this measures what landed. A fellow may answer one and not the other — both are
          real data, and neither is ever used to fill in the other.
        </Text>

        {rotationError ? (
          <Banner
            status="warning"
            title="This session cannot be provisioned yet"
            description={rotationError}
            endContent={
              <Button label="Edit this session" size="sm" href={`/sessions/${sessionId}/edit`} />
            }
          />
        ) : rotation ? (
          <Banner status="info" title={`Week ${rotation.week_index} asks ${KIND_LABEL[rotation.kind] || rotation.kind}`}>
            <Stack gap={1}>
              <Text>{`“${rotation.text}”`}</Text>
              {rotation.wrapped ? (
                <Text type="supporting">
                  {`Past the end of the schedule, so it repeats week ${rotation.schedule_week}.`}
                </Text>
              ) : null}
              <Link href="/rotation">{'See the whole rotation'}</Link>
            </Stack>
          </Banner>
        ) : null}

        {!helpRouting.has_recipient ? (
          <Banner
            status="warning"
            title="The “check in with me” box will not be on this form"
            description={helpRouting.reason_omitted}
          />
        ) : (
          <Text type="supporting">
            {`The last field — “${helpOption}” — goes to ${(helpRouting.recipients || []).map((r) => r.name || r.email).join(', ')} the moment it is submitted. It counts towards nothing.`}
          </Text>
        )}

        {templateBlocked ? (
          <Banner
            status="warning"
            title="Part B provisioning is blocked"
            description="The Part B template does not exist, or its email collection has not been verified. Each part has its own template and its own one-time manual step."
            endContent={<Button label="Go to template setup" size="sm" href="/template" />}
          />
        ) : null}

        {ready && formUrl ? (
          <Stack direction="horizontal" gap={5} wrap="wrap" align="start">
            <Stack gap={2}>
              <Text type="code">{formUrl}</Text>
              <Stack direction="horizontal" gap={2} wrap="wrap">
                <Link href={formUrl} isExternalLink>{'Open the form'}</Link>
                {session.b_edit_url ? (
                  <Link href={session.b_edit_url} isExternalLink>{'Edit it in Google Forms'}</Link>
                ) : null}
                <Link href={`/sessions/${sessionId}/responses`}>{'See the responses'}</Link>
              </Stack>
              <MetadataList columns="single">
                <MetadataListItem label="Form ID">
                  <Text type="code">{session.b_form_id}</Text>
                </MetadataListItem>
                <MetadataListItem label="Publish verified">
                  {fmtStamp(session.b_publish_verified_at)}
                </MetadataListItem>
                <MetadataListItem label="Last polled">
                  {session.b_last_polled_at ? fmtStamp(session.b_last_polled_at) : 'never'}
                </MetadataListItem>
                <MetadataListItem label="Responses">
                  <Text hasTabularNumbers>{session.b_response_count}</Text>
                </MetadataListItem>
              </MetadataList>
            </Stack>
            <Stack gap={2}>
              {qr ? (
                <>
                  <Qr markup={qr} />
                  <Text type="supporting">
                    Point a phone camera at this. The link beside it is the same address.
                  </Text>
                </>
              ) : qrError ? (
                <Banner
                  status="warning"
                  title="No QR code for this link"
                  description={`${qrError} Use the link instead.`}
                />
              ) : null}
            </Stack>
          </Stack>
        ) : (
          <Text>
            {session.b_form_id
              ? `A form was copied (${session.b_form_id}) but its publish state has not been verified, so it may accept nothing while its link still resolves. Provision again — it will resume this form rather than creating a second one.`
              : 'No end-of-session form has been provisioned for this session yet.'}
          </Text>
        )}

        <PostForm action={`/sessions/${sessionId}/provision`}>
          <input type="hidden" name="part" value="b" />
          <Button
            label={ready ? 'Re-check Part B' : 'Provision Part B'}
            variant="primary"
            type="submit"
            isDisabled={Boolean(templateBlocked) || Boolean(rotationError)}
          />
          <Button
            label="Dry run"
            type="submit"
            name="dry_run"
            value="1"
            isDisabled={Boolean(templateBlocked) || Boolean(rotationError)}
          />
        </PostForm>

        <PostForm action={`/sessions/${sessionId}/pull`}>
          <input type="hidden" name="part" value="b" />
          <Button label="Pull Part B responses" type="submit" isDisabled={!ready} />
        </PostForm>

        <Divider />

        {questionMap.length ? (
          <Collapsible trigger={`Question id map (${questionMap.length} fields)`}>
            <Stack gap={2}>
              <Text type="supporting">
                Answers come back keyed by question id, not by title or position, and
                whether a Drive copy keeps those ids is not something anyone has confirmed.
                So they are read off this form after it was built, and every answer is
                resolved through this table. A form whose map is incomplete refuses to
                ingest rather than guessing.
              </Text>
              <Table density="compact" dividers="rows">
                <TableRow isHeaderRow>
                  <TableHeaderCell>#</TableHeaderCell>
                  <TableHeaderCell>Slot</TableHeaderCell>
                  <TableHeaderCell>Question id</TableHeaderCell>
                  <TableHeaderCell>Text shown</TableHeaderCell>
                </TableRow>
                {questionMap.map((row) => (
                  <TableRow key={row.question_id}>
                    <TableCell><Text hasTabularNumbers>{row.item_index}</Text></TableCell>
                    <TableCell><Token label={row.slot} color="default" size="sm" /></TableCell>
                    <TableCell><Text type="code">{row.question_id}</Text></TableCell>
                    <TableCell><Text type="supporting">{row.question_text}</Text></TableCell>
                  </TableRow>
                ))}
              </Table>
            </Stack>
          </Collapsible>
        ) : null}

        <Divider />

        <Collapsible trigger="Thinking of adding a question?">
          <Prose text={surveyRationale} />
        </Collapsible>
      </Stack>
    </Card>
  )
}

export function SessionDetail({
  session = {},
  template,
  template_blocked,
  template_b,
  template_b_blocked,
  ready,
  form_url,
  qr,
  qr_error,
  b_ready,
  b_form_url,
  b_qr,
  b_qr_error,
  b_rotation,
  b_rotation_error,
  b_question_map = [],
  help_routing = {},
  help_option,
  survey_rationale,
  accessibility_reminder,
  provisioning_log = [],
  ingest_warnings = [],
  notice,
  error,
}) {
  const [copied, setCopied] = useState('')

  const copy = () => {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(form_url).then(
        () => setCopied('Link copied to the clipboard.'),
        () => setCopied('Could not copy — select the link and copy it by hand.'),
      )
    } else {
      setCopied('Could not copy — select the link and copy it by hand.')
    }
  }

  return (
    <Stack gap={4}>
      <Link href="/sessions">{"← All sessions"}</Link>

      <PageHeader title={session.title}>
        {`${fmtLong(session.scheduled_at_local)} ${session.timezone || ''} · ${session.duration_minutes} minutes (+${session.grace_minutes} minutes grace either side) · cohort ${session.cohort_id}`}
      </PageHeader>
      <Stack direction="horizontal">
        <Link href={`/sessions/${session.session_id}/edit`}>{"Edit this session"}</Link>
      </Stack>

      <Banner
        status="info"
        title="Say it aloud and put it on screen"
        description={accessibility_reminder}
      />

      <Notices notice={notice} error={error} errorTitle="This did not work — the form is not ready" />

      {ingest_warnings.length ? (
        <Banner status="warning" title="Warnings from the pull" defaultIsExpanded>
          <Stack gap={1}>
            {ingest_warnings.map((w, i) => <Text key={i} type="supporting">{w}</Text>)}
          </Stack>
        </Banner>
      ) : null}

      <Card padding={5}>
        <Stack gap={2}>
          <Heading level={2}>Today&apos;s passphrase</Heading>
          {session.passphrase ? (
            <>
              <Text type="code" size="2xl">{session.passphrase}</Text>
              <Text type="supporting">
                Read this out and display it. It is one signal among several and never proof
                on its own.
              </Text>
            </>
          ) : (
            <>
              <Stack direction="horizontal">
                <Token label="none set" color="default" size="sm" />
              </Stack>
              <Text type="supporting">
                No passphrase is configured for this session. That is legal: check-ins will
                adjudicate as not_set rather than as failures.
              </Text>
              <Link href={`/sessions/${session.session_id}/edit`}>{"Set one"}</Link>
            </>
          )}
        </Stack>
      </Card>

      {template_blocked ? (
        <Banner
          status="warning"
          title="Provisioning is blocked"
          description={
            (template
              ? "The template form's email collection has not been verified as VERIFIED."
              : 'No template form exists yet.') +
            ' Forms copied from an unverified template collect a typed address instead of a Google-confirmed one, so provisioning refuses to run.'
          }
          endContent={<Button label="Go to template setup" size="sm" href="/template" />}
        />
      ) : null}

      <Card padding={5}>
        <Stack gap={3}>
          <Heading level={2}>Part A — the mid-lesson form</Heading>

          {ready && form_url ? (
            <>
              <Stack direction="horizontal" gap={2} align="center" wrap="wrap">
                <Token label="published and verified" color="green" size="sm" />
                <Text type="supporting">
                  Publish state was read back from the API, not assumed.
                </Text>
              </Stack>

              <Stack direction="horizontal" gap={5} wrap="wrap" align="start">
                <Stack gap={2}>
                  <Stack direction="horizontal" gap={2} align="center" wrap="wrap">
                    <Text type="code">{form_url}</Text>
                    <Button label="Copy link" size="sm" onClick={copy} />
                  </Stack>
                  {copied ? <Text type="supporting" aria-live="polite">{copied}</Text> : null}
                  <Stack direction="horizontal" gap={2} wrap="wrap">
                    <Link href={form_url} isExternalLink>{"Open the form"}</Link>
                    {session.edit_url ? (
                      <Link href={session.edit_url} isExternalLink>{"Edit it in Google Forms"}</Link>
                    ) : null}
                  </Stack>
                  <MetadataList columns="single">
                    <MetadataListItem label="Form ID">
                      <Text type="code">{session.form_id}</Text>
                    </MetadataListItem>
                    <MetadataListItem label="Provisioned">{fmtStamp(session.provisioned_at)}</MetadataListItem>
                    <MetadataListItem label="Publish verified">{fmtStamp(session.publish_verified_at)}</MetadataListItem>
                    <MetadataListItem label="Last polled">
                      {session.last_polled_at ? fmtStamp(session.last_polled_at) : 'never'}
                    </MetadataListItem>
                  </MetadataList>
                </Stack>

                <Stack gap={2}>
                  {qr ? (
                    <>
                      <Qr markup={qr} />
                      <Text type="supporting">
                        Point a phone camera at this. The link beside it is the same address
                        if the code will not scan.
                      </Text>
                    </>
                  ) : qr_error ? (
                    <Banner
                      status="warning"
                      title="No QR code for this link"
                      description={`${qr_error} Use the link instead.`}
                    />
                  ) : null}
                </Stack>
              </Stack>
            </>
          ) : (
            <>
              <Stack direction="horizontal">
                <Token label="not ready" color="orange" size="sm" />
              </Stack>
              <Text>
                {session.form_id
                  ? `A form was copied (${session.form_id}) but its publish state has not been verified, so it may accept nothing while its link still resolves. Provision again — it will resume this form rather than creating a second one.`
                  : 'No form has been provisioned for this session yet.'}
              </Text>
              <Text type="supporting">
                No link or QR code is shown until the API confirms the form is published and
                accepting responses.
              </Text>
            </>
          )}

          <PostForm action={`/sessions/${session.session_id}/provision`}>
            <input type="hidden" name="part" value="a" />
            <Button
              label={ready ? 'Re-check provisioning' : 'Provision Part A'}
              variant="primary"
              type="submit"
              isDisabled={Boolean(template_blocked)}
            />
            <Button
              label="Dry run"
              type="submit"
              name="dry_run"
              value="1"
              isDisabled={Boolean(template_blocked)}
            />
          </PostForm>
          <Text type="supporting">
            Provisioning is safe to press twice: an existing form is shown, never duplicated.
          </Text>
        </Stack>
      </Card>

      <PartB
        session={session}
        templateBlocked={template_b_blocked}
        ready={b_ready}
        formUrl={b_form_url}
        qr={b_qr}
        qrError={b_qr_error}
        rotation={b_rotation}
        rotationError={b_rotation_error}
        questionMap={b_question_map}
        helpRouting={help_routing}
        helpOption={help_option}
        surveyRationale={survey_rationale}
      />

      <Card padding={5}>
        <Stack gap={3}>
          <Heading level={2}>Announce</Heading>
          <Text>
            {session.announced_at_utc
              ? `Announced at ${fmtStamp(session.announced_at_utc)}. Latency for every check-in in this session is measured from that moment.`
              : 'Not announced yet. Until it is, latency is measured from the earliest matched submission instead — which means the first person to submit always reads zero.'}
          </Text>
          <PostForm action={`/sessions/${session.session_id}/announce`}>
            <Button
              label={session.announced_at_utc ? 'Announce again (resets T0)' : 'Announce now'}
              variant="primary"
              type="submit"
            />
          </PostForm>
        </Stack>
      </Card>

      <Responses
        sessionId={session.session_id}
        initialCount={session.response_count}
        ready={ready}
      />

      {provisioning_log.length ? (
        <Card padding={5}>
          <Collapsible trigger={`Provisioning attempts (${provisioning_log.length} most recent)`}>
            <Table density="compact" dividers="rows">
              <TableRow isHeaderRow>
                <TableHeaderCell>When</TableHeaderCell>
                <TableHeaderCell>Action</TableHeaderCell>
                <TableHeaderCell>Outcome</TableHeaderCell>
                <TableHeaderCell>Error</TableHeaderCell>
              </TableRow>
              {provisioning_log.map((entry, i) => (
                <TableRow key={i}>
                  <TableCell><Text type="supporting">{fmtStamp(entry.at)}</Text></TableCell>
                  <TableCell><Text type="code">{entry.action}</Text></TableCell>
                  <TableCell><StatusToken value={entry.outcome} /></TableCell>
                  <TableCell><Text type="supporting">{entry.error || ''}</Text></TableCell>
                </TableRow>
              ))}
            </Table>
          </Collapsible>
        </Card>
      ) : null}
    </Stack>
  )
}
