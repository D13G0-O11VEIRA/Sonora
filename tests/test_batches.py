"""Testa o fatiamento de playlists grandes em lotes + "Continuar" (sem rede, sem baixar nada)."""
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:  # o Manager importa o mutagen; nos testes ele não é usado
    import mutagen  # noqa: F401
except ImportError:
    fake = types.ModuleType("mutagen")
    fid3 = types.ModuleType("mutagen.id3")
    for n in ("APIC COMM ID3 TALB TCON TDRC TIT2 TPE1 TPE2 TPOS TRCK TSRC USLT WOAS ID3NoHeaderError").split():
        setattr(fid3, n, type(n, (), {}))
    fmp3 = types.ModuleType("mutagen.mp3")
    fmp3.MP3 = type("MP3", (), {})
    sys.modules.update({"mutagen": fake, "mutagen.id3": fid3, "mutagen.mp3": fmp3})

from sonora import manager as manager_mod
from sonora.config import Settings
from sonora.models import Resolved, TrackMeta


class FakePool:
    def __init__(self):
        self.submitted = []

    def submit(self, fn, *args):
        self.submitted.append(args)

    def shutdown(self, *a, **k):
        pass


def make_manager(batch_size=100):
    tmp = Path(tempfile.mkdtemp())
    manager_mod.DATA_DIR, manager_mod.TMP_DIR = tmp, tmp / "tmp"
    manager_mod.DB_FILE, manager_mod.LOG_FILE = tmp / "h.db", tmp / "log.txt"
    s = Settings()
    s.output_dir = str(tmp / "music")
    s.batch_size = batch_size
    s.save = lambda: None
    m = manager_mod.Manager(s)
    m._pool = FakePool()
    return m


def playlist(n, capped=False, kind="playlist"):
    tracks = [TrackMeta(title=f"Faixa {i}", artists=["Artista"], origin="spotify") for i in range(1, n + 1)]
    return Resolved(tracks, "Minha Lista", kind, capped=capped)


class BatchTests(unittest.TestCase):
    def test_big_playlist_is_split_and_continues(self):
        m = make_manager(100)
        m._enqueue(playlist(250))
        self.assertEqual(len(m.jobs), 100)
        info = m.batch_info()
        self.assertEqual((info[0]["enqueued"], info[0]["total"], info[0]["pending"], info[0]["next"]), (100, 250, 150, 100))

        self.assertEqual(m.continue_batch(info[0]["id"]), 100)
        self.assertEqual(len(m.jobs), 200)
        self.assertEqual(m.batch_info()[0]["pending"], 50)

        self.assertEqual(m.continue_batch(info[0]["id"]), 50)
        self.assertEqual(len(m.jobs), 250)
        self.assertEqual(m.batch_info(), [])                 # nada pendente e sem aviso: o cartão some
        titles = [j.meta.title for j in m.jobs.values()]
        self.assertEqual(titles[100], "Faixa 101")           # continua de onde parou
        self.assertEqual([j.meta.playlist_index for j in list(m.jobs.values())[99:102]], [100, 101, 102])
        self.assertEqual(m.continue_batch(info[0]["id"]), 0)  # nada mais para continuar

    def test_small_playlist_is_not_split(self):
        m = make_manager(100)
        m._enqueue(playlist(40))
        self.assertEqual(len(m.jobs), 40)
        self.assertEqual(m.batch_info(), [])

    def test_batch_size_zero_never_pauses(self):
        m = make_manager(0)
        m._enqueue(playlist(300))
        self.assertEqual(len(m.jobs), 300)
        self.assertEqual(m.batch_info(), [])

    def test_custom_batch_size(self):
        m = make_manager(30)
        m._enqueue(playlist(70))
        self.assertEqual(len(m.jobs), 30)
        self.assertEqual(m.batch_info()[0]["next"], 30)

    def test_capped_spotify_playlist_shows_notice_until_dismissed(self):
        m = make_manager(100)
        m._enqueue(playlist(100, capped=True))
        info = m.batch_info()
        self.assertEqual(len(info), 1)
        self.assertTrue(info[0]["capped"])
        self.assertEqual(info[0]["pending"], 0)
        m.dismiss_batch(info[0]["id"])
        self.assertEqual(m.batch_info(), [])

    def test_dismiss_drops_pending(self):
        m = make_manager(100)
        m._enqueue(playlist(150))
        bid = m.batch_info()[0]["id"]
        m.dismiss_batch(bid)
        self.assertEqual(m.batch_info(), [])
        self.assertEqual(m.continue_batch(bid), 0)

    def test_single_track_has_no_batch(self):
        m = make_manager(100)
        m._enqueue(Resolved([TrackMeta(title="X", artists=["A"])], "", "single"))
        self.assertEqual(len(m.jobs), 1)
        self.assertEqual(m.batch_info(), [])


if __name__ == "__main__":
    unittest.main()
