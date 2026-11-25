# Play statistics and phone recording (design notes)

## 1. Play statistics
**UI idea (prototyped with mock data, not in this repo):** new "Stats" tab. Range 7 / 30 days / all time; tiles for plays, hours, cards used; plays-per-day bars; cards ranked by plays with a bar, "last played x ago" and hours listened; a list of cards not played in the period.

**Data today (no new logging needed):** the service already appends one line per swipe to `~/.gordibox/latency.jsonl` (`ts`, `card`, `folder`). New endpoint `GET /stats` (service, tested live) returns `{card: {folder, plays, last_ts}}`. That answers "which cards, how often, how recently".
Caveats: test swipes count too. Swipes only: a card that plays for 2 minutes and one that plays for 2 hours count the same. Cards started by the old script fallback or from the phone are not counted.

**For listening time (needs a small addition):** the service's watcher already polls MPD every 2 s. Log a line when playback starts and stops (`ts`, card, seconds) into `~/.gordibox/plays.jsonl`; cap the file (rotate at ~1 MB). Gives hours listened, skip counts, and "the box played 3 h on Tuesday". Phase 2 moves this into SQLite with the other state.

## 2. Recording on the phone and playing it from a card
**Works, with one catch.** Flow: phone browser records, uploads to the box, box converts, auto-scan picks it up, it is a normal track on a card.

| Step | How | Notes |
|---|---|---|
| Record | `MediaRecorder` in the page (prototype has it: level meter, timer, preview, name) | Android/Chrome gives webm/opus, iPhone Safari gives mp4/aac. Both fine. |
| **The catch** | browsers allow the microphone only on **https or localhost** | The box serves plain http on the LAN, so the mic is blocked there. Options below. |
| Fallback without https | `<input type="file" accept="audio/*" capture>` opens the phone's own recorder app | Works on http, any phone, no code. The prototype shows this button when the mic is unavailable. |
| Upload | multipart POST to the service, same path as song upload | Small: 1 min of voice is about 0.1-0.5 MB. |
| Convert | box already has `ffmpeg`: `ffmpeg -i in -ac 1 -b:a 64k out.mp3` | Mono 64 kbps mp3 is plenty for voice (about 0.5 MB/min). A minute of audio converts in a few seconds on the Zero 2 W; do it one at a time, nice 19. |
| Play | file lands in the card's folder as `NN - <name>.mp3`; auto-scan (60 s timer) indexes it | Mopidy plays mp3 for sure. webm/opus and m4a decoders also exist on the box (checked), but converting to mp3 keeps one format everywhere and the scanner happy. |

**Format answer:** mp3 is right (works everywhere on this box). Opus/ogg would be smaller but gives nothing here; keep mp3 for voice at 64 kbps mono.

**Getting https for the microphone (pick one when we build it):**
1. Tailscale/`tailscale cert` or a local CA (mkcert) with the box name; one-time phone trust step. Cleanest.
2. Self-signed certificate: works, but the phone shows a warning once.
3. Skip it: use the "open phone recorder" fallback only. Zero setup, slightly less slick (record in the phone's app, then it uploads). Recommended start.

**Ideas that fall out of this:**
- Voice messages on a dedicated card (record remotely, send the file, it appears on the card).
- Record a "name card" voice intro that plays before an album.
- Duplicate: the same upload path serves a podcast-style "message of the day".
- Privacy: recordings stay on the box; add a delete button and an "recorded by" label.

**Not built yet:** the upload endpoint, the ffmpeg conversion hook (a small script run by the existing auto-scan timer, converting anything not `.mp3` in a `incoming/` folder), https. The prototype only mocks the save step.

