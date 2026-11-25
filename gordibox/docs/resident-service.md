# Resident player service

## What it is
`gordibox/service/gordibox_player.py`: one small Python process (standard library only, about 13 MB RSS) that keeps a connection to Mopidy's MPD port and starts a card in ONE command list (clear, mode flags, add all tracks, play). It replaces the per-swipe shell chain (`rfid_trigger_play.sh` -> php -> `playout_controls.sh` -> several `mpc`/`nc` calls). Same file model as Phoniebox: card = `shared/shortcuts/<id>` -> folder under `shared/audiofolders/`; `folder.conf` is honoured.

HTTP API on `127.0.0.1:8765` (localhost only): `GET /swipe/<id>`, `/cmd/{next,prev,toggle}`, `/health`, `/cards`, `/state`, `/stats`. These change state on plain GET, so keep it bound to localhost.

## Numbers (measured once, on one Pi Zero 2 W)
| | Shell chain | Service |
|---|---|---|
| Warm swipe to MPD "play" | about 1150 ms | 100-170 ms |
| First swipe after boot | about 4000 ms | 197 ms (with the service's own warm-up) |
| Next / previous / pause button | 250-335 ms | 31-59 ms |

"Play" is MPD's state; audible sound follows a little later (not measured).

## Warm-up
After Mopidy is up the service plays the first card's first track for 1.2 s muted, then looks up every card's folder once. It mutes through the same volume manager as Phoniebox (amixer, or MPD volume) and restores the level only after MPD is confirmed stopped. A swipe during warm-up waits for the muted second to finish (a few seconds) and is never stopped by it. If the service is killed during the muted second the amp stays muted until the next volume change.

## How it is wired (and how to undo each step)
| Piece | Change | Undo |
|---|---|---|
| Service | copy `gordibox_player.py` to `/home/pi/`, install `gordibox/systemd/gordibox-player.service` | `sudo systemctl disable --now gordibox-player` |
| Card reader | `scripts/daemon_rfid_reader.py` asks the service first (timeout 8 s); the original `rfid_trigger_play.sh` runs only if the service does NOT answer 200 (unknown card, command card, service down) | restore the upstream file, `sudo systemctl restart phoniebox-rfid-reader` |
| Buttons | `components/gpio_control/function_calls.py`: next/prev/pause ask the service first, fall back to `playout_controls.sh` (also when the old mute file exists) | restore the upstream file, `sudo systemctl restart phoniebox-gpio-control` |

Because of the fallbacks, cards and buttons keep working if the service crashes or is removed; they are just slower again. Both patches are the commits `v2.8.0..gordibox` in this repository.

Edge case: if the service takes longer than the daemon's timeout, the original script also runs and the card may start twice. The timeout is set above the service's own worst case (6 s) to avoid that.

## What it does and does not do
Does: card -> folder -> all audio files (natural sort, recursive, dotfiles ignored) -> play; `folder.conf` SHUFFLE/LOOP/SINGLE; second swipe per `settings/Second_Swipe` (RESTART default, PAUSE, SKIPNEXT); audiobook resume (RESUME=ON folders): position saved to `~/.gordibox/state.json` (atomic write, at most every 15 s), restored 3 s earlier on the next swipe; per-swipe latency log in `~/.gordibox/latency.jsonl`; updates the old web UI's "last card" files.

Does not (yet): command cards (volume/shuffle cards fall back to the old script), card folders that contain a `spotify.txt` (fall back), card registration, volume and dial, recovery from a stuck Mopidy (see the watchdog unit), a database for playlists.

Tested with a fake MPD (8 unit tests: `python -m unittest gordibox/service/test_player.py`), and on one box by hand, through reboots and with a real card reader.

## Ideas for phase 2
Route volume and the dial through the service; move cards, playlists and resume positions into SQLite; serve a phone UI from the same process.
