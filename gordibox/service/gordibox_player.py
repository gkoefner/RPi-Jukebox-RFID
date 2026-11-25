#!/usr/bin/env python3
"""gordibox resident player service, phase 1 (same file model as Phoniebox: card -> folder).

Replaces the per-swipe shell chain (rfid_trigger_play.sh -> php -> playout_controls.sh -> mpc ...) by one process that keeps a
connection to Mopidy's MPD port and does a swipe in ONE command list. Python 3.9, standard library only.

  GET  /swipe/<cardid>   play what the card points to (same result as a swipe)
  GET  /health           json: mpd reachable, state, last swipes (latency), uptime
  GET  /cards            json: registered cards with folder, type, track count
  GET  /state            json: current MPD state
Latency of every swipe (card seen -> command sent -> MPD says "play") is appended to ~/.gordibox/latency.jsonl.
Resume positions of RESUME=ON folders (audiobooks) are kept in ~/.gordibox/state.json, written at most every 15 s while playing
and immediately on pause/stop. Phoniebox's folder.conf (RESUME/SHUFFLE/LOOP) is only READ, never written.
"""
import argparse, json, os, re, socket, subprocess, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote

AUDIO = {".mp3", ".m4a", ".m4b", ".flac", ".ogg", ".opus", ".wav"}
HOME = os.path.expanduser("~")
DEF_ROOT = os.path.join(HOME, "RPi-Jukebox-RFID")
DATA = os.path.join(HOME, ".gordibox")


def natural(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


class MPD:
    """Minimal MPD client: one persistent connection, reconnects on error, one lock (Mopidy answers one command at a time anyway)."""

    def __init__(self, host="localhost", port=6600):
        self.host, self.port, self.sock, self.f, self.lock = host, port, None, None, threading.Lock()

    def _connect(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=5)
        self.f = self.sock.makefile("rwb", buffering=0)
        self.f.readline()  # "OK MPD x.y.z"

    def _send(self, text):
        self.f.write(text.encode("utf-8") + b"\n")
        out = {}
        while True:
            line = self.f.readline().decode("utf-8", "replace").rstrip("\n")
            if line == "OK":
                return out
            if line.startswith("ACK") or not line:
                raise RuntimeError(line or "connection closed")
            k, _, v = line.partition(": ")
            out[k] = v

    def run(self, text):
        with self.lock:
            for attempt in (1, 2):
                try:
                    if not self.sock:
                        self._connect()
                    return self._send(text)
                except (OSError, RuntimeError) as e:
                    self.sock = None
                    if attempt == 2 or isinstance(e, RuntimeError) and str(e).startswith("ACK"):
                        raise

    def batch(self, cmds):
        return self.run("command_list_begin\n" + "\n".join(cmds) + "\ncommand_list_end")

    @staticmethod
    def q(s):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


class Player:
    def __init__(self, root=DEF_ROOT, mpd=None, data=DATA):
        self.root, self.data = root, data
        self.mpd = mpd or MPD()
        self.t0 = time.time()
        self.warm = None
        self.current = None  # (card, folder)
        self.lock = threading.Lock()  # one MPD-changing action at a time: swipe vs warm-up
        self.last = []  # recent swipes with latency
        self.pos_written = 0
        os.makedirs(data, exist_ok=True)
        self.state_file = os.path.join(data, "state.json")
        self.resume = json.load(open(self.state_file)) if os.path.exists(self.state_file) else {}

    # ---- files (Phoniebox layout, read only) ----
    def setting(self, name, default=""):
        try:
            return open(os.path.join(self.root, "settings", name)).read().strip()
        except OSError:
            return default

    def folder_for(self, card):
        try:
            f = open(os.path.join(self.root, "shared", "shortcuts", card)).read().strip()
            return f if f and not os.path.isabs(f) and ".." not in f.split("/") else None  # stay inside audiofolders
        except OSError:
            return None

    def folder_conf(self, folder):
        conf = {}
        try:
            for line in open(os.path.join(self.root, "shared", "audiofolders", folder, "folder.conf")):
                m = re.match(r'(\w+)="?(.*?)"?\s*$', line)
                if m:
                    conf[m.group(1)] = m.group(2)
        except OSError:
            pass
        return conf

    def tracks(self, folder):
        base = os.path.join(self.root, "shared", "audiofolders", folder)
        out = []
        for d, dirs, files in os.walk(base):
            dirs[:] = [x for x in dirs if not x.startswith(".")]
            for fn in files:
                if not fn.startswith(".") and os.path.splitext(fn)[1].lower() in AUDIO:
                    out.append(os.path.relpath(os.path.join(d, fn), os.path.join(self.root, "shared", "audiofolders")).replace(os.sep, "/"))
        return sorted(out, key=natural)

    @staticmethod
    def uri(rel):  # identical to what Phoniebox's php writes into its m3u files
        return "local:track:" + quote(rel, safe="/")

    def cards(self):
        d = os.path.join(self.root, "shared", "shortcuts")
        res = []
        for card in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            folder = self.folder_for(card)
            if folder:
                conf = self.folder_conf(folder)
                res.append({"card": card, "folder": folder, "type": "book" if conf.get("RESUME") == "ON" else "music",
                            "tracks": len(self.tracks(folder))})
        return res

    # ---- the swipe ----
    def swipe(self, card):
        t_seen = time.time()
        folder = self.folder_for(card)
        if not folder:
            return {"ok": False, "error": "unknown card"}
        try:
            status = self.mpd.run("status")
        except (OSError, RuntimeError) as e:
            return {"ok": False, "error": "mpd: %s" % e}
        conf = self.folder_conf(folder)
        again = self.current == (card, folder) and status.get("state") in ("play", "pause")
        mode = self.setting("Second_Swipe", "RESTART").upper()
        if again and mode == "PAUSE":
            self.mpd.run("pause")
            return {"ok": True, "action": "toggle pause"}
        if again and mode == "SKIPNEXT":
            self.mpd.run("next")
            return {"ok": True, "action": "next"}
        recs = self.tracks(folder)
        if not recs:
            return {"ok": False, "error": "no audio in folder %s" % folder}
        book = conf.get("RESUME") == "ON"
        start, elapsed = 0, 0.0
        saved = self.resume.get(folder)
        if book and saved and not again:
            start, elapsed = min(saved["pos"], len(recs) - 1), saved["elapsed"]
        cmds = ["clear", "random %d" % (conf.get("SHUFFLE") == "ON"), "repeat %d" % (conf.get("LOOP") == "ON"),
                "single %d" % (conf.get("SINGLE") == "ON")]
        cmds += ["add " + MPD.q(self.uri(r)) for r in recs]
        cmds.append("play %d" % start)
        t_cmd = time.time()
        self.mpd.batch(cmds)
        if elapsed > 5:
            self.mpd.run("seekcur %.1f" % max(0, elapsed - 3))  # 3 s back, so the listener hears where they were
        self.current = (card, folder)
        t_play = self.wait_playing(t_cmd)
        rec = {"ts": round(t_seen, 3), "card": card, "folder": folder, "tracks": len(recs), "again": again,
               "prep_ms": round((t_cmd - t_seen) * 1000), "mpd_ms": round((t_play - t_cmd) * 1000) if t_play else None,
               "total_ms": round((t_play - t_seen) * 1000) if t_play else None}
        self.last = (self.last + [rec])[-20:]
        try:
            with open(os.path.join(self.data, "latency.jsonl"), "a") as f:
                f.write(json.dumps(rec) + "\n")
        except OSError:
            pass
        try:  # the old web UI shows this as "last card"
            open(os.path.join(self.root, "shared", "latestID.txt"), "w").write("Card ID '%s' was used at '%s'." % (card, time.strftime("%Y-%m-%d.%H:%M:%S")))
        except OSError:
            pass
        for name, val in (("Latest_RFID", card), ("Latest_Folder_Played", folder)):  # keep the old web UI's "last card" working
            try:
                open(os.path.join(self.root, "settings", name), "w").write(val)
            except OSError:
                pass
        return {"ok": True, "folder": folder, "tracks": len(recs), "book": book, "latency": rec}

    def control(self, name):
        """Transport buttons. Returns None when the old script must handle it (muted via the old mute file)."""
        if os.path.exists(os.path.join(self.root, "settings", "Audio_Volume_Level")):
            return None
        if name == "next":
            self.mpd.run("next")
        elif name == "prev":
            self.mpd.run("previous")
        elif name == "toggle":  # same as `mpc toggle`
            state = self.mpd.run("status").get("state")
            self.mpd.run("pause 1" if state == "play" else "pause 0" if state == "pause" else "play")
        else:
            return None
        return {"ok": True, "action": name}

    def stats(self):
        """Swipes per card from latency.jsonl: {card: {folder, plays, last_ts}}."""
        out = {}
        try:
            for line in open(os.path.join(self.data, "latency.jsonl")):
                r = json.loads(line)
                o = out.setdefault(r["card"], {"folder": r["folder"], "plays": 0, "last_ts": 0})
                o["plays"] += 1
                o["last_ts"] = max(o["last_ts"], r["ts"])
        except (OSError, ValueError, KeyError):
            pass
        return out

    def wait_playing(self, t_cmd, limit=6.0):
        while time.time() - t_cmd < limit:
            try:
                if self.mpd.run("status").get("state") == "play":
                    return time.time()
            except (OSError, RuntimeError):
                return None
            time.sleep(0.02)
        return None

    # ---- resume bookkeeping (audiobooks) ----
    def watch(self):
        prev = None
        while True:
            time.sleep(2)
            try:
                st = self.mpd.run("status")
            except (OSError, RuntimeError):
                continue
            if not self.current:
                continue
            folder = self.current[1]
            if self.folder_conf(folder).get("RESUME") != "ON":
                continue
            state = st.get("state")
            if state in ("play", "pause", "stop") and "song" in st:
                rec = {"pos": int(st["song"]), "elapsed": float(st.get("elapsed", 0))}
                changed = state != prev or time.time() - self.pos_written > 15
                if state == "play" and not changed:
                    continue
                if state != "play" and prev == state:
                    continue
                self.resume[folder] = rec
                tmp = self.state_file + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(self.resume, f)
                os.replace(tmp, self.state_file)  # atomic: a power cut leaves the old file, never a half-written one
                self.pos_written = time.time()
            prev = state


    # ---- warm-up: the first play after Mopidy starts costs ~1.5-2 s (pipeline, amp), the first lookup of each folder ~0.8 s ----
    def amixer(self, *args):
        return subprocess.run(["amixer", "-c", os.environ.get("GORDIBOX_ALSA_CARD", "1"), *args], capture_output=True, text=True).stdout

    def settle(self, clear=False):
        """Stop and make sure it stuck: Mopidy sometimes finishes starting a track after 'stop' (seen at boot: Tom Turbo played on its own, empty tracklist)."""
        for _ in range(5):
            self.mpd.run("stop")
            if clear:
                self.mpd.run("clear")
            time.sleep(1.0)
            if self.mpd.run("status").get("state") == "stop":
                return
            print("warm-up: mpd still %s after stop, retrying" % self.mpd.run("status").get("state"), flush=True)

    def warmup(self):
        """Muted 1 s play of the first card's first track, then load every card's folder once (library lookup), then clear.
        Muting uses the same volume manager as Phoniebox (amixer Master, or MPD volume). If we crash while muted the box is silent, not loud."""
        try:
            for _ in range(90):  # wait for Mopidy (at boot it answers a while after the unit starts)
                try:
                    self.mpd.run("status")
                    break
                except (OSError, RuntimeError):
                    time.sleep(1)
            time.sleep(2)
            cards = self.cards()
            first = next((c for c in cards if c["tracks"]), None)
            if not first:
                return
            use_amixer = self.setting("Volume_Manager", "mpd") == "amixer"
            ctl = self.setting("Audio_iFace_Name", "Master")
            with self.lock:  # a swipe during the muted second waits (about 4 s) instead of being muted and stopped
                if self.current:
                    self.warm = "skipped: swipe came first"
                    return
                if use_amixer:
                    m = re.search(r"\[(\d+)%\]", self.amixer("sget", ctl))
                    old = m.group(1) if m else None
                    self.amixer("sset", ctl, "0%")
                else:
                    old = self.mpd.run("status").get("volume")
                    self.mpd.run("setvol 0")
                try:
                    recs = self.tracks(first["folder"])
                    self.mpd.batch(["clear", "add " + MPD.q(self.uri(recs[0])), "play 0"])
                    time.sleep(1.2)
                    self.settle()
                finally:
                    if old is not None:
                        self.amixer("sset", ctl, old + "%") if use_amixer else self.mpd.run("setvol %s" % old)
            for c in cards:  # first lookup of each folder inside Mopidy's library
                recs = self.tracks(c["folder"])
                if recs and not os.path.exists(os.path.join(self.root, "shared", "audiofolders", c["folder"], "spotify.txt")):
                    with self.lock:
                        if self.current:
                            break
                        self.mpd.batch(["clear"] + ["add " + MPD.q(self.uri(r)) for r in recs[:3]])
            with self.lock:
                if not self.current:
                    self.settle(clear=True)
            self.warm = round(time.time() - self.t0, 1)
            print("warm-up done after %.1f s, mpd state %s" % (self.warm, self.mpd.run("status").get("state")), flush=True)
        except (OSError, RuntimeError, subprocess.SubprocessError) as e:
            self.warm = "failed: %s" % e

    def health(self):
        try:
            st = self.mpd.run("status")
            mpd_ok = True
        except (OSError, RuntimeError):
            st, mpd_ok = {}, False
        tot = [r["total_ms"] for r in self.last if r.get("total_ms")]
        return {"mpd": mpd_ok, "warmed_after_s": self.warm, "state": st.get("state"), "uptime_s": round(time.time() - self.t0), "last_swipes": self.last[-5:],
                "median_total_ms": sorted(tot)[len(tot) // 2] if tot else None}


def make_handler(player):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def reply(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            p = self.path.split("?")[0].strip("/").split("/")
            if p[0] == "swipe" and len(p) == 2 and re.fullmatch(r"[\w-]+", p[1]):
                with player.lock:
                    r = player.swipe(p[1])
                return self.reply(r, 200 if r.get("ok") else 404)
            if p[0] == "cmd" and len(p) == 2:
                try:
                    r = player.control(p[1])
                except (OSError, RuntimeError) as e:
                    r = None
                return self.reply(r or {"ok": False}, 200 if r else 409)
            if p == ["health"]:
                return self.reply(player.health())
            if p == ["stats"]:
                return self.reply(player.stats())
            if p == ["cards"]:
                return self.reply(player.cards())
            if p == ["state"]:
                return self.reply(player.mpd.run("status"))
            self.reply({"error": "not found"}, 404)

    return H


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--root", default=DEF_ROOT)
    ap.add_argument("--no-warmup", action="store_true")
    a = ap.parse_args()
    player = Player(a.root)
    threading.Thread(target=player.watch, daemon=True).start()
    if not a.no_warmup:
        threading.Thread(target=player.warmup, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(player)).serve_forever()  # localhost only: nothing else on the LAN can swipe


if __name__ == "__main__":
    main()
