import './layers.css'
import '@astryxdesign/core/reset.css'
import '@astryxdesign/theme-neutral/theme.css'

import {neutralTheme} from '@astryxdesign/theme-neutral/built'
import {Theme} from '@astryxdesign/core/theme'
import {createRoot} from 'react-dom/client'

import {AppFrame} from './AppFrame.jsx'
import {Connect} from './Connect.jsx'
import {HelpRequests} from './HelpRequests.jsx'
import {Responses} from './Responses.jsx'
import {Review} from './Review.jsx'
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
  template: TemplateSetup,
  sessions: Sessions,
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

// Sign-in has no nav and no user, so it renders outside the app frame.
const UNFRAMED = new Set(['signin'])

const state = bootState()
const Screen = SCREENS[state.screen]
const mount = document.getElementById('root')

if (Screen && mount) {
  const screen = <Screen {...state} />
  createRoot(mount).render(
    <Theme theme={neutralTheme}>
      {UNFRAMED.has(state.screen) ? (
        screen
      ) : (
        <AppFrame
          user={state.user}
          path={state.path}
          fakeGoogle={state.fakeGoogle}
          noAllowlist={state.noAllowlist}
        >
          {screen}
        </AppFrame>
      )}
    </Theme>,
  )
}
