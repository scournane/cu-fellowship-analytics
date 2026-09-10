"""The Slack bot.

Everything the bot does is a plain function over a database connection and a
``SlackClient``. The Bolt adapter in :mod:`cufa.slack.app` is the only thing
that knows about sockets, and it is imported only by ``cufa slack serve``, so
the rest of this package — and every test — runs with the in-memory fake.
"""
