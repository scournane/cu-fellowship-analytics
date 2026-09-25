import {Banner} from '@astryxdesign/core/Banner'
import {Button} from '@astryxdesign/core/Button'
import {Card} from '@astryxdesign/core/Card'
import {Collapsible} from '@astryxdesign/core/Collapsible'
import {Divider} from '@astryxdesign/core/Divider'
import {EmptyState} from '@astryxdesign/core/EmptyState'
import {Heading} from '@astryxdesign/core/Heading'
import {Link} from '@astryxdesign/core/Link'
import {ProgressBar} from '@astryxdesign/core/ProgressBar'
import {Stack} from '@astryxdesign/core/Stack'
import {Table, TableCell, TableHeaderCell, TableRow} from '@astryxdesign/core/Table'
import {Text} from '@astryxdesign/core/Text'
import {Token} from '@astryxdesign/core/Token'

import {Notices, PageHeader, PostForm} from './AppFrame.jsx'
import {Prose} from './Prose.jsx'
import {fmtStamp} from './format.js'

const KIND_LABEL = {
  teacher_question: "teacher's question",
  muddiest_point: 'muddiest point',
  application: 'application',
}

/** The 1-7 distribution, whole rather than summarised.
 *
 *  Seven numbers fit on a screen, and a distribution answers "is this
 *  bimodal?" — two clusters at 2 and 6 average to something that describes
 *  nobody. The median and IQR sit beside it; there is deliberately no mean.
 */
function Distribution({distribution = [], responses, median, q1, q3, iqr}) {
  const peak = distribution.reduce((max, row) => Math.max(max, row.count), 0) || 1

  if (!responses) {
    return (
      <EmptyState
        title="No confidence answers yet"
        description="They arrive with the first pull after the lesson."
        headingLevel={3}
      />
    )
  }

  return (
    <Stack gap={3}>
      <Stack direction="horizontal" gap={4} wrap="wrap">
        <Text type="supporting">{`${responses} answered`}</Text>
        <Text type="supporting">{`median ${median ?? '—'}`}</Text>
        <Text type="supporting">{`middle half ${q1 ?? '—'}–${q3 ?? '—'} (IQR ${iqr ?? '—'})`}</Text>
      </Stack>
      <Stack gap={1}>
        {distribution.map((row) => (
          <Stack key={row.value} direction="horizontal" gap={2} align="center">
            <Text hasTabularNumbers>{row.value}</Text>
            <ProgressBar
              label={`${row.count} answered ${row.value}`}
              isLabelHidden
              value={row.count}
              max={peak}
              variant={row.value === median ? 'accent' : 'neutral'}
            />
            <Text type="supporting" hasTabularNumbers>{row.count}</Text>
          </Stack>
        ))}
      </Stack>
      <Text type="supporting">1 = “not at all”, 7 = “I could explain it easily”.</Text>
    </Stack>
  )
}

/** "1 = “Not at all”, 5 = “Completely”" for a scale that has end labels. */
function scaleLegend(question) {
  const counts = question.counts || []
  const parts = []
  if (question.low_label && counts.length) parts.push(`${counts[0].value} = “${question.low_label}”`)
  if (question.high_label && counts.length) {
    parts.push(`${counts[counts.length - 1].value} = “${question.high_label}”`)
  }
  return parts.join(', ')
}

/** One exit ticket question: how many answered, then counts or the words.
 *
 *  Counts for a choice or a scale, with every option shown including the ones
 *  nobody picked. For text, the answers themselves with no name beside them:
 *  they are read to plan the next lesson, and a list of who wrote what reads as
 *  a marking sheet. */
function PartAQuestion({question, responses}) {
  const peak = (question.counts || []).reduce((max, row) => Math.max(max, row.count), 0) || 1
  return (
    <Stack gap={2}>
      <Stack gap={0.5}>
        <Text type="label">{question.title}</Text>
        <Text type="supporting">
          {`${question.answered} of ${responses} answered`}
          {question.key ? '' : ' · not on the question map, so shown by its own title'}
        </Text>
      </Stack>
      {question.counted ? (
        <Stack gap={1}>
          {question.counts.map((row) => (
            <Stack key={row.value} direction="horizontal" gap={2} align="center">
              <Text hasTabularNumbers>{row.value}</Text>
              <ProgressBar
                label={`${row.count} answered ${row.value}`}
                isLabelHidden
                value={row.count}
                max={peak}
                variant="neutral"
              />
              <Text type="supporting" hasTabularNumbers>{row.count}</Text>
            </Stack>
          ))}
          {question.low_label || question.high_label ? (
            <Text type="supporting">{scaleLegend(question)}</Text>
          ) : null}
        </Stack>
      ) : question.answers.length ? (
        <Collapsible trigger={`Read the ${question.answers.length} answer${question.answers.length === 1 ? '' : 's'}`} defaultIsOpen={false}>
          <Stack gap={1}>
            {question.answers.map((answer, i) => (
              <Text key={i} type="supporting">{`“${answer}”`}</Text>
            ))}
          </Stack>
        </Collapsible>
      ) : null}
    </Stack>
  )
}

function PartA({partA = {}}) {
  const questions = partA.questions || []
  return (
    <Card padding={5}>
      <Stack gap={4}>
        <Stack gap={1}>
          <Heading level={2}>Part A — exit ticket answers</Heading>
          <Text type="supporting">
            {`${partA.responses || 0} exit ticket${partA.responses === 1 ? '' : 's'} with answers. Counted and read, never graded: submitting is what records someone as present, and what they wrote is for planning the next lesson.`}
          </Text>
          {partA.without_answers ? (
            <Text type="supporting">
              {`${partA.without_answers} more check-in${partA.without_answers === 1 ? '' : 's'} came from a CSV export, which carries the address and the time but not the answers.`}
            </Text>
          ) : null}
        </Stack>
        {!partA.form_id ? (
          <EmptyState
            title="No exit ticket form yet"
            description="Provision Part A from the session page; its answers appear here after a pull."
            headingLevel={3}
          />
        ) : !partA.responses ? (
          <EmptyState
            title="No answers yet"
            description="They arrive with the first pull after the form goes on screen."
            headingLevel={3}
          />
        ) : (
          questions.map((question, i) => (
            <Stack key={question.question_id} gap={4}>
              {i ? <Divider /> : null}
              <PartAQuestion question={question} responses={partA.responses} />
            </Stack>
          ))
        )}
      </Stack>
    </Card>
  )
}

function Themes({themes = [], sessionId}) {
  return (
    <Card padding={5}>
      <Stack gap={3}>
        <Heading level={2}>What was still unclear</Heading>
        {themes.length ? (
          <Stack gap={4}>
            {themes.map((theme) => (
              <Stack key={theme.theme_id} gap={2}>
                <Stack direction="horizontal" gap={2} align="center" wrap="wrap">
                  <Heading level={3}>{theme.label}</Heading>
                  <Token label={`${theme.members.length} answers`} color="default" size="sm" />
                </Stack>
                <Text>{theme.summary}</Text>
                <Collapsible trigger="Read the answers in this theme">
                  <Stack gap={1}>
                    {theme.members.map((member) => (
                      <Text key={member.checkin_b_id} type="supporting">
                        {`“${member.rotating_text}”`}
                      </Text>
                    ))}
                  </Stack>
                </Collapsible>
                <Text type="supporting">
                  {`model ${theme.model} · prompt ${theme.prompt_version} · generated ${fmtStamp(theme.generated_at)}`}
                </Text>
              </Stack>
            ))}
          </Stack>
        ) : (
          <EmptyState
            title="No themes yet"
            description="Themes are clustered from the “what's still unclear” answers on muddiest-point weeks. Without a GEMINI_API_KEY none are generated — the answers below are still complete and readable."
            headingLevel={3}
          />
        )}

        <PostForm action={`/sessions/${sessionId}/themes`}>
          <Button label="Regenerate themes" type="submit" />
        </PostForm>
        <Text type="supporting">
          Regenerating supersedes the previous set rather than overwriting it, so what you
          planned a lesson around last week is still there. The model receives the answers
          as anonymous text — no names, no addresses, no ids — and its only job is grouping
          them by subject. Nothing here judges an individual fellow.
        </Text>
      </Stack>
    </Card>
  )
}

export function Responses({
  session = {},
  distribution = {},
  interpretation,
  responses = [],
  themes = [],
  question_map = [],
  part_a = {},
  survey_rationale,
  notice,
}) {
  const withTakeaway = responses.filter((r) => r.has_takeaway)

  return (
    <Stack gap={4}>
      <Link href={`/sessions/${session.session_id}`}>{'← Back to the session'}</Link>

      <PageHeader title={`Responses — ${session.title || ''}`}>
        {`${part_a.responses || 0} exit ticket(s) · ${responses.length} end-of-session response(s)` +
          (session.week_index ? ` · week ${session.week_index}` : '')}
      </PageHeader>

      <Notices notice={notice} />

      <PartA partA={part_a} />

      <Card padding={5}>
        <Stack gap={3}>
          <Heading level={2}>Part B — confidence</Heading>
          <Distribution {...distribution} />
          <Banner status="info" title="How to read this">
            <Prose text={interpretation} />
          </Banner>
        </Stack>
      </Card>

      <Themes themes={themes} sessionId={session.session_id} />

      <Card padding={5}>
        <Stack gap={3}>
          <Heading level={2}>Takeaways</Heading>
          <Text type="supporting">
            {`${withTakeaway.length} of ${responses.length} responses left something with content in it. That is the whole measure — these are counted, never graded. Rating how well a sentence is written would penalise fellows writing in a second language, or writing differently, for reasons that have nothing to do with whether they were engaged.`}
          </Text>
          {responses.length ? (
            <Table density="compact" dividers="rows">
              <TableRow isHeaderRow>
                <TableHeaderCell>Who</TableHeaderCell>
                <TableHeaderCell>Confidence</TableHeaderCell>
                <TableHeaderCell>Takeaway</TableHeaderCell>
                <TableHeaderCell>This week&apos;s question</TableHeaderCell>
              </TableRow>
              {responses.map((row) => (
                <TableRow key={row.checkin_b_id}>
                  <TableCell>
                    <Stack gap={0.5}>
                      <Text>{row.full_name || row.submitted_email}</Text>
                      {!row.full_name ? (
                        <Token label="not on the roster" color="orange" size="sm" />
                      ) : null}
                      <Text type="supporting">{fmtStamp(row.submitted_at_utc)}</Text>
                    </Stack>
                  </TableCell>
                  <TableCell>
                    {row.confidence_raw === null || row.confidence_raw === undefined ? (
                      <Token label="none / out of range" color="default" size="sm" />
                    ) : (
                      <Text hasTabularNumbers>{row.confidence_raw}</Text>
                    )}
                  </TableCell>
                  <TableCell>
                    <Text>{row.has_takeaway ? row.takeaway_text : '—'}</Text>
                  </TableCell>
                  <TableCell>
                    <Stack gap={0.5}>
                      <Text>{row.rotating_text || '—'}</Text>
                      {row.rotating_kind ? (
                        <Text type="supporting">
                          {KIND_LABEL[row.rotating_kind] || row.rotating_kind}
                        </Text>
                      ) : null}
                    </Stack>
                  </TableCell>
                </TableRow>
              ))}
            </Table>
          ) : (
            <EmptyState
              title="Nothing has arrived yet"
              description="Pull the Part B form from the session screen once the lesson has finished."
              headingLevel={3}
            />
          )}
        </Stack>
      </Card>

      <Card padding={5}>
        <Stack gap={3}>
          <Collapsible trigger="Which question is which (the id map)">
            <Stack gap={2}>
              <Text type="supporting">
                Answers come back keyed by question id, not by title or position. This map is
                read off the form after it is provisioned and is what every answer above was
                resolved through. A form with an incomplete map refuses to ingest rather than
                guessing.
              </Text>
              <Table density="compact" dividers="rows">
                <TableRow isHeaderRow>
                  <TableHeaderCell>#</TableHeaderCell>
                  <TableHeaderCell>Slot</TableHeaderCell>
                  <TableHeaderCell>Question id</TableHeaderCell>
                  <TableHeaderCell>Text shown to fellows</TableHeaderCell>
                </TableRow>
                {question_map.map((row) => (
                  <TableRow key={row.question_id}>
                    <TableCell><Text hasTabularNumbers>{row.item_index}</Text></TableCell>
                    <TableCell><Token label={row.slot} color="default" size="sm" /></TableCell>
                    <TableCell><Text type="code">{row.question_id}</Text></TableCell>
                    <TableCell><Text type="supporting">{row.question_text}</Text></TableCell>
                  </TableRow>
                ))}
              </Table>
              <Text type="supporting">
                The rotating slot&apos;s text is snapshot here at provisioning time, never
                rebuilt later from config — “what was actually asked in week 3” has to be
                answerable from the database alone.
              </Text>
            </Stack>
          </Collapsible>
          <Divider />
          <Collapsible trigger="Thinking of adding a question?">
            <Prose text={survey_rationale} />
          </Collapsible>
        </Stack>
      </Card>
    </Stack>
  )
}
