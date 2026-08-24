import {Selector} from '@astryxdesign/core/Selector'

// Where the console's two filtered screens live. Both the tab links and the
// cohort dropdown build their URLs here, so they cannot disagree about which
// query parameters survive a change of filter — which is exactly what happened
// while `tabHref` and `CohortFilter` each had their own URLSearchParams.

function url(basePath, params) {
  const query = new URLSearchParams(
    Object.entries(params).filter(([, value]) => Boolean(value)),
  ).toString()
  return query ? `${basePath}?${query}` : basePath
}

export function sessionsUrl({cohort} = {}) {
  return url('/sessions', {cohort})
}

export function reviewUrl({tab, cohort} = {}) {
  return url('/review', {tab, cohort})
}

export function rotationUrl({cohort} = {}) {
  return url('/rotation', {cohort})
}

export function shoutoutsUrl({cohort} = {}) {
  return url('/shoutouts', {cohort})
}

export function helpRequestsUrl({status, cohort} = {}) {
  return url('/help-requests', {status, cohort})
}

export function cohortOptions(cohorts, {includeAll = true} = {}) {
  const options = (cohorts || []).map((c) => ({
    value: c.cohort_id,
    label: `${c.label} (${c.cohort_id})`,
  }))
  return includeAll ? [{value: '', label: 'All cohorts'}].concat(options) : options
}

/** Navigating on change keeps the server as the source of truth for the filter,
 *  exactly as the <select onchange="this.form.submit()"> did. */
export function CohortFilter({cohorts, selected, hrefFor}) {
  return (
    <Selector
      label="Cohort"
      size="sm"
      value={selected || ''}
      options={cohortOptions(cohorts)}
      onChange={(value) => {
        window.location.href = hrefFor(value)
      }}
    />
  )
}
