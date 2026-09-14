import './layers.css'
import '@astryxdesign/core/reset.css'
import '@astryxdesign/theme-neutral/theme.css'

import {neutralTheme} from '@astryxdesign/theme-neutral/built'
import {Theme} from '@astryxdesign/core/theme'
import {createRoot} from 'react-dom/client'

import {AppFrame, FellowFrame} from './AppFrame.jsx'
import {AssignmentForm} from './AssignmentForm.jsx'
import {Assignments} from './Assignments.jsx'
import {Connect} from './Connect.jsx'
import {Dashboard} from './Dashboard.jsx'
import {Fellow} from './Fellow.jsx'
import {HelpRequests} from './HelpRequests.jsx'
import {Responses} from './Responses.jsx'
import {Review} from './Review.jsx'
import {Roster} from './Roster.jsx'
import {Rotation} from './Rotation.jsx'
import {SessionDetail} from './SessionDetail.jsx'
import {SessionForm} from './SessionForm.jsx'
import {Sessions} from './Sessions.jsx'
import {Shoutouts} from './Shoutouts.jsx'
import {SignIn} from './SignIn.jsx'
import {DbDown, Message} from './Simple.jsx'
import {TemplateSetup} from './TemplateSetup.jsx'

// The server hands the screen its data in a JSON script tag rather than a
// fetch. Every screen has to paint correctly on the first response — sign-in
// especially, since it is reachable with no session to fetch with.
function bootState() {
  const tag = document.getElementById('__CUFA_STATE__')
  if (!tag) return {}
  try {
    return JSON.parse(tag.textContent || '{}')
  } catch {
    return {}
  }
}

const SCREENS = {
  signin: SignIn,
  connect: Connect,
  dashboard: Dashboard,
  me: Fellow,
  template: TemplateSetup,
  sessions: Sessions,
  assignments: Assignments,
  assignmentForm: AssignmentForm,
  roster: Roster,
  sessionForm: SessionForm,
  sessionDetail: SessionDetail,
  responses: Responses,
  rotation: Rotation,
  shoutouts: Shoutouts,
  helpRequests: HelpRequests,
  review: Review,
  message: Message,
  dbDown: DbDown,
}

// Which frame a screen hangs in, named by the server. Sign-in has no nav and
// no user; a fellow's own page is not a staff screen and gets neither the staff
// nav nor the warnings about a console they are not signed in to.
const FRAMES = {none: null, staff: AppFrame, fellow: FellowFrame}

const state = bootState()
const Screen = SCREENS[state.screen]
const mount = document.getElementById('root')

if (Screen && mount) {
  const screen = <Screen {...state} />
  const Frame = state.frame in FRAMES ? FRAMES[state.frame] : AppFrame
  createRoot(mount).render(
    <Theme theme={neutralTheme}>
      {Frame ? (
        <Frame
          user={state.user}
          path={state.path}
          fakeGoogle={state.fakeGoogle}
          noAllowlist={state.noAllowlist}
        >
          {screen}
        </Frame>
      ) : (
        screen
      )}
    </Theme>,
  )
}
