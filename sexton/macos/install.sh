#!/bin/zsh
# Installs the daily token check as a launchd agent. `install.sh --uninstall` removes it.
# The agent runs check.sh from this folder, so keep the repo where it is.

set -euo pipefail
DIR=${0:A:h}
LABEL=com.jpschafer.sexton.fb-token
PLIST=$HOME/Library/LaunchAgents/$LABEL.plist
LOG=$HOME/Library/Logs/sexton-fb-token.log

launchctl bootout gui/$UID/$LABEL 2>/dev/null || true

if [[ ${1:-} == --uninstall ]]; then
  rm -f $PLIST
  print "Removed $LABEL."
  exit 0
fi

chmod +x $DIR/check.sh $DIR/renew.command
mkdir -p $HOME/Library/LaunchAgents $HOME/Library/Logs
sed -e "s|__CHECK__|$DIR/check.sh|" -e "s|__LOG__|$LOG|" $DIR/fb-token.plist > $PLIST
plutil -lint -s $PLIST
launchctl bootstrap gui/$UID $PLIST

print "Installed $LABEL: checks daily at 9:00 and at login."
print "Log: $LOG"
