import {Banner} from '@astryxdesign/core/Banner'
import {Button} from '@astryxdesign/core/Button'
import {Divider} from '@astryxdesign/core/Divider'
import {DropdownMenu} from '@astryxdesign/core/DropdownMenu'
import {EmptyState} from '@astryxdesign/core/EmptyState'
import {Icon} from '@astryxdesign/core/Icon'
import {IconButton} from '@astryxdesign/core/IconButton'
import {Selector} from '@astryxdesign/core/Selector'
import {Stack, StackItem} from '@astryxdesign/core/Stack'
import {Switch} from '@astryxdesign/core/Switch'
import {Text} from '@astryxdesign/core/Text'
import {TextArea} from '@astryxdesign/core/TextArea'
import {TextInput} from '@astryxdesign/core/TextInput'
import {Token} from '@astryxdesign/core/Token'

/** The editor for one exit ticket's list of questions.
 *
 *  Controlled: the screen holds the list and gets every change back through
 *  `onChange`. Each row carries a client-side `_id` so React keeps a row's
 *  inputs attached to it while rows move — the question's `key` cannot do that
 *  job, because a new question has none until the server mints it, and a
 *  duplicate must not share one.
 *
 *  The types come from the server (`part_a_questions.QUESTION_TYPES`), so what
 *  this offers is what the validator accepts. The list below is only used if a
 *  page arrives without them.
 */

export const FALLBACK_TYPES = [
  {type: 'short_answer', label: 'Short answer', has_options: false, has_scale: false, answerable: true},
  {type: 'paragraph', label: 'Paragraph', has_options: false, has_scale: false, answerable: true},
  {type: 'multiple_choice', label: 'Multiple choice', has_options: true, has_scale: false, answerable: true},
  {type: 'checkboxes', label: 'Checkboxes', has_options: true, has_scale: false, answerable: true},
  {type: 'dropdown', label: 'Dropdown', has_options: true, has_scale: false, answerable: true},
  {type: 'linear_scale', label: 'Linear scale', has_options: false, has_scale: true, answerable: true},
  {type: 'section', label: 'Section break', has_options: false, has_scale: false, answerable: false},
  {type: 'text', label: 'Title and description', has_options: false, has_scale: false, answerable: false},
]

/** Above this many answerable questions the editor says so, and still saves.
 *  The form is open for about ten minutes after a lesson; every question past
 *  a handful is one more reason to close the tab. */
export const SOFT_LIMIT = 6

// Google Forms offers "Other" on radio and checkbox questions only; a dropdown
// with isOther is rejected by the API. The server says which (`allows_other`);
// this is the same answer for a page that arrived without it.
const TAKES_OTHER = new Set(['multiple_choice', 'checkboxes'])

function takesOther(info) {
  return info.allows_other ?? TAKES_OTHER.has(info.type)
}

const LOW_OPTIONS = [
  {value: '0', label: '0'},
  {value: '1', label: '1'},
]
const HIGH_OPTIONS = [2, 3, 4, 5, 6, 7, 8, 9, 10].map((n) => ({value: String(n), label: String(n)}))

let nextId = 0
const tempId = () => {
  nextId += 1
  return `row-${nextId}`
}

export function typeInfo(types, type) {
  return (types || FALLBACK_TYPES).find((t) => t.type === type) || {type, label: type}
}

export function isAnswerable(types, type) {
  return typeInfo(types, type).answerable !== false
}

/** Stored questions → editor rows. */
export function toRows(questions) {
  return (questions || []).map((q) => ({...q, _id: tempId()}))
}

/** One editor row → the stored shape, keeping only the fields its type uses.
 *
 *  Nothing is trimmed here: the server's validator trims, and doing it on every
 *  keystroke would eat the space someone is about to type a word after. */
export function cleanQuestion(row, types) {
  const info = typeInfo(types, row.type)
  const out = {}
  if (row.key) out.key = row.key
  out.type = row.type
  out.title = row.title || ''
  out.description = row.description || ''
  out.required = info.answerable !== false ? Boolean(row.required) : false
  if (info.has_options) {
    out.options = (row.options || []).map((o) => String(o))
    out.shuffle = Boolean(row.shuffle)
    if (takesOther(info)) out.allow_other = Boolean(row.allow_other)
  }
  if (info.has_scale) {
    const scale = row.scale || {}
    out.scale = {
      low: Number(scale.low ?? 1),
      high: Number(scale.high ?? 5),
      low_label: scale.low_label || '',
      high_label: scale.high_label || '',
    }
  }
  return out
}

export function toQuestions(rows, types) {
  return (rows || []).map((row) => cleanQuestion(row, types))
}

/** A fresh question of one type, with the parts its type needs filled in. */
function blankRow(type, types) {
  const info = typeInfo(types, type)
  const row = {_id: tempId(), type, title: '', description: '', required: info.answerable !== false}
  return withTypeParts(row, info)
}

function withTypeParts(row, info) {
  const next = {...row}
  if (info.has_options && !(next.options && next.options.length)) next.options = ['Option 1']
  if (info.has_scale && !next.scale) next.scale = {low: 1, high: 5, low_label: '', high_label: ''}
  if (info.answerable === false) next.required = false
  if (!takesOther(info)) next.allow_other = false
  return next
}

function move(list, from, to) {
  if (to < 0 || to >= list.length) return list
  const next = list.slice()
  const [item] = next.splice(from, 1)
  next.splice(to, 0, item)
  return next
}

function Options({row, info, isReadOnly, onChange}) {
  const options = row.options || []
  const set = (next) => onChange({...row, options: next})

  return (
    <Stack gap={2}>
      <Text type="label">Options</Text>
      {options.map((option, i) => (
        <Stack key={i} direction="horizontal" gap={1} align="center">
          <StackItem size="fill">
            <TextInput
              label={`Option ${i + 1}`}
              isLabelHidden
              size="sm"
              value={option}
              isReadOnly={isReadOnly}
              onChange={(value) => set(options.map((o, j) => (j === i ? value : o)))}
            />
          </StackItem>
          {isReadOnly ? null : (
            <>
              <IconButton
                label={`Move option ${i + 1} up`}
                icon={<Icon icon="arrowUp" color="inherit" />}
                variant="ghost"
                size="sm"
                isDisabled={i === 0}
                onClick={() => set(move(options, i, i - 1))}
              />
              <IconButton
                label={`Move option ${i + 1} down`}
                icon={<Icon icon="arrowDown" color="inherit" />}
                variant="ghost"
                size="sm"
                isDisabled={i === options.length - 1}
                onClick={() => set(move(options, i, i + 1))}
              />
              <IconButton
                label={`Remove option ${i + 1}`}
                icon={<Icon icon="close" color="inherit" />}
                variant="ghost"
                size="sm"
                isDisabled={options.length <= 1}
                onClick={() => set(options.filter((_, j) => j !== i))}
              />
            </>
          )}
        </Stack>
      ))}
      {isReadOnly ? null : (
        <Stack direction="horizontal">
          <Button
            label="Add an option"
            size="sm"
            onClick={() => set(options.concat(`Option ${options.length + 1}`))}
          />
        </Stack>
      )}
      <Stack direction="horizontal" gap={4} wrap="wrap">
        {takesOther(info) ? (
          <Switch
            label="Offer “Other” with a text box"
            size="sm"
            value={Boolean(row.allow_other)}
            isDisabled={isReadOnly}
            onChange={(value) => onChange({...row, allow_other: value})}
          />
        ) : null}
        <Switch
          label="Shuffle the order for each person"
          size="sm"
          value={Boolean(row.shuffle)}
          isDisabled={isReadOnly}
          onChange={(value) => onChange({...row, shuffle: value})}
        />
      </Stack>
    </Stack>
  )
}

function Scale({row, isReadOnly, onChange}) {
  const scale = row.scale || {low: 1, high: 5, low_label: '', high_label: ''}
  const set = (patch) => onChange({...row, scale: {...scale, ...patch}})

  return (
    <Stack gap={2}>
      <Text type="label">Scale</Text>
      <Stack direction="horizontal" gap={3} wrap="wrap" align="end">
        <Selector
          label="From"
          size="sm"
          value={String(scale.low ?? 1)}
          options={LOW_OPTIONS}
          isDisabled={isReadOnly}
          onChange={(value) => set({low: Number(value)})}
        />
        <Selector
          label="To"
          size="sm"
          value={String(scale.high ?? 5)}
          options={HIGH_OPTIONS}
          isDisabled={isReadOnly}
          onChange={(value) => set({high: Number(value)})}
        />
        <TextInput
          label={`Label for ${scale.low ?? 1}`}
          isOptional
          size="sm"
          value={scale.low_label || ''}
          isReadOnly={isReadOnly}
          onChange={(value) => set({low_label: value})}
          placeholder="Not at all"
        />
        <TextInput
          label={`Label for ${scale.high ?? 5}`}
          isOptional
          size="sm"
          value={scale.high_label || ''}
          isReadOnly={isReadOnly}
          onChange={(value) => set({high_label: value})}
          placeholder="Completely"
        />
      </Stack>
    </Stack>
  )
}

function QuestionRow({row, index, count, types, isReadOnly, onChange, onMove, onDuplicate, onRemove}) {
  const info = typeInfo(types, row.type)
  const answerable = info.answerable !== false
  const titleLabel =
    row.type === 'section' ? 'Section title' : row.type === 'text' ? 'Heading' : 'Question'
  const typeOptions = (types || FALLBACK_TYPES).map((t) => ({value: t.type, label: t.label}))

  return (
    <Stack gap={3}>
      <Stack direction="horizontal" gap={2} align="center" justify="between" wrap="wrap">
        <Stack direction="horizontal" gap={2} align="center" wrap="wrap">
          <Text type="label" hasTabularNumbers>{`${index + 1}.`}</Text>
          <Token label={info.label || row.type} color={answerable ? 'blue' : 'default'} size="sm" />
          {answerable && row.required ? <Token label="required" color="default" size="sm" /> : null}
          <Text type="supporting">{row.key || 'new'}</Text>
        </Stack>
        {isReadOnly ? null : (
          <Stack direction="horizontal" gap={0.5}>
            <IconButton
              label={`Move question ${index + 1} up`}
              tooltip="Move up"
              icon={<Icon icon="arrowUp" color="inherit" />}
              variant="ghost"
              size="sm"
              isDisabled={index === 0}
              onClick={() => onMove(index, index - 1)}
            />
            <IconButton
              label={`Move question ${index + 1} down`}
              tooltip="Move down"
              icon={<Icon icon="arrowDown" color="inherit" />}
              variant="ghost"
              size="sm"
              isDisabled={index === count - 1}
              onClick={() => onMove(index, index + 1)}
            />
            <IconButton
              label={`Duplicate question ${index + 1}`}
              tooltip="Duplicate"
              icon={<Icon icon="copy" color="inherit" />}
              variant="ghost"
              size="sm"
              onClick={() => onDuplicate(index)}
            />
            <IconButton
              label={`Delete question ${index + 1}`}
              tooltip="Delete"
              icon={<Icon icon="close" color="inherit" />}
              variant="ghost"
              size="sm"
              onClick={() => onRemove(index)}
            />
          </Stack>
        )}
      </Stack>

      <Stack direction="horizontal" gap={3} wrap="wrap" align="start">
        <Selector
          label="Type"
          value={row.type}
          options={typeOptions}
          isDisabled={isReadOnly}
          onChange={(type) => onChange(withTypeParts({...row, type}, typeInfo(types, type)))}
        />
        <StackItem size="fill">
          <TextInput
            label={titleLabel}
            isRequired={row.type !== 'text'}
            value={row.title || ''}
            isReadOnly={isReadOnly}
            onChange={(title) => onChange({...row, title})}
            autoComplete="off"
          />
        </StackItem>
      </Stack>

      <TextArea
        label="Description"
        isOptional
        rows={2}
        value={row.description || ''}
        isReadOnly={isReadOnly}
        onChange={(description) => onChange({...row, description})}
      />

      {answerable ? (
        <Switch
          label="Required"
          value={Boolean(row.required)}
          isDisabled={isReadOnly}
          onChange={(required) => onChange({...row, required})}
        />
      ) : null}

      {info.has_options ? (
        <Options row={row} info={info} isReadOnly={isReadOnly} onChange={onChange} />
      ) : null}
      {info.has_scale ? <Scale row={row} isReadOnly={isReadOnly} onChange={onChange} /> : null}
    </Stack>
  )
}

export function QuestionListEditor({rows = [], types, isReadOnly = false, onChange}) {
  const list = types && types.length ? types : FALLBACK_TYPES
  const answerableCount = rows.filter((row) => isAnswerable(list, row.type)).length

  const update = (index, row) => onChange(rows.map((r, i) => (i === index ? row : r)))
  const add = (type) => onChange(rows.concat(blankRow(type, list)))
  const duplicate = (index) => {
    // A copy is a new question: it gets its own key from the server, so the
    // answers to the two are never mixed up.
    const {key: _key, ...rest} = rows[index]
    const copy = {...rest, _id: tempId(), title: rest.title ? `${rest.title} (copy)` : ''}
    const next = rows.slice()
    next.splice(index + 1, 0, copy)
    onChange(next)
  }
  const remove = (index) => onChange(rows.filter((_, i) => i !== index))

  const addMenu = isReadOnly ? null : (
    <DropdownMenu
      button={{label: 'Add a question', variant: 'primary'}}
      items={list.map((t) => ({
        id: t.type,
        label: t.label,
        description: t.answerable === false ? 'Not a question — no answer is collected' : undefined,
        onClick: () => add(t.type),
      }))}
    />
  )

  return (
    <Stack gap={4}>
      {answerableCount > SOFT_LIMIT && !isReadOnly ? (
        <Banner
          status="warning"
          title={`${answerableCount} questions to answer`}
          description={`The form is open for about ten minutes after the lesson. Past ${SOFT_LIMIT} questions, fewer people finish it — and a form left unfinished records nobody as present. It will still save.`}
        />
      ) : null}

      {!rows.length ? (
        <EmptyState
          title="No questions yet"
          description="Add one below. An exit ticket needs at least one question with an answer, since submitting it is what records someone as present."
          headingLevel={3}
        />
      ) : (
        rows.map((row, index) => (
          <Stack key={row._id} gap={4}>
            {index ? <Divider /> : null}
            <QuestionRow
              row={row}
              index={index}
              count={rows.length}
              types={list}
              isReadOnly={isReadOnly}
              onChange={(next) => update(index, next)}
              onMove={(from, to) => onChange(move(rows, from, to))}
              onDuplicate={duplicate}
              onRemove={remove}
            />
          </Stack>
        ))
      )}

      {addMenu ? (
        <Stack direction="horizontal" gap={3} align="center" wrap="wrap">
          {addMenu}
          <Text type="supporting">
            {`${answerableCount} question${answerableCount === 1 ? '' : 's'} to answer`}
          </Text>
        </Stack>
      ) : null}
    </Stack>
  )
}
