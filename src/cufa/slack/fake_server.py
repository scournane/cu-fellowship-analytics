"""An HTTP stand-in for Slack, so the real bot can be driven from a browser.

Two halves on one port:

* ``/api/<method>`` — the Web API. The bot's ``slack_sdk.WebClient`` is pointed
  here via ``SLACK_API_BASE_URL``; ``auth.test``, ``users.info`` and the rest
  answer from a ``FakeWorkspace``. Same library, same calls as production.
* ``/`` and ``/ui/*`` — a page with buttons. Each button builds an Events API
  envelope, **signs it with the bot's signing secret exactly as Slack would**,
  and POSTs it to the bot's ``/slack/events``. The bot's real signature check
  runs. A "replay" button re-sends the last envelope with Slack's retry
  headers, which is how the idempotency claim gets demonstrated rather than
  asserted.

Standard library only: this has to run on the same machine as the bot with
nothing installed beyond the project itself.
"""

from __future__ import annotations

import json
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .fake import FakeSlackWebClient, FakeWorkspace, demo_workspace
from .signing import sign

SAMPLE_MESSAGES = (
    "Just finished the reading — the part about ranked-choice voting surprised me",
    "does anyone have the slides from tuesday?",
    "I think the coalition idea could actually work in our district",
    "+1",
    "Sharing this: https://example.invalid/article-on-local-budgets",
    "can't make thursday, will catch the recording",
    "what does 'quorum' mean in this context?",
    "my project team is meeting at 6 if anyone wants to join",
    "ok",
    "This changed how I think about school board meetings honestly",
    "thanks for the help earlier!!",
    "who's presenting first next week?",
)
REACTIONS = ("thumbsup", "heart", "raised_hands", "fire", "eyes", "tada", "100")

#: What the Q&A buttons post when the text box is empty. The last one is a
#: paraphrase of the first, so "ask it again" has something to match.
SAMPLE_QUESTIONS = (
    "does anyone have the slides from tuesday?",
    "what does 'quorum' mean in this context?",
    "is the reading due before or after thursday's session?",
    "can someone share tuesday's slides?",
)
SAMPLE_ANSWERS = (
    "yes — they're pinned in #announcements",
    "the minimum number of members who have to be present for a vote to count",
    "before: it's the basis for the discussion",
)
QA_CHANNEL = "q-and-a"


class FakeSlackHTTPServer:
    def __init__(
        self,
        workspace: FakeWorkspace,
        *,
        signing_secret: str,
        bot_events_url: str,
        bot_stats_url: str | None = None,
        host: str = "127.0.0.1",
        port: int = 3001,
        seed: int | None = None,
    ) -> None:
        self.ws = workspace
        self.client = FakeSlackWebClient(workspace)
        self.signing_secret = signing_secret
        self.bot_events_url = bot_events_url
        self.bot_stats_url = bot_stats_url or bot_events_url.rsplit("/slack/events", 1)[0] + "/stats"
        self.host, self.port = host, port
        self.log: deque[dict[str, Any]] = deque(maxlen=200)
        self.last_envelope: dict[str, Any] | None = None
        self.last_messages: dict[str, dict[str, Any]] = {}  # channel -> last message event
        self.questions: list[dict[str, Any]] = []  # Q&A questions asked, in order
        self.last_answer: dict[str, Any] | None = None
        self.rng = random.Random(seed)
        self._lock = threading.Lock()
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # -- delivery to the bot --------------------------------------------------

    def deliver(self, envelope: dict[str, Any], *, retry: int | None = None, tamper: bool = False) -> dict[str, Any]:
        body = json.dumps(envelope)
        headers = sign("wrong-secret" if tamper else self.signing_secret, body)
        if retry:
            headers["X-Slack-Retry-Num"] = str(retry)
            headers["X-Slack-Retry-Reason"] = "http_timeout"
        req = urllib.request.Request(self.bot_events_url, data=body.encode("utf-8"), headers=headers, method="POST")
        started = time.time()
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                status, text = resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            status, text = exc.code, exc.read().decode("utf-8", "replace")
        except urllib.error.URLError as exc:
            status, text = 0, f"bot unreachable: {exc.reason}"
        entry = self._describe(envelope, status, text, retry=retry, tamper=tamper, ms=int((time.time() - started) * 1000))
        with self._lock:
            self.log.appendleft(entry)
            self.last_envelope = envelope
        return entry

    def _describe(self, envelope: dict[str, Any], status: int, text: str, *, retry: int | None, tamper: bool, ms: int) -> dict[str, Any]:
        ev = envelope.get("event") or {}
        who = self._name(ev.get("user") or (ev.get("message") or {}).get("user") or (ev.get("previous_message") or {}).get("user"))
        where = self._channel_name(ev.get("channel") or (ev.get("item") or {}).get("channel"))
        kind = ev.get("type", "?")
        if ev.get("subtype"):
            kind += f"/{ev['subtype']}"
        if ev.get("reaction"):
            kind += f" :{ev['reaction']}:"
        return {
            "at": datetime.now(timezone.utc).strftime("%H:%M:%S"),
            "kind": kind,
            "who": who,
            "where": f"#{where}" if where else "",
            "status": status,
            "note": ("retry" if retry else "") + (" BAD SIGNATURE" if tamper else ""),
            "ms": ms,
            "response": text[:120],
            "event_id": envelope.get("event_id"),
        }

    def _name(self, uid: str | None) -> str:
        u = self.ws.users.get(uid or "") or {}
        return u.get("real_name") or uid or "?"

    def _channel_name(self, cid: str | None) -> str:
        return (self.ws.channels.get(cid or "") or {}).get("name") or cid or ""

    # -- actions the UI can trigger -------------------------------------------

    def people(self) -> list[str]:
        return [uid for uid, u in self.ws.users.items() if not u.get("is_bot") and not u.get("deleted")]

    def act_message(self, user: str, channel: str, text: str, *, thread_ts: str | None = None) -> dict[str, Any]:
        event = self.ws.message_event(user, channel, text, thread_ts=thread_ts)
        self.last_messages[channel] = event
        return self.deliver(self.ws.envelope(event))

    def act_reaction(self, user: str, channel: str, reaction: str, *, ts: str | None = None, removed: bool = False) -> dict[str, Any]:
        target = ts or (self.last_messages.get(channel) or {}).get("ts")
        if not target:
            return {"error": "no message in that channel to react to yet"}
        return self.deliver(self.ws.envelope(self.ws.reaction_event(user, channel, target, reaction, removed=removed)))

    def act_join(self, user: str, channel: str, *, left: bool = False) -> dict[str, Any]:
        return self.deliver(self.ws.envelope(self.ws.join_event(user, channel, left=left)))

    def act_edit(self, channel: str, text: str) -> dict[str, Any]:
        original = self.last_messages.get(channel)
        if not original:
            return {"error": "no message in that channel to edit"}
        return self.deliver(self.ws.envelope(self.ws.edit_event(original, text)))

    def act_delete(self, channel: str) -> dict[str, Any]:
        original = self.last_messages.pop(channel, None)
        if not original:
            return {"error": "no message in that channel to delete"}
        return self.deliver(self.ws.envelope(self.ws.delete_event(original)))

    def act_bot_message(self, channel: str) -> dict[str, Any]:
        return self.deliver(self.ws.envelope(self.ws.bot_message_event(channel, "Reminder: check-in form opens in 5 minutes")))

    def act_replay(self) -> dict[str, Any]:
        with self._lock:
            env = self.last_envelope
        if not env:
            return {"error": "nothing to replay yet"}
        return self.deliver(env, retry=1)

    def act_tamper(self) -> dict[str, Any]:
        with self._lock:
            env = self.last_envelope
        if not env:
            return {"error": "nothing to replay yet"}
        return self.deliver(env, tamper=True)

    # -- Q&A ------------------------------------------------------------------

    @property
    def qa_channel(self) -> str:
        return self.ws.channel_id(QA_CHANNEL)

    def act_qa_ask(self, user: str, text: str = "") -> dict[str, Any]:
        """A top-level question in #q-and-a."""
        text = text or SAMPLE_QUESTIONS[len(self.questions) % len(SAMPLE_QUESTIONS)]
        event = self.ws.message_event(user, self.qa_channel, text)
        self.questions.append(event)
        self.last_messages[self.qa_channel] = event
        return self.deliver(self.ws.envelope(event))

    def act_qa_answer(self, user: str, text: str = "") -> dict[str, Any]:
        """A reply in the thread of the LAST question."""
        if not self.questions:
            return {"error": "ask a question first"}
        question = self.questions[-1]
        text = text or SAMPLE_ANSWERS[(len(self.questions) - 1) % len(SAMPLE_ANSWERS)]
        event = self.ws.message_event(user, self.qa_channel, text, thread_ts=question["ts"])
        self.last_answer = event
        return self.deliver(self.ws.envelope(event))

    def act_qa_accept(self, user: str) -> dict[str, Any]:
        """✅ on the last answer — "this is the answer"."""
        if not self.last_answer:
            return {"error": "answer a question first"}
        return self.deliver(self.ws.envelope(
            self.ws.reaction_event(user, self.qa_channel, self.last_answer["ts"], "white_check_mark")
        ))

    def act_qa_again(self, user: str, text: str = "") -> dict[str, Any]:
        """Ask the FIRST question again, reworded. If it was answered, the bot
        should reply in this new thread with a link to that answer."""
        if not self.questions:
            return {"error": "ask a question first"}
        first = self.questions[0]["text"]
        text = text or f"sorry if this was covered already — {first}"
        return self.act_qa_ask(user, text)

    def act_mention(self, user: str, channel: str, text: str = "summary") -> dict[str, Any]:
        """``@bot …``. Slack delivers a message event AND an app_mention event
        for the same post; so does this."""
        channel = channel or self.qa_channel
        mention = self.ws.mention_event(user, channel, text)
        as_message = self.ws.message_event(user, channel, mention["text"], ts=mention["ts"])
        self.deliver(self.ws.envelope(as_message))
        return self.deliver(self.ws.envelope(mention))

    def act_busy_day(self, n: int = 40, *, quiet_user: str | None = None) -> list[dict[str, Any]]:
        """A plausible day: messages across channels, some threads, reactions, a join."""
        people = [p for p in self.people() if p != quiet_user]
        # Not the Q&A channel: chatter there would read as questions in the
        # summary, and the Q&A buttons script that channel deliberately.
        public = [cid for cid, ch in self.ws.channels.items() if not ch["is_private"] and ch["name"] != QA_CHANNEL]
        out: list[dict[str, Any]] = []
        for _ in range(n):
            roll = self.rng.random()
            channel = self.rng.choice(public)
            if roll < 0.55 or channel not in self.last_messages:
                user = self.rng.choice(people)
                thread = None
                if self.rng.random() < 0.25 and channel in self.last_messages:
                    thread = self.last_messages[channel]["ts"]
                out.append(self.act_message(user, channel, self.rng.choice(SAMPLE_MESSAGES), thread_ts=thread))
            elif roll < 0.92:
                out.append(self.act_reaction(self.rng.choice(people), channel, self.rng.choice(REACTIONS)))
            else:
                out.append(self.act_join(self.rng.choice(people), channel))
        return out

    # -- state for the UI -----------------------------------------------------

    def state(self) -> dict[str, Any]:
        with self._lock:
            log = list(self.log)
        return {
            "team": {"id": self.ws.team_id, "name": self.ws.team_name},
            "bot_events_url": self.bot_events_url,
            "bot_stats_url": self.bot_stats_url,
            "users": [
                {
                    "id": uid,
                    "name": u.get("real_name") or u.get("name"),
                    "email": (u.get("profile") or {}).get("email"),
                    "deleted": bool(u.get("deleted")),
                    "bot": bool(u.get("is_bot")),
                }
                for uid, u in self.ws.users.items()
            ],
            "channels": [{"id": cid, "name": c["name"], "private": c["is_private"]} for cid, c in self.ws.channels.items()],
            "last_message_ts": {cid: ev.get("ts") for cid, ev in self.last_messages.items()},
            "log": log,
            "api_calls": len(self.client.calls),
            "qa_channel": self.qa_channel,
            "questions_asked": len(self.questions),
            # What the bot said back — the pointers and the summaries.
            "posted": [
                {"channel": self._channel_name(p["channel"]), "thread_ts": p.get("thread_ts"), "text": p["text"]}
                for p in self.ws.posted[-10:]
            ],
        }

    # -- server ---------------------------------------------------------------

    def _make_handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:  # silence
                pass

            def _json(self, status: int, payload: Any) -> None:
                body = json.dumps(payload, default=str).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _html(self, html: str) -> None:
                body = html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _body(self) -> dict[str, Any]:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                ctype = (self.headers.get("Content-Type") or "").lower()
                if "json" in ctype:
                    try:
                        return json.loads(raw.decode("utf-8") or "{}")
                    except json.JSONDecodeError:
                        return {}
                parsed = urllib.parse.parse_qs(raw.decode("utf-8"), keep_blank_values=True)
                return {k: v[0] if len(v) == 1 else v for k, v in parsed.items()}

            def do_GET(self) -> None:
                path = urllib.parse.urlparse(self.path).path
                if path == "/":
                    return self._html(UI_PAGE)
                if path == "/ui/state":
                    return self._json(200, server.state())
                if path.startswith("/api/"):
                    return self._json(200, server.client.dispatch(path[5:], dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))))
                self._json(404, {"ok": False, "error": "not_found"})

            def do_POST(self) -> None:
                path = urllib.parse.urlparse(self.path).path
                params = self._body()
                if path.startswith("/api/"):
                    return self._json(200, server.client.dispatch(path[5:], params))
                if path.startswith("/ui/"):
                    return self._json(200, server.ui_action(path[4:], params))
                self._json(404, {"ok": False, "error": "not_found"})

        return Handler

    def ui_action(self, action: str, p: dict[str, Any]) -> Any:
        user, channel, text = p.get("user") or "", p.get("channel") or "", p.get("text") or ""
        if action == "message":
            return self.act_message(user, channel, text or self.rng.choice(SAMPLE_MESSAGES), thread_ts=p.get("thread_ts") or None)
        if action == "reaction":
            return self.act_reaction(user, channel, p.get("reaction") or "thumbsup", removed=str(p.get("removed", "")).lower() in ("1", "true"))
        if action == "join":
            return self.act_join(user, channel)
        if action == "edit":
            return self.act_edit(channel, text or "(edited) " + self.rng.choice(SAMPLE_MESSAGES))
        if action == "delete":
            return self.act_delete(channel)
        if action == "bot-message":
            return self.act_bot_message(channel)
        if action == "replay":
            return self.act_replay()
        if action == "tamper":
            return self.act_tamper()
        if action == "busy-day":
            return {"delivered": len(self.act_busy_day(int(p.get("n") or 40), quiet_user=p.get("quiet_user") or None))}
        if action == "qa-ask":
            return self.act_qa_ask(user, text)
        if action == "qa-answer":
            return self.act_qa_answer(user, text)
        if action == "qa-accept":
            return self.act_qa_accept(user)
        if action == "qa-again":
            return self.act_qa_again(user, text)
        if action == "mention":
            return self.act_mention(user, channel, text or "summary")
        return {"error": f"unknown action {action}"}

    def start_in_thread(self) -> "FakeSlackHTTPServer":
        self._httpd = ThreadingHTTPServer((self.host, self.port), self._make_handler())
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="fake-slack", daemon=True)
        self._thread.start()
        return self

    def serve_forever(self) -> None:
        self._httpd = ThreadingHTTPServer((self.host, self.port), self._make_handler())
        self.port = self._httpd.server_address[1]
        self._httpd.serve_forever()

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()

    @property
    def api_base_url(self) -> str:
        return f"http://{self.host}:{self.port}/api/"

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"


UI_PAGE = """<!doctype html>
<meta charset="utf-8"><title>Fake Slack — CIF demo workspace</title>
<style>
  :root{--ink:#1a1a1a;--mute:#6b6b6b;--line:#e4e4e1;--bg:#f7f7f5;--card:#fff;--accent:#4a154b;--ok:#2f7d4f;--bad:#b3261e}
  body{font:14px/1.45 system-ui,sans-serif;margin:0;color:var(--ink);background:var(--bg)}
  header{background:var(--accent);color:#fff;padding:.9rem 1.5rem;display:flex;justify-content:space-between;align-items:baseline}
  header h1{font-size:1.05rem;margin:0;font-weight:600} header a{color:#fff;opacity:.85}
  main{display:grid;grid-template-columns:22rem 1fr;gap:1.25rem;padding:1.25rem 1.5rem;max-width:80rem}
  .card{background:var(--card);border:1px solid var(--line);border-radius:.6rem;padding:1rem 1.1rem;margin-bottom:1rem}
  .card h2{font-size:.8rem;text-transform:uppercase;letter-spacing:.05em;color:var(--mute);margin:0 0 .6rem}
  label{display:block;font-size:.8rem;color:var(--mute);margin:.5rem 0 .2rem}
  select,input,textarea{width:100%;box-sizing:border-box;padding:.45rem .55rem;border:1px solid var(--line);border-radius:.4rem;font:inherit;background:#fff}
  .row{display:flex;gap:.5rem;flex-wrap:wrap;margin-top:.6rem}
  button{padding:.45rem .8rem;border:1px solid var(--line);border-radius:.4rem;background:#fff;font:inherit;cursor:pointer}
  button.primary{background:var(--accent);color:#fff;border-color:var(--accent)}
  button.warn{border-color:#d9a300;background:#fff7dd} button:hover{filter:brightness(.97)}
  table{border-collapse:collapse;width:100%} th,td{text-align:left;padding:.35rem .5rem;border-top:1px solid var(--line);font-size:.85rem;vertical-align:top}
  th{border-top:0;color:var(--mute);font-weight:500;font-size:.75rem} td.s200{color:var(--ok);font-weight:600} td.bad{color:var(--bad);font-weight:600}
  .tag{display:inline-block;font-size:.7rem;padding:.05rem .4rem;border-radius:.3rem;background:#eee;color:var(--mute);margin-left:.3rem}
  .hint{color:var(--mute);font-size:.8rem;margin:.25rem 0 0}
  iframe{width:100%;height:30rem;border:1px solid var(--line);border-radius:.6rem;background:#fff}
</style>
<header><h1 id="title">Fake Slack</h1><a id="botlink" href="#" target="_blank">bot status page ↗</a></header>
<main>
<div>
  <div class="card"><h2>Who &amp; where</h2>
    <label>Person</label><select id="user"></select>
    <p class="hint">Roster fellows, plus three deliberate edge cases: a guest with no roster match, a profile with no email, and a deactivated account. All of their events must still be recorded.</p>
    <label>Channel</label><select id="channel"></select>
  </div>
  <div class="card"><h2>Do something</h2>
    <label>Message text (blank = random)</label><textarea id="text" rows="2"></textarea>
    <div class="row">
      <button class="primary" onclick="act('message')">Post message</button>
      <button onclick="act('message',{thread:true})">Reply in thread</button>
      <button onclick="act('reaction')">React 👍</button>
      <button onclick="act('reaction',{reaction:'heart'})">React ❤️</button>
      <button onclick="act('join')">Join channel</button>
      <button onclick="act('edit')">Edit last</button>
      <button onclick="act('delete')">Delete last</button>
    </div>
  </div>
  <div class="card"><h2>Prove the guarantees</h2>
    <div class="row">
      <button class="warn" onclick="act('replay')">Replay last delivery (retry)</button>
      <button class="warn" onclick="act('tamper')">Send with bad signature</button>
      <button onclick="act('bot-message')">Bot message (should skip)</button>
    </div>
    <p class="hint"><b>Replay</b> re-sends the identical event with Slack's retry headers: the bot answers 200 and writes nothing — watch "duplicates dropped" on the status page. <b>Bad signature</b> must be rejected outright.</p>
  </div>
  <div class="card"><h2>Simulate a day</h2>
    <div class="row"><button class="primary" onclick="busy(40)">40 events</button><button onclick="busy(150)">150 events</button></div>
    <p class="hint">Random messages, thread replies, reactions and joins across the public channels. Re-run it and the counts keep rising; replay any of it and they do not.</p>
  </div>
  <div class="card"><h2>Q&amp;A in #q-and-a</h2>
    <label>Question or answer text (blank = a sample)</label><textarea id="qatext" rows="2"></textarea>
    <div class="row">
      <button class="primary" onclick="qa('qa-ask')">Ask a question</button>
      <button onclick="qa('qa-answer')">Answer it (in thread)</button>
      <button onclick="qa('qa-accept')">Mark the answer ✅</button>
      <button class="warn" onclick="qa('qa-again')">Ask the first question again</button>
      <button onclick="qa('mention')">@bot summary</button>
    </div>
    <p class="hint">Ask, answer, then <b>ask the first question again</b>: the bot replies in the new thread pointing at the earlier answer. <b>@bot summary</b> posts the session's Q&amp;A digest for the teacher. Both appear under "What the bot posted". The person selected above is the one acting.</p>
  </div>
</div>
<div>
  <div class="card"><h2>Deliveries to the bot <span class="tag" id="calls"></span></h2>
    <table id="log"><tr><th>time</th><th>event</th><th>who</th><th>where</th><th>bot</th><th>ms</th><th>note</th></tr></table>
  </div>
  <div class="card"><h2>What the bot posted</h2>
    <table id="posted"><tr><th>where</th><th>text</th></tr></table>
    <p class="hint">Pointers and summaries, exactly as the bot sent them to chat.postMessage. Names and addresses never appear here.</p>
  </div>
  <div class="card"><h2>Bot status (live)</h2><iframe id="frame" src="about:blank"></iframe></div>
</div>
</main>
<script>
let S=null;
async function refresh(){
  S=await (await fetch('/ui/state')).json();
  document.getElementById('title').textContent='Fake Slack — '+S.team.name+' ('+S.team.id+')';
  const bl=document.getElementById('botlink'); bl.href=S.bot_stats_url.replace(/\\/stats$/,'/');
  const fr=document.getElementById('frame'); const want=bl.href; if(fr.dataset.src!==want){fr.src=want;fr.dataset.src=want;}
  const us=document.getElementById('user'), cs=document.getElementById('channel');
  if(!us.options.length){
    S.users.filter(u=>!u.bot).forEach(u=>{const o=document.createElement('option');o.value=u.id;
      o.textContent=u.name+(u.email?'':'  · no email')+(u.deleted?'  · deactivated':'')+(u.email&&!/\\./.test(u.email.split('@')[0])?'':'')+(u.email&&/guest|former/.test(u.email)?'  · not on roster':'');us.appendChild(o);});
    S.channels.forEach(c=>{const o=document.createElement('option');o.value=c.id;o.textContent='#'+c.name+(c.private?' (private)':'');cs.appendChild(o);});
  }
  document.getElementById('calls').textContent=S.api_calls+' Web API calls served';
  const t=document.getElementById('log'); while(t.rows.length>1)t.deleteRow(1);
  S.log.forEach(e=>{const r=t.insertRow();[e.at,e.kind,e.who,e.where].forEach(v=>r.insertCell().textContent=v);
    const c=r.insertCell();c.textContent=e.status;c.className=e.status===200?'s200':'bad';r.insertCell().textContent=e.ms;r.insertCell().textContent=e.note||'';});
  const pt=document.getElementById('posted'); while(pt.rows.length>1)pt.deleteRow(1);
  (S.posted||[]).slice().reverse().forEach(p=>{const r=pt.insertRow();r.insertCell().textContent='#'+p.channel+(p.thread_ts?' (thread)':'');
    const c=r.insertCell();c.textContent=p.text;c.style.whiteSpace='pre-wrap';});
}
async function qa(a){
  const user=document.getElementById('user').value, text=document.getElementById('qatext').value;
  const r=await (await fetch('/ui/'+a,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user,text,channel:S.qa_channel})})).json();
  if(r.error) alert(r.error);
  document.getElementById('qatext').value='';
  refresh();
}
async function act(a,opt={}){
  const user=document.getElementById('user').value, channel=document.getElementById('channel').value, text=document.getElementById('text').value;
  const body={user,channel,text,...opt};
  if(opt.thread){ body.thread_ts=(S.last_message_ts||{})[channel]||''; }
  const r=await (await fetch('/ui/'+a,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
  if(r.error) alert(r.error);
  document.getElementById('text').value='';
  refresh();
}
async function busy(n){ await fetch('/ui/busy-day',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({n})}); refresh(); }
refresh(); setInterval(refresh,2000);
</script>
"""


def main(argv: list[str] | None = None) -> int:
    import argparse
    import signal

    parser = argparse.ArgumentParser(description="Run the fake Slack server for the demo.")
    parser.add_argument("--roster", default="fixtures/roster.csv")
    parser.add_argument("--port", type=int, default=3001)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--bot-url", default="http://127.0.0.1:3000/slack/events")
    parser.add_argument("--signing-secret", required=True)
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args(argv)

    ws = demo_workspace(Path(args.roster))
    server = FakeSlackHTTPServer(
        ws, signing_secret=args.signing_secret, bot_events_url=args.bot_url,
        host=args.host, port=args.port, seed=args.seed,
    )
    print(f"fake Slack: {server.url}   (Web API at {server.api_base_url})")
    print(f"delivering events to {args.bot_url}")
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
