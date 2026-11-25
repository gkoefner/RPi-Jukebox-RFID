# Gordibox fork of Phoniebox

A fork of [MiczFlor/RPi-Jukebox-RFID](https://github.com/MiczFlor/RPi-Jukebox-RFID) **v2.8.0** for one concrete box: Raspberry Pi Zero 2 W (426 MB RAM), Innomaker TAS5713 amp HAT (12-20 V), RC522 reader, arcade buttons and a rotary dial, Raspbian 11, Mopidy 3.4.2 / MPD.

Goal: make the box feel instant, keep the old card workflow (a card = a file in `shared/shortcuts/` that names a folder in `shared/audiofolders/`), and make every change reversible. Everything not listed here is unchanged upstream code. All numbers below were measured once on one Pi Zero 2 W, not in a controlled benchmark.

The branch `gordibox` is upstream `v2.8.0` plus three small commits to upstream files and one commit adding [`gordibox/`](gordibox/) and this file, so merging a newer upstream stays easy. Upstream `v2.9.0` has not been merged yet (the box runs 2.8.0; see "Upgrade" below).

## What changed and why it helps

| Change | Where | Benefit (measured on the box) |
|---|---|---|
| **Resident player service**: a small Python program (standard library only, about 13 MB RAM) that starts a card with one batched MPD command instead of the shell chain (`rfid_trigger_play.sh` -> php -> `playout_controls.sh` -> several `nc`/`mpc` calls) | `gordibox/service/` | Card swipe to "playing": **about 1150 ms -> 100-170 ms** (warm). First swipe after a reboot: **about 4000 ms -> 197 ms**. Audiobook resume position kept safely across power cuts. 8 unit tests with a fake MPD. |
| **Warm-up at boot**: plays one second muted and loads every card's folder once, then confirms MPD is stopped again | service | Removes the slow "first play after boot" (pipeline start and library lookup). Volume is restored only after MPD is confirmed stopped. |
| **RFID daemon asks the service first**, falls back to the original script on anything but HTTP 200 | `scripts/daemon_rfid_reader.py` (commit) | Unknown cards, command cards, Spotify cards and a crashed service still behave exactly as upstream. |
| **Buttons next / previous / pause ask the service first** | `components/gpio_control/function_calls.py` (commit) | Button action **250-335 ms -> 31-59 ms**. Volume and shutdown deliberately stay on the original scripts. |
| **Optional library scan at boot** (`settings/ScanOnBoot`, default OFF) | `scripts/startup-scripts.sh` (commit) | **About 14 s faster boot**. Files are indexed by the auto-scan timer instead. |
| **Auto-scan timer**: scans Mopidy's library a minute after new audio files appear | `gordibox/systemd/gordibox-autoscan.*` | Upload a folder, it plays without a manual scan or reboot. |
| **Mopidy wedge watchdog**: restarts Mopidy (at most once per 40 s) on `GStreamer error: Resource not found` | `gordibox/systemd/gordibox-mopidy-heal.*` | After one failed track, every later track used to stay silent until a power cycle. Tested: wedge, automatic restart, next swipe plays. |
| **Hardware volume** through the amp's own control (`Volume_Manager=amixer`, Mopidy mixer fixed at 100), cap `Max_Volume_Limit`, floor `Min_Volume_Limit` | settings (no code) | Volume is real amp gain: the cap is a hard limit, steps are 1 %. Useful range on this amp is about 30-60 %. |
| **System slimming**: cups, ModemManager, bluetooth, triggerhappy, man-db timer disabled, `vm.swappiness=10`, journald capped, boot splash removed | system settings, no code in this repo | Swap in use **88 MB -> about 1 MB**, used RAM about -10 MB, boot to running about 24 s faster (userspace 77.6 s -> 53.6 s in the A/B run). Less swapping is what made the first action after idle slow. |

Things we tried and **reverted** because measurement said no: pre-warming the file cache at boot (it made the first swipe slower).

## More detail

- [`gordibox/docs/resident-service.md`](gordibox/docs/resident-service.md): design of the service and its fallbacks.
- [`gordibox/docs/recording-and-stats.md`](gordibox/docs/recording-and-stats.md): play statistics and phone recording design.

## Install on a box (what we did)

```sh
# 1. the service (paths assume user pi and Phoniebox in /home/pi/RPi-Jukebox-RFID)
scp gordibox/service/gordibox_player.py pi@box:/home/pi/
scp gordibox/systemd/gordibox-player.service pi@box:/tmp/
ssh pi@box 'sudo install -m644 /tmp/gordibox-player.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now gordibox-player'
# 2. the two Phoniebox hooks: copy scripts/daemon_rfid_reader.py and components/gpio_control/function_calls.py
#    from this branch (or: git fetch --tags https://github.com/MiczFlor/RPi-Jukebox-RFID.git, then git diff v2.8.0..gordibox -- scripts components)
ssh pi@box 'sudo systemctl restart phoniebox-rfid-reader phoniebox-gpio-control'
# 3. optional watchdog and auto-scan: copy gordibox/systemd/gordibox-*.sh to /usr/local/bin (chmod +x),
#    the .service/.timer files to /etc/systemd/system, then:
#    sudo systemctl daemon-reload && sudo systemctl enable --now gordibox-mopidy-heal gordibox-autoscan.timer
```

Check: `curl localhost:8765/health`, `curl localhost:8765/swipe/<cardid>`, `journalctl -u gordibox-player`.
Rollback of the hooks: restore the original files; the service can simply be stopped, the old chain takes over.

## Hardware assumptions (change for another box)

- ALSA card number for the amp: environment `GORDIBOX_ALSA_CARD` (default `1`, see `aplay -l`); control name from Phoniebox's `settings/Audio_iFace_Name` (default `Master`).
- Service port `8765` (`--port`), localhost only. Phoniebox lives in `/home/pi/RPi-Jukebox-RFID` (`--root`). Paths in the systemd units assume user `pi`.

## Upgrade to upstream v2.9.0

Not done yet. The three commits touch `startup-scripts.sh`, `daemon_rfid_reader.py` and `function_calls.py`. Plan: `git merge upstream/master` on a branch, resolve, then test on a spare SD card before touching the box.

## Not tested / known gaps

- LED wiring (button LEDs off, blink on shutdown, pulse on pause) is a backlog item, not implemented.
- The service holds one MPD connection and assumes a single user on a trusted LAN (it listens on localhost only).
