"""Slack participation capture.

The Director of Programs defined Slack participation as "sending messages,
reacting to messages, etc". This package records those acts as they happen.

Why a bot rather than an export or a scheduled pull: Slack's free plan hides
messages after 90 days and deletes them after a year, and the workspace CU is
waiting on may well start on the free plan. A bot that writes each event to
Postgres on arrival owns a permanent copy from the first day, so the
participation record cannot evaporate mid-fellowship. The cost is that a bot
has to be running — see docs/setup/slack-bot.md for who runs it after the
contract ends, because that is the actual risk.

Layout:

* ``events``   — turn a Slack event payload into an observation. Pure; no I/O.
* ``fake``     — an in-memory workspace and a duck-typed WebClient, shared by
                 the tests and the demo server so both exercise the same model.
* ``users``    — the slack_user_id → email cache, via ``users.info``.
* ``store``    — write observations, resolve identity, report counts.
* ``bot``      — the Bolt app (HTTP via FastAPI, or Socket Mode).
* ``backfill`` — walk ``conversations.history`` for what the bot missed.
* ``signing``  — sign a request the way Slack does, for the fake and the tests.
"""
