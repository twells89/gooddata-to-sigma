#!/usr/bin/env bash
# get-token.sh — resolve GoodData Cloud / .CN credentials for discover.py.
#
# GoodData auth is a static bearer API token (no OAuth exchange), so this just
# locates and validates the credentials and prints export lines to eval.
#
#   eval "$(./get-token.sh)"
#
# Looks for (in order): env vars already set, then ~/.gooddata_env, then
# ./.gooddata_env. Required: GOODDATA_HOST (e.g. https://acme.cloud.gooddata.com)
# and GOODDATA_TOKEN. Optional: GOODDATA_WORKSPACE.
set -euo pipefail

for f in ~/.gooddata_env ./.gooddata_env; do
  if [ -z "${GOODDATA_HOST:-}" ] && [ -f "$f" ]; then
    # shellcheck disable=SC1090
    set -a; source "$f"; set +a
  fi
done

: "${GOODDATA_HOST:?set GOODDATA_HOST (e.g. https://acme.cloud.gooddata.com)}"
: "${GOODDATA_TOKEN:?set GOODDATA_TOKEN (a GoodData API token)}"

echo "export GOODDATA_HOST='${GOODDATA_HOST%/}'"
echo "export GOODDATA_TOKEN='${GOODDATA_TOKEN}'"
[ -n "${GOODDATA_WORKSPACE:-}" ] && echo "export GOODDATA_WORKSPACE='${GOODDATA_WORKSPACE}'"
