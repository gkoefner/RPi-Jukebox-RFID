#!/bin/bash
# Restart Mopidy when it wedges ("GStreamer error: Resource not found" after a failed track).
# At most once per 40 s. The amp volume lives in the amp, so nothing to restore.
last=0
journalctl -fu mopidy -n 0 --no-pager | while read -r line; do
  case "$line" in
    *"GStreamer error: Resource not found"*)
      now=$(date +%s)
      if [ $((now - last)) -ge 40 ]; then
        last=$now
        logger -t gordibox-heal "mopidy wedged, restarting"
        systemctl restart mopidy
      fi;;
  esac
done
