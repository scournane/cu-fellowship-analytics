#!/usr/bin/env bash
# Vendor the package next to the entrypoint, because Vercel's Python builder
# installs requirements.txt and uploads the folder -- it does not `pip install`
# the repository around it. Run from the repo root before `vercel deploy`.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../.." && pwd)"

if [ ! -d "$root/src/cufa/console/static/app" ]; then
  echo "The front-end bundle is missing. Run: python tasks.py frontend" >&2
  exit 1
fi

rm -rf "$here/api/cufa"
cp -r "$root/src/cufa" "$here/api/cufa"
find "$here/api/cufa" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
echo "vendored $(du -sh "$here/api/cufa" | cut -f1) into deploy/vercel/api/cufa"
