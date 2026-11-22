#!/bin/zsh
# Run daily by launchd. If the Facebook user token is missing, invalid, or expires
# within RENEW_DAYS, opens renew.command in Terminal. Otherwise does nothing.
# Facebook's own expiry date decides, so sleep, reboots and missed days don't matter.

set -uo pipefail
DIR=${0:A:h}
source "$DIR/lib.sh"

log() { print -r -- "$(date '+%F %T') $*" }

app_id=$(kc_get app_id)
app_secret=$(kc_get app_secret)
token=$(kc_get user_token)
reason=""

if [[ -z $app_id || -z $app_secret || -z $token ]]; then
  reason="Facebook token not set up yet"
else
  if ! body=$(graph_get debug_token "input_token=$token" "access_token=$app_id|$app_secret"); then
    log "network error; will try again tomorrow"
    exit 1
  fi
  err_code=$(print -r -- "$body" | json error code)
  valid=$(print -r -- "$body" | json data is_valid)
  expires=$(print -r -- "$body" | json data expires_at)
  if [[ -n $err_code ]]; then
    # The check itself failed, so this says nothing about the token.
    msg=$(print -r -- "$body" | json error message)
    case $err_code in
      4|17|32|613)
        log "rate limited by Facebook (code $err_code: $msg); will try again tomorrow"
        exit 1 ;;
      *)
        reason="Facebook check failed (code $err_code: $msg)" ;;
    esac
  elif [[ $valid != True ]]; then
    reason="Facebook user token is no longer valid ($(print -r -- "$body" | json data error message))"
  elif (( ${expires:-0} == 0 )); then
    log "ok: token does not expire"
    exit 0
  elif (( expires - $(date +%s) < RENEW_DAYS * 86400 )); then
    reason="Facebook user token expires $(date -r $expires +%F)"
  else
    log "ok: token expires $(date -r $expires +%F)"
    exit 0
  fi
fi

log "renewal needed: $reason"
notify "$reason. Opening renewal."
/usr/bin/open -a Terminal "$DIR/renew.command"
