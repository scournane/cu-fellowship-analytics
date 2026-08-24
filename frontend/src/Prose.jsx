import {Stack} from '@astryxdesign/core/Stack'
import {Text} from '@astryxdesign/core/Text'

/** Multi-paragraph server text, rendered as paragraphs rather than one blob.
 *
 *  Several of these strings live in Python — the survey-length rationale, the
 *  confidence interpretation note, the safeguarding explanation — because they
 *  are the same words the CLI prints and the docs quote, and three copies of a
 *  paragraph is three chances for them to disagree. Splitting on blank lines
 *  here keeps them readable without a `white-space: pre-wrap` rule, which the
 *  component library has no token for.
 */
export function Prose({text, type = 'supporting', gap = 2}) {
  const paragraphs = String(text || '')
    .split(/\n{2,}/)
    .map((p) => p.trim())
    .filter(Boolean)

  if (!paragraphs.length) return null

  return (
    <Stack gap={gap}>
      {paragraphs.map((paragraph, index) => (
        <Text key={index} as="p" type={type}>
          {paragraph}
        </Text>
      ))}
    </Stack>
  )
}
