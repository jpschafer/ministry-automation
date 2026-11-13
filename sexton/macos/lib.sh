# Shared helpers for the macOS Facebook token scripts. Sourced, not run.
# Callers set DIR to their own directory before sourcing.

KC_SERVICE=sexton-facebook
EXPLORER_URL=https://developers.facebook.com/tools/explorer/
SCOPES=publish_video,pages_show_list,pages_read_engagement,pages_manage_posts
RENEW_DAYS=${RENEW_DAYS:-10}

# One Graph API version for the whole project: read it from the service code.
GRAPH_API_VERSION=$(sed -n 's/^GRAPH_API_VERSION = "\(.*\)"/\1/p' "$DIR/../sexton/facebook.py")
if [[ -z $GRAPH_API_VERSION ]]; then
  print -u2 "Could not read GRAPH_API_VERSION from sexton/facebook.py"
  exit 1
fi
GRAPH_URL=https://graph.facebook.com/$GRAPH_API_VERSION

# Keychain access. Values go through stdin so they never appear in `ps`.
kc_get() {
  /usr/bin/security find-generic-password -s $KC_SERVICE -a "$1" -w 2>/dev/null
}
kc_set() {
  printf 'add-generic-password -U -s %s -a %s -w %s\n' $KC_SERVICE "$1" "$2" | /usr/bin/security -i >/dev/null
}

# graph_get PATH key=value... : GET a Graph path. Parameters go through a curl
# config on stdin, again keeping tokens and the app secret out of `ps`.
graph_get() {
  local path=$1; shift
  local kv
  for kv in "$@"; do printf 'data-urlencode = "%s"\n' "$kv"; done |
    /usr/bin/curl -sS --max-time 20 -G -K - "$GRAPH_URL/$path"
}

# json KEY... : walk a JSON document on stdin; lists print comma-joined.
json() {
  /usr/bin/python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except ValueError:
    d = None
for k in sys.argv[1:]:
    d = d.get(k) if isinstance(d, dict) else None
print("" if d is None else ",".join(map(str, d)) if isinstance(d, list) else d)
' "$@"
}

notify() {
  /usr/bin/osascript -e "display notification \"$1\" with title \"Sexton\""
}
