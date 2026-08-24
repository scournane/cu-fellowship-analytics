# When a fellow asks to be checked in with

**Who this is for:** anyone at Civics Unplugged who runs the fellowship. You do
not need to be an engineer to read this, and there is nothing in it you need a
developer to explain.

---

## What the box is

The last thing on the end-of-session form is one optional checkbox:

> ☐ I'd like someone to check in with me

That is the whole thing. There is no text box next to it, no "tell us why", and
no list of reasons to pick from. A fellow ticks it or does not.

It is deliberately the **last** field on the form. A sensitive question placed
early makes people abandon the whole form — not just that question — so it comes
after four ordinary ones, when they are already most of the way through.

The form says, in plain language, that ticking it costs them nothing. That
sentence is true, and this document is the explanation of how it is kept true.

---

## What happens when someone ticks it

1. **An email goes out immediately**, to the person named in the routing
   configuration. Not that night, not on a weekly run — the moment the response
   is collected.
2. **A row appears on the Help requests screen** in the console, marked open.
3. **Nothing else happens.** No count changes. No score moves. No report
   mentions it.

### Who is emailed

Whoever is named in `config/help_routing.json`. Today that is the **Director of
Programs**, because when this was built CU had no dedicated fellow-support
responder role, and the Director of Programs is a real person who exists.

To change who is emailed, change that file. It is a short file and the names are
the only thing in it that matters:

```json
"recipients": [
  {"name": "Director of Programs", "email": "adiah@civicsunplugged.org"}
]
```

### What the email says

The fellow's **name** and the **session**. That is all.

It does not include their takeaway. It does not include their confidence rating.
It does not include who they thanked. It does not include anything else they
wrote on that form or any other one.

This is deliberate, and it is not a technical limitation — the system has all of
that and withholds it. Someone who has raised their hand should get a
conversation, not a file. If they want to tell you what is going on, that is
theirs to tell.

The email says so out loud, so that whoever receives it knows there is more and
knows they were not given it.

---

## The rule that matters most

**Ticking the box never lowers anything.**

Not attendance. Not participation. Not any count, rate, score, or summary,
anywhere, ever. A fellow who ticks it every week and a fellow who never ticks it
have identical numbers everywhere in this system.

This is not a policy someone remembered to follow. It is built in three ways:

* The checkbox is **not stored on the response record at all.** It goes into its
  own separate table, so no report that reads responses can accidentally pick it
  up.
* **No report, export or summary reads that table.** There is a test that runs
  every report and export the system has, against data that includes a help
  request, and fails if anything from it comes out.
* There is a **second test** that watches the actual database queries every
  count and rate runs, and fails if any of them so much as mentions the table.

The reason for all three is simple. If a fellow suspects that asking for help
costs them something, they stop asking — and this box is the only way the
programme finds out that someone is struggling before somebody notices in a
lesson.

---

## No recipient means no box

If `config/help_routing.json` names nobody, **the checkbox is left off the form
entirely.**

Not greyed out, not collected-and-ignored. It is not on the form. The console
says so on the session screen, and the provisioning history records it.

This is on purpose. A system that invites a young person to ask for help and
routes it nowhere is worse than one that never asks: the request gets recorded,
nobody is told, and everyone involved assumes it was handled. Better to not ask
than to ask and drop it.

So: **if the box should be there, somebody has to be named.**

---

## Who can see the requests

Fewer people than can see everything else.

The Help requests screen has its own access list, separate from the general
console sign-in list. Being able to use the console does **not** get you into
that screen.

* If `CUFA_HELP_ALLOWLIST` is set in the environment file, those addresses.
* If it is not set, whoever is named in `config/help_routing.json` — the people
  already receiving the emails.

Below that, the database itself refuses access to anything that is not the
application. There is no dashboard, no shared query, and no export that reaches
it.

This is deliberately stricter than the rest of the system. CU has said the
attendance data should be visible to every full-time team member. That question
is still open even for attendance; it is nowhere near settled for a record that
a young person asked to be contacted, and the safe default while it is open is
"fewer people".

---

## Using the screen

Open **Help requests** in the console. Open ones are first.

Each request shows the fellow's name, which session it came from, and when.
Two things you can do:

* **"I'm picking this up"** — records that you have it, with your name and the
  time, so two people do not both reach out and nobody assumes the other did.
* **Close** — records that it has been dealt with.

Both take an optional note. The note is **for the next person to read**, written
by you. Nothing the fellow typed is copied into it.

You can do the same from a terminal:

```
cufa help-requests list
cufa help-requests ack   --id <id> --by you@civicsunplugged.org --note "Emailed them."
cufa help-requests close --id <id> --by you@civicsunplugged.org
```

---

## What is not written down anywhere

Nothing about a help request is written to the system's log files — not the
name, not the address, not the session, at any level of detail. Log files get
copied around, pasted into chat, and attached to bug reports. This one category
stays out of them.

---

## The open question: how long these are kept

**CU has not decided a retention period, and this system has not invented one.**

That means: right now, a help request stays in the database until somebody
deletes it.

This is flagged rather than fixed on purpose. Whatever number gets written down
becomes the policy, and an assumed retention period is exactly the kind of thing
nobody revisits. It is also very unlikely that the right answer here is the same
as the right answer for a timestamp.

Questions someone at CU needs to answer:

* How long is an **open** request kept? A **closed** one?
* When a request is closed, is it deleted, or reduced to a count with the name
  removed?
* When a fellow leaves the programme, are their requests deleted?
* Does a fellow have any way to ask for theirs to be removed?

Until those are answered, the marker in the code says `TODO(retention)` and this
section says the same thing.

---

## Things this system deliberately does not do

* **No AI ever reads a help request.** Not to summarise it, not to sort it, not
  to rank it by urgency. There is a test asserting the AI part of the system
  never receives any of it.
* **No at-risk flag, no struggling-fellow label, no risk score.** The box says
  what it says: this person would like someone to check in. Turning that into a
  category would be a judgment about a young person made by a program, and it is
  not one this system is entitled to make.
* **No automatic reply to the fellow.** Whether they hear back, and what they
  hear, is a person's decision.

---

## If something looks wrong

* **A request came in but no email arrived.** The request is still recorded and
  still on the console screen — the row is written before the email is
  attempted, so a mail failure never loses it. Check the recipient address in
  `config/help_routing.json`.
* **The checkbox is missing from a form.** Check whether a recipient is
  configured. The session screen in the console says whether it was included and
  why not.
* **Someone can see the screen who should not.** Set `CUFA_HELP_ALLOWLIST` in
  the environment file to exactly the addresses that should have it, and restart
  the console.

---

## See also

* [`docs/setup/part-b-form.md`](setup/part-b-form.md) — the end-of-session form
  in full, and what a teacher prepares each week.
* [`docs/decisions.md`](decisions.md) — ADR-025 and ADR-026, which record why the
  no-recipient rule and the exclusion from every signal were chosen, and what was
  rejected.
