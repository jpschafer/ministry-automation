#!/bin/zsh
# Interactive Facebook user token renewal. Opened in Terminal by check.sh, or
# double-click / run it by hand. Opens Graph API Explorer, exchanges the token you
# paste for a ~60 day one, saves it to Keychain and copies it to the clipboard.

set -uo pipefail
DIR=${0:A:h}
source "$DIR/lib.sh"

finish() {
  print
  read -s -k1 "?Press any key to close."
  print
  exit $1
}

print "Renewing the Facebook user token for Sexton.\n"

app_id=$(kc_get app_id)
if [[ -z $app_id ]]; then
  read "app_id?App ID (developers.facebook.com > your app > App settings > Basic): "
  kc_set app_id "$app_id"
fi
app_secret=$(kc_get app_secret)
if [[ -z $app_secret ]]; then
  read -s "app_secret?App secret (hidden): "
  print
  kc_set app_secret "$app_secret"
fi

print "Opening Graph API Explorer. In it:"
print "  1. Pick your app and choose User Token"
print "  2. Add permissions: ${SCOPES//,/, }"
print "  3. Click Generate Access Token, approve both Pages, and copy the token\n"
/usr/bin/open "$EXPLORER_URL"
read -s "short?Paste the short-lived token (hidden): "
print "\n"

if ! body=$(graph_get oauth/access_token grant_type=fb_exchange_token \
    "client_id=$app_id" "client_secret=$app_secret" "fb_exchange_token=$short"); then
  print "Network error talking to Facebook."
  finish 1
fi
token=$(print -r -- "$body" | json access_token)
if [[ -z $token ]]; then
  print -r -- "Exchange failed: $(print -r -- "$body" | json error message)"
  print "Code 190: the short-lived token expired or was pasted wrong. Run this again."
  print "Code 101: wrong App ID/secret. Clear them and run again:"
  print "  security delete-generic-password -s $KC_SERVICE -a app_id"
  print "  security delete-generic-password -s $KC_SERVICE -a app_secret"
  finish 1
fi

info=$(graph_get debug_token "input_token=$token" "access_token=$app_id|$app_secret")
scopes=$(print -r -- "$info" | json data scopes)
expires=$(print -r -- "$info" | json data expires_at)
missing=()
for s in ${(s:,:)SCOPES}; do
  [[ ,$scopes, == *,$s,* ]] || missing+=$s
done

kc_set user_token "$token"
print -rn -- "$token" | pbcopy
print "New user token saved to Keychain and copied to the clipboard."
if (( ${expires:-0} > 0 )); then
  print "It expires $(date -r $expires +%F); you'll be prompted again $RENEW_DAYS days before."
fi

print "\nNext: Unraid > Docker > sexton > Edit > \"FB user token\" > paste > Apply."

print "\nPage tokens (only needed if a Page stops working; they don't expire):"
graph_get me/accounts fields=id,name,access_token "access_token=$token" | /usr/bin/python3 -c '
import json, sys
for p in (json.load(sys.stdin).get("data") or []):
    print("  %s (page id %s): %s" % (p.get("name"), p.get("id"), p.get("access_token")))
'

if (( ${#missing} )); then
  print "\nWARNING: the token is missing permissions: ${(j:, :)missing}"
  print "Profile or Page streaming may fail. Generate again with all permissions ticked."
  finish 1
fi
finish 0
