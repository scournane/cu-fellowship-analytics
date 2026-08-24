"""Allow ``python -m cufa`` as well as the installed ``cufa`` script."""

from .cli import main

raise SystemExit(main())
