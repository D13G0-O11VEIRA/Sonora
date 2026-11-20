"""Testes do servidor web local com um Manager falso (sem rede, sem yt-dlp)."""
import http.client
import json
import queue
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sonora.config import Settings
from sonora.models import Job, Status, TrackMeta
from sonora.web.server import SonoraServer, apply_settings_dict


class FakeManager:
    def __init__(self):
        self.settings = Settings()
        self.settings.save = lambda: None            # não escreve data/config.json nos testes
        self.events: queue.Queue = queue.Queue()
        self.jobs: dict[int, Job] = {}
        self.added: list[str] = []
        self.cancelled: list[int] = []

    def add_links(self, text):
        self.added.append(text)
        return len([l for l in text.splitlines() if l.strip()])

    def cancel(self, job): self.cancelled.append(job.id)
    def retry(self, job): job.status = Status.QUEUED
    def cancel_all(self): self.cancelled.append(-1)
    def forget_finished(self):
        done = [j for j in self.jobs.values() if j.finished]
        for j in done:
            self.jobs.pop(j.id)
        return done
    def apply_settings(self): pass
    def log(self, msg): pass
    def batch_info(self):
        return [{"id": 7, "name": "Lista", "kind": "playlist", "enqueued": 100, "total": 250,
                 "pending": 150, "next": 100, "capped": True}]
    def continue_batch(self, bid):
        self.continued = bid
        return 100
    def dismiss_batch(self, bid):
        self.dismissed = bid


class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mgr = FakeManager()
        cls.srv = SonoraServer(("127.0.0.1", 0), cls.mgr)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = dict(headers or {})
        data = None
        if body is not None:
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            h.setdefault("Content-Type", "application/json")
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        raw = r.read()
        c.close()
        return r.status, dict(r.getheaders()), raw

    def test_index_and_manifest(self):
        st, _, raw = self.req("GET", "/")
        self.assertEqual(st, 200)
        self.assertIn(b"Sonora", raw)
        st, _, raw = self.req("GET", "/manifest.json")
        m = json.loads(raw)
        self.assertEqual(m["share_target"]["action"], "/share")
        self.assertEqual(m["display"], "standalone")

    def test_static_whitelist(self):
        st, hdr, raw = self.req("GET", "/static/icon-192.png")
        self.assertEqual(st, 200)
        self.assertTrue(raw.startswith(b"\x89PNG"))
        st, _, _ = self.req("GET", "/static/../config.py")
        self.assertEqual(st, 404)

    def test_state_lists_jobs(self):
        job = Job(TrackMeta(title="Burning Pile", artists=["Mother Mother"], album="O My Heart"))
        job.status, job.progress = Status.DOWNLOADING, 42.0
        self.mgr.jobs[job.id] = job
        st, _, raw = self.req("GET", "/api/state")
        data = json.loads(raw)
        item = next(j for j in data["jobs"] if j["id"] == job.id)
        self.assertEqual(item["title"], "Burning Pile")
        self.assertEqual(item["artists"], "Mother Mother")
        self.assertEqual(item["code"], "downloading")
        self.assertTrue(item["active"])
        self.assertGreaterEqual(data["active"], 1)
        self.mgr.jobs.pop(job.id)

    def test_add_links(self):
        st, _, raw = self.req("POST", "/api/add", {"text": "https://youtu.be/dQw4w9WgXcQ\nArtista - Música"})
        self.assertEqual(st, 200)
        self.assertEqual(json.loads(raw)["added"], 2)
        self.assertIn("https://youtu.be/dQw4w9WgXcQ", self.mgr.added[-1])

    def test_batches_in_state_and_continue(self):
        st, _, raw = self.req("GET", "/api/state")
        data = json.loads(raw)
        self.assertEqual(data["batches"][0]["pending"], 150)
        self.assertIn("100", data["capped_help"])
        st, _, raw = self.req("POST", "/api/continue", {"id": 7})
        self.assertEqual(json.loads(raw)["started"], 100)
        self.assertEqual(self.mgr.continued, 7)
        self.req("POST", "/api/dismiss", {"id": 7})
        self.assertEqual(self.mgr.dismissed, 7)

    def test_post_requires_json(self):
        st, _, _ = self.req("POST", "/api/add", b"text=x", {"Content-Type": "text/plain"})
        self.assertEqual(st, 415)

    def test_foreign_origin_blocked(self):
        st, _, _ = self.req("POST", "/api/add", {"text": "x"}, {"Origin": "https://evil.example"})
        self.assertEqual(st, 403)
        st, _, _ = self.req("GET", "/api/state", headers={"Origin": "https://evil.example"})
        self.assertEqual(st, 403)
        ok, _, _ = self.req("GET", "/api/state", headers={"Origin": f"http://127.0.0.1:{self.port}"})
        self.assertEqual(ok, 200)

    def test_share_prefills_but_does_not_download(self):
        before = len(self.mgr.added)
        st, hdr, _ = self.req("GET", "/share?title=Faixa&url=https%3A%2F%2Fyoutu.be%2FdQw4w9WgXcQ")
        self.assertEqual(st, 302)
        self.assertTrue(hdr["Location"].startswith("/?text="))
        self.assertIn("youtu.be", hdr["Location"])
        self.assertEqual(len(self.mgr.added), before)

    def test_cancel_and_clear(self):
        job = Job(TrackMeta(title="x"))
        self.mgr.jobs[job.id] = job
        self.req("POST", "/api/cancel", {"id": job.id})
        self.assertIn(job.id, self.mgr.cancelled)
        job.status = Status.DONE
        st, _, raw = self.req("POST", "/api/clear", {})
        self.assertEqual(json.loads(raw)["cleared"], 1)

    def test_settings_roundtrip_and_secret_hidden(self):
        self.mgr.settings.spotify_client_secret = "segredo"
        st, _, raw = self.req("GET", "/api/settings")
        data = json.loads(raw)
        self.assertNotIn("spotify_client_secret", data)
        self.assertTrue(data["spotify_client_secret_set"])
        st, _, raw = self.req("POST", "/api/settings", {
            "bitrate": "192", "concurrent_downloads": 99, "spotify_client_secret": "", "fetch_lyrics": False})
        self.assertEqual(self.mgr.settings.bitrate, "192")
        self.assertEqual(self.mgr.settings.concurrent_downloads, 6)
        self.assertEqual(self.mgr.settings.spotify_client_secret, "segredo")   # vazio = mantém
        self.assertFalse(self.mgr.settings.fetch_lyrics)


class TestApplySettings(unittest.TestCase):
    def test_validation(self):
        s = Settings()
        apply_settings_dict(s, {"batch_size": 99999})
        self.assertEqual(s.batch_size, 1000)
        changed = apply_settings_dict(s, {"bitrate": "999", "js_runtime": "rm -rf", "artist_separator": "",
                                          "filename_template": "  ", "theme": "light", "nope": 1})
        self.assertEqual(s.bitrate, "320")
        self.assertEqual(s.js_runtime, Settings().js_runtime)
        self.assertEqual(s.artist_separator, "; ")
        self.assertEqual(s.filename_template, "{artist} - {title}")
        self.assertEqual(s.theme, Settings().theme)       # tema não é editável pela web
        self.assertNotIn("theme", changed)


if __name__ == "__main__":
    unittest.main()
