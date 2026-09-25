#!/usr/bin/env bash
# Post one Slack message for a failed live run: which job, which Opik
# versions, the run link and the failing tests.
#
# Same shape as comet-ml/opik's .github/scripts/send-slack-message.sh: a curl to
# an incoming webhook, and a notice instead of a failure when the webhook is
# not configured, so a fork or a fresh repo never goes red over Slack.
#
# Env: SLACK_WEBHOOK_URL, LOCAL_RESULT, PROD_RESULT, OPIK_VERSION, CLOUD_VERSION,
# LOCAL_FAILED, PROD_FAILED (newline-separated test ids), plus the GITHUB_*
# variables every step has.
set -euo pipefail

if [ -z "${SLACK_WEBHOOK_URL:-}" ]; then
  echo "::notice::SLACK_WEBHOOK_URL not configured - Slack notification skipped"
  exit 0
fi

MAX_NAMED=20
run_url="${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}"

# The failing tests of one job, capped, as mrkdwn lines.
list_failed() {
  local tests="$1" count
  count=$(printf '%s\n' "$tests" | sed '/^$/d' | wc -l | tr -d ' ')
  printf '%s\n' "$tests" | sed '/^$/d' | head -n "$MAX_NAMED" | sed 's/^/• `/; s/$/`/'
  if [ "$count" -gt "$MAX_NAMED" ]; then
    echo "• and $((count - MAX_NAMED)) more"
  fi
}

# What a job's result says, in the message. "cancelled" on a nightly is a job
# that hit its time limit, which is as much a failure as a red test.
verdict() {
  case "$1" in
    failure) echo "failed" ;;
    cancelled) echo "timed out or was cancelled" ;;
    *) echo "" ;;
  esac
}

body=""
local_verdict=$(verdict "${LOCAL_RESULT:-}")
if [ -n "$local_verdict" ]; then
  body+="*live-local* ${local_verdict} against Opik ${OPIK_VERSION:-unknown}"$'\n'
  body+="$(list_failed "${LOCAL_FAILED:-}")"$'\n'
fi
prod_verdict=$(verdict "${PROD_RESULT:-}")
if [ -n "$prod_verdict" ]; then
  body+="*live-prod* ${prod_verdict} against Opik cloud ${CLOUD_VERSION:-unknown}"$'\n'
  body+="$(list_failed "${PROD_FAILED:-}")"$'\n'
fi

payload=$(jq -n \
  --arg title "opik-mcp live tests failed on ${GITHUB_REF_NAME}" \
  --arg body "$body" \
  --arg url "$run_url" \
  '{attachments: [{color: "danger", blocks: [
     {type: "header", text: {type: "plain_text", text: $title}},
     {type: "section", text: {type: "mrkdwn", text: ($body | .[0:2900])}},
     {type: "actions", elements: [{type: "button", text: {type: "plain_text", text: "View run"}, url: $url}]}
   ]}]}')

status=$(curl -s -o /dev/null -w "%{http_code}" -X POST -H 'Content-type: application/json' \
  --data "$payload" "$SLACK_WEBHOOK_URL")
if [ "$status" != "200" ]; then
  echo "::warning::Slack answered HTTP $status"
  exit 1
fi
echo "Slack notified"
