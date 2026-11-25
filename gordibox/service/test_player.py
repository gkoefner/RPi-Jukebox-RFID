"""Run: python -m unittest service/test_player.py  (fake MPD server, temp Phoniebox tree; no box needed)"""
import os, socketserver, tempfile, threading, unittest, sys
sys.path.insert(0, os.path.dirname(__file__))
import gordibox_player as gp


class FakeMPD(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    log, state = [], {"state": "stop"}


class H(socketserver.StreamRequestHandler):
    def handle(self):
        self.wfile.write(b"OK MPD 0.19.0\n")
        batch = None
        for raw in self.rfile:
            line = raw.decode().strip()
            if line == "command_list_begin":
                batch = []
            elif line == "command_list_end":
                self.server.log.append(batch)
                if any(c.startswith("play") for c in batch):
                    self.server.state["state"] = "play"
                batch = None
                self.wfile.write(b"OK\n")
            elif batch is not None:
                batch.append(line)
            elif line == "status":
                self.wfile.write(("state: %s\nsong: 1\nelapsed: 42.5\nOK\n" % self.server.state["state"]).encode())
            else:
                self.server.log.append([line])
                self.wfile.write(b"OK\n")


class T(unittest.TestCase):
    def test_control_buttons(self):
        self.p.control("next"); self.p.control("toggle")
        self.assertIn(["next"], FakeMPD.log); self.assertIn(["play"], FakeMPD.log)  # stopped -> toggle plays
        open(os.path.join(self.tmp, "settings/Audio_Volume_Level"), "w").write("30")
        self.assertIsNone(self.p.control("next"))  # muted by the old script: hand over

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        r = self.tmp
        for d in ("shared/shortcuts", "shared/audiofolders/Book", "shared/audiofolders/Mix", "settings"):
            os.makedirs(os.path.join(r, d))
        for f in ("10 - b.mp3", "9 - a.mp3", "cover.jpg", ".hidden.mp3", "it's (1).mp3"):
            open(os.path.join(r, "shared/audiofolders/Mix", f), "w").close()
        open(os.path.join(r, "shared/audiofolders/Book/ch1.m4b"), "w").close()
        open(os.path.join(r, "shared/audiofolders/Book/folder.conf"), "w").write('RESUME="ON"\nSHUFFLE="OFF"\n')
        open(os.path.join(r, "shared/shortcuts/111"), "w").write("Mix\n")
        open(os.path.join(r, "shared/shortcuts/222"), "w").write("Book")
        FakeMPD.log, FakeMPD.state = [], {"state": "stop"}
        self.srv = FakeMPD(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.p = gp.Player(r, gp.MPD("127.0.0.1", self.srv.server_address[1]), data=os.path.join(r, "data"))

    def tearDown(self):
        self.srv.shutdown()

    def test_tracks_natural_order_and_filter(self):
        self.assertEqual(self.p.tracks("Mix"), ["Mix/9 - a.mp3", "Mix/10 - b.mp3", "Mix/it's (1).mp3"])

    def test_uri_matches_phoniebox_m3u_style(self):
        self.assertEqual(self.p.uri("Mix/it's (1).mp3"), "local:track:Mix/it%27s%20%281%29.mp3")

    def test_swipe_builds_one_command_list(self):
        r = self.p.swipe("111")
        self.assertTrue(r["ok"])
        batch = self.srv.log[0]
        self.assertEqual(batch[0], "clear")
        self.assertIn('add "local:track:Mix/9%20-%20a.mp3"', batch)
        self.assertEqual(batch[-1], "play 0")
        self.assertEqual(len(self.srv.log), 1)  # one round trip for the whole swipe

    def test_unknown_card(self):
        self.assertFalse(self.p.swipe("999")["ok"])

    def test_book_resumes_from_saved_position(self):
        self.p.resume["Book"] = {"pos": 0, "elapsed": 100.0}
        r = self.p.swipe("222")
        self.assertTrue(r["book"])
        self.assertEqual(self.srv.log[0][-1], "play 0")
        self.assertIn(["seekcur 97.0"], self.srv.log)

    def test_second_swipe_default_restarts(self):
        self.p.swipe("111")
        self.p.swipe("111")
        self.assertEqual(len([b for b in self.srv.log if b and b[0] == "clear"]), 2)

    def test_cards_listing(self):
        c = {x["card"]: x for x in self.p.cards()}
        self.assertEqual(c["222"]["type"], "book")
        self.assertEqual(c["111"]["tracks"], 3)


if __name__ == "__main__":
    unittest.main()
