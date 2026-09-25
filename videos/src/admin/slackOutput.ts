// Real bot replies from `cufa slack cmd` against the running demo (log lines removed,
// Slack user ids shown as names).
export const SLACK: Record<string, { cmd: string; out: string }> = {
 "report": {
  "cmd": "/report",
  "out": "*Report — cohort demo* (as of Sep 25 16:51 UTC)\nSessions held: 11 · active fellows: 20 · overall attendance: 21%\nCheck-ins: 99 (attended 49, needs review 47, unknown address 1)\nExit tickets: 82 from 19 fellows · shoutouts given: 71\nHighest attention index: Sabra Stonebrook 80, Rosalind Ravensmoor 71, Oriel Oakhaven 67, Marisol Mossgate 64, Nevin Northcote 63, Quill Quarrington 61\nLast data: slack_message never · slack_sync Sep 25 16:50 · part_a Sep 25 16:50 · part_b Sep 25 16:50\n_✓ = someone has reached out. Full detail: the staff dashboard, or `cufa report`._"
 },
 "fellow": {
  "cmd": "/fellow Sabra",
  "out": "*Sabra Stonebrook* · CU-2618 · active\nEmail: sabra.stonebrook@example.invalid\nSlack: @Sabra Stonebrook (primary_email)\nAttendance: 2/11 (+1 under review) · Exit tickets: 0/11 · Messages: 0 (7d: 0; cohort mean 0.0)\nAttention index: *80* — missing sessions\nReached out: no\nBadges: ✅1\nFunnel: ✅ accepted → ✅ Slack → ⬜ message → ✅ check-in → ⬜ completed"
 },
 "attendance": {
  "cmd": "/attendance next",
  "out": "*Session summary — Session 1 — What a civic problem is* (Sun Sep 27, 7:00 PM EDT)\nChecked in: *5/20* · needs review: 5\nExit tickets: *8/20*\nNo check-in: Kestrel Kelloway, Lorne Larkspur, Marisol Mossgate, Marisol Thornbury, Nevin Northcote, Oriel Oakhaven, Peregrine Pennyfeather, Quill Quarrington, Rosalind Ravensmoor, Sabra Stonebrook\n5 check-ins waiting for a human decision — `cufa review`."
 },
 "leaderboard": {
  "cmd": "/leaderboard checkins",
  "out": "*Most check-ins* (staff view — not shown to fellows)\n1. Ellery Everstead — 4  (4 badges)\n2. Halcyon Havershill — 4  (5 badges)\n3. Bexley Brambleton — 3  (4 badges)\n4. Corvin Cinderwick — 3  (4 badges)\n5. Delphine Dunmore — 3  (5 badges)"
 },
 "asg_create": {
  "cmd": "/assignment create \"Solvathon pitch\" 2026-10-20 18:00 solvathon",
  "out": "Created *Solvathon pitch* (Solvathon), due 2026-10-20 18:00 America/New_York. Reminders go out 24h, 1h and 10m before.\n`e3836eb4-631e-40e5-b094-829b65adeb4f`\nAdd the link with `/assignment link <title> <url>`."
 },
 "asg_list": {
  "cmd": "/assignment list",
  "out": "• *Solvathon pitch* (Solvathon) due Tue Oct 20, 6:00 PM EDT — 0 submitted, 0 scored — _no link_\n   `e3836eb4-631e-40e5-b094-829b65adeb4f`"
 },
 "score": {
  "cmd": "/score \"Solvathon pitch\" Sabra 8 clear problem statement",
  "out": "Recorded 8 for *Sabra Stonebrook* on *Solvathon pitch*."
 },
 "zoom": {
  "cmd": "/zoom next https://zoom.example.invalid/j/123456",
  "out": "Zoom link set on *Session 1 — What a civic problem is* (Sun Sep 27, 7:00 PM EDT). It will be in every reminder."
 },
 "outreach": {
  "cmd": "/outreach Sabra called her Tuesday",
  "out": "Marked: someone has reached out to *Sabra Stonebrook*. Note: called her Tuesday"
 },
 "alias": {
  "cmd": "/alias Sabra sabra.s@school.example.invalid school",
  "out": "*Sabra Stonebrook* now also resolves from sabra.s@school.example.invalid. Every check-in and message from that address is attributed to them from now on, past ones included."
 },
 "link": {
  "cmd": "/link @Guest Speaker Sabra",
  "out": "@Guest Speaker is now *Sabra Stonebrook* (CU-2618)."
 },
 "alerts": {
  "cmd": "/alerts",
  "out": "*Joined but not on the roster:*\n• @Guest Speaker Guest Speaker — guest.speaker@example.invalid (since Sep 25)\n• @No Email On Profile No Email On Profile (since Sep 25)\n`/link <@user> <fellow>` · `/alerts resolve <@user> staff|ignored`"
 },
 "alerts_resolve": {
  "cmd": "/alerts resolve @No Email On Profile ignored",
  "out": "Resolved: @No Email On Profile marked as ignored."
 },
 "digest": {
  "cmd": "/digest",
  "out": "Posted the weekly digest to the staff channel."
 },
 "sync": {
  "cmd": "/sync",
  "out": "Synced: users=25 new_users=0 alerts=0 channels=6 messages_read=0 messages_written=0"
 },
 "admindash": {
  "cmd": "/admin-dashboard",
  "out": "*Staff dashboard:* http://127.0.0.1:8000/dashboard\nSign in with Google if it is configured here, or with the site password. The password is not sent over Slack — ask whoever runs this install for it."
 },
 "refused": {
  "cmd": "/report",
  "out": "⛔ `/report` is a staff command. Ask a workspace admin, or have your address added to CUFA_SLACK_ADMINS."
 },
 "checkin": {
  "cmd": "/checkin",
  "out": "Done — a staff member has been pinged and will reach out."
 }
};
