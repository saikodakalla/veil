#!/bin/bash
cd -- "$(dirname -- "$0")"
/bin/bash ./start.sh "$@"
VEIL_EXIT=$?
if [ "$VEIL_EXIT" -ne 0 ]; then
  printf '\nVeil stopped before it was ready. Read the message above, then press Return to close.\n'
  read -r
fi
