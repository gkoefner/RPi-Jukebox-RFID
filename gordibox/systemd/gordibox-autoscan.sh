#!/bin/bash
# Runs every minute (gordibox-autoscan.timer, user pi). Scans the Mopidy local library when the audio file
# set changed AND was unchanged since the previous run (= upload finished) AND nothing is playing.
# Same scan as web UI "Scan Music Library" (playout_controls.sh -c=scan). State: ~/.gordibox-scan-{seen,done}
D=/home/pi/RPi-Jukebox-RFID/shared/audiofolders
sig=$(find "$D" -type f \( -iname '*.mp3' -o -iname '*.m4a' -o -iname '*.m4b' -o -iname '*.flac' -o -iname '*.ogg' -o -iname '*.opus' -o -iname '*.wav' -o -iname '*.m3u' \) -printf '%p %s %T@\n' | sort | md5sum | cut -d' ' -f1)
seen=$(cat ~/.gordibox-scan-seen 2>/dev/null); done_=$(cat ~/.gordibox-scan-done 2>/dev/null)
echo "$sig" > ~/.gordibox-scan-seen
[ "$sig" = "$done_" ] && exit 0            # nothing new
[ "$sig" = "$seen" ] || exit 0             # still changing, wait one more minute
mpc status 2>/dev/null | grep -q '^\[playing\]' && exit 0   # scan stops mopidy: never mid-song
logger -t gordibox-autoscan "library changed, scanning"
/home/pi/RPi-Jukebox-RFID/scripts/playout_controls.sh -c=scan && echo "$sig" > ~/.gordibox-scan-done
logger -t gordibox-autoscan "scan done"
