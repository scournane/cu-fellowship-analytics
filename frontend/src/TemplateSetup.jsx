import {Banner} from '@astryxdesign/core/Banner'
import {Button} from '@astryxdesign/core/Button'
import {Card} from '@astryxdesign/core/Card'
import {CodeBlock} from '@astryxdesign/core/CodeBlock'
import {Collapsible} from '@astryxdesign/core/Collapsible'
import {Heading} from '@astryxdesign/core/Heading'
import {Link} from '@astryxdesign/core/Link'
import {MetadataList, MetadataListItem} from '@astryxdesign/core/MetadataList'
import {Stack} from '@astryxdesign/core/Stack'
import {Text} from '@astryxdesign/core/Text'
import {Token} from '@astryxdesign/core/Token'

import {Notices, PageHeader, PostForm} from './AppFrame.jsx'
import {Prose} from './Prose.jsx'
import {fmtStamp} from './format.js'

const BLOCKED_BY_PART = {
  a: 'Blocked while unverified: Provision Part A on every session, and therefore that form’s link, QR code and response pulling.',
  b: 'Blocked while unverified: Provision Part B on every session, and therefore the end-of-session form, its question map, and every confidence, takeaway and shoutout that would have come from it.',
}

/** One part's template: create it, do the manual step, verify it.
 *
 *  Both parts get their own card because they genuinely are separate work.
 *  Email collection is a property of a form and is carried only by a Drive copy,
 *  so verifying Part A's template says nothing whatsoever about Part B's — and a
 *  single combined "verified" tick would be the exact false reassurance trap 2
 *  produces on its own.
 */
function PartCard({entry, manualStep, connectedAccount}) {
  const {part, label, record, blocked, unreachable} = entry

  return (
    <Card padding={5}>
      <Stack gap={4}>
        <Stack direction="horizontal" gap={3} align="center" wrap="wrap">
          <Heading level={2}>{label}</Heading>
          {record ? (
            <Token
              label={blocked ? 'not verified' : 'verified'}
              color={blocked ? 'orange' : 'green'}
              size="sm"
            />
          ) : (
            <Token label="not created" color="default" size="sm" />
          )}
        </Stack>

        {unreachable ? (
          <Banner
            status="error"
            title="This template cannot be opened by the connected account"
            description={`The stored form id for this part is ${record ? record.form_id : ''}, which ${connectedAccount || 'the connected Google account'} cannot see — most often because it was created by the demo's simulated Google client and never existed in Drive. Verifying it will fail with a 404 that explains none of that. Retire it and create a real one; the replacement is a new form, so the one manual step has to be done again on it.`}
            defaultIsExpanded
          >
            <PostForm action="/template/replace" confirm={`Retire the stored ${label} template and create a new form in Drive?`}>
              <input type="hidden" name="part" value={part} />
              <Button
                label={`Create a replacement Part ${part.toUpperCase()} template`}
                variant="primary"
                type="submit"
              />
            </PostForm>
          </Banner>
        ) : blocked ? (
          <Banner
            status="warning"
            title="Provisioning is blocked for this part"
            description={BLOCKED_BY_PART[part]}
          />
        ) : (
          <Banner
            status="success"
            title="Verified"
            description={
              'The API confirmed verified email collection' +
              (record && record.verified_email_confirmed_at
                ? ` at ${fmtStamp(record.verified_email_confirmed_at)}`
                : '') +
              '. It is re-checked before every provisioning run, so an edit that breaks the template fails loudly rather than quietly producing forms that collect nothing.'
            }
          />
        )}

        {!record ? (
          <Stack gap={3}>
            <Text type="supporting">
              {part === 'a'
                ? 'Creates one form through the API with the single passphrase question and the plain-language header notice.'
                : 'Creates one form through the API with the four fields that are always on the end-of-session form: confidence, takeaway, this week’s rotating question, and the shoutout. The help checkbox is added per session at provisioning time, because whether it appears at all depends on whether anyone is configured to receive it.'}
              {' Done once, not per session.'}
            </Text>
            <PostForm action="/template/create">
              <input type="hidden" name="part" value={part} />
              <Button label={`Create the Part ${part.toUpperCase()} template`} variant="primary" type="submit" />
            </PostForm>
          </Stack>
        ) : (
          <Stack gap={4}>
            <MetadataList columns="single">
              <MetadataListItem label="Form ID">
                <Text type="code">{record.form_id}</Text>
              </MetadataListItem>
              <MetadataListItem label="Edit link">
                {record.edit_url ? (
                  <Link href={record.edit_url} isExternalLink>{record.edit_url}</Link>
                ) : '—'}
              </MetadataListItem>
              <MetadataListItem label="Responder link">
                {record.form_url ? (
                  <Link href={record.form_url} isExternalLink>{record.form_url}</Link>
                ) : '—'}
              </MetadataListItem>
              <MetadataListItem label="Verified at">
                {record.verified_email_confirmed_at
                  ? fmtStamp(record.verified_email_confirmed_at)
                  : 'never'}
              </MetadataListItem>
              <MetadataListItem label="Last checked">
                {record.last_verified_at ? fmtStamp(record.last_verified_at) : 'never'}
              </MetadataListItem>
              <MetadataListItem label="Settings last read">
                <Text type="code">{JSON.stringify(record.settings_snapshot)}</Text>
              </MetadataListItem>
            </MetadataList>

            <Stack gap={2}>
              <Heading level={3}>The one manual step</Heading>
              <Text type="supporting">
                Open this template in the Google Forms editor and change one setting. About
                thirty seconds, once per part.
              </Text>
              <CodeBlock code={manualStep || ''} isWrapped hasCopyButton={false} />
              {record.edit_url ? (
                <Stack direction="horizontal">
                  <Link href={record.edit_url} isExternalLink isStandalone>
                    {'Open this template form'}
                  </Link>
                </Stack>
              ) : null}
            </Stack>

            <Stack gap={2}>
              <Heading level={3}>Then verify</Heading>
              <Text type="supporting">
                This reads form.settings back from the API and believes only what the API
                says. Your word that you flipped the setting is not evidence.
              </Text>
              <PostForm action="/template/verify">
                <input type="hidden" name="part" value={part} />
                <Button
                  label={`Verify the Part ${part.toUpperCase()} template`}
                  variant="primary"
                  type="submit"
                />
              </PostForm>
            </Stack>
          </Stack>
        )}
      </Stack>
    </Card>
  )
}

export function TemplateSetup({
  parts = [],
  manual_step,
  notice,
  error,
  survey_rationale,
  connected_account,
}) {
  return (
    <Stack gap={4}>
      <PageHeader title="Template setup">
        Every session form is a copy of a template. Copying preserves email collection
        settings; setting them through the API does not work reliably, which is why one
        manual step exists and cannot be removed. There is one template per part, and the
        manual step is needed for each.
      </PageHeader>

      <Notices
        notice={notice}
        error={error}
        errorTitle="Not verified — provisioning stays blocked"
      />

      {parts.map((entry) => (
        <PartCard
          key={entry.part}
          entry={entry}
          manualStep={manual_step}
          connectedAccount={connected_account}
        />
      ))}

      {survey_rationale ? (
        <Card padding={5}>
          <Collapsible trigger="Why the Part B form is six fields and not seven">
            <Prose text={survey_rationale} />
          </Collapsible>
        </Card>
      ) : null}
    </Stack>
  )
}
