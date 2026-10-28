#!/bin/sh
# This candidate bootstrap and its interpreter require independent human review.
set -eu
case "$0" in
  /*) bootstrap_path=$0 ;;
  *) bootstrap_path=$(/bin/pwd -P)/$0 ;;
esac
bootstrap_dir=$(CDPATH= cd -- "$(/usr/bin/dirname -- "$bootstrap_path")/../.." && /bin/pwd -P)
exec /usr/bin/env -i PATH=/usr/bin:/bin LANG=C LC_ALL=C /usr/bin/python3 -I -S -B -c '
import sys
sys.path.insert(0, sys.argv.pop(1))
from ai_zones.deploy import main
raise SystemExit(main())
' "$bootstrap_dir/sandbox" "$@"
