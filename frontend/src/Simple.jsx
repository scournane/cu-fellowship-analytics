import {Banner} from '@astryxdesign/core/Banner'
import {Button} from '@astryxdesign/core/Button'
import {Card} from '@astryxdesign/core/Card'
import {CodeBlock} from '@astryxdesign/core/CodeBlock'
import {Stack} from '@astryxdesign/core/Stack'
import {Text} from '@astryxdesign/core/Text'

import {NAV, PageHeader} from './AppFrame.jsx'

export function Message({heading, body, code, link, link_label}) {
  return (
    <Stack gap={4}>
      <PageHeader title={heading} />
      <Card padding={5}>
        <Stack gap={3}>
          <Text>{body}</Text>
          {/* Long, opaque, and has to be transcribed exactly — so it gets a
              copy button rather than a line of text to select by hand. */}
          {code ? <CodeBlock code={code} isWrapped hasCopyButton /> : null}
          {link ? (
            <Stack direction="horizontal">
              <Button label={link_label} href={link} />
            </Stack>
          ) : null}
        </Stack>
      </Card>
    </Stack>
  )
}

export function DbDown({hint}) {
  return (
    <Stack gap={4}>
      <PageHeader title="The database is not answering" />
      <Banner
        status="error"
        title="Every screen in this console reads from Postgres"
        description="None of them can show you anything until it is back. Nothing has been lost — this is a connection failure, not a data failure."
      />
      <CodeBlock code={hint || ''} isWrapped hasCopyButton={false} />
      <Card padding={5}>
        <Stack gap={3}>
          <Text>Once it is running, come back to any screen:</Text>
          <Stack direction="horizontal" gap={2} wrap="wrap">
            {NAV.map((item) => (
              <Button key={item.href} label={item.label} href={item.href} />
            ))}
          </Stack>
        </Stack>
      </Card>
    </Stack>
  )
}
