"""Testes das partes puras (sem rede, sem interface):  python -m unittest discover -s tests -v"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sonora.links import classify, parse_inputs
from sonora.matching import pick_best, score_candidate
from sonora.models import TrackMeta
from sonora.utils import (
    clean_title, extract_featured, guess_artist_title, render_template, sanitize_filename,
    similarity, unique_path,
)


class TestFilenames(unittest.TestCase):
    def test_sanitize_invalid_chars(self):
        self.assertEqual(sanitize_filename('AC/DC: "Back" in Black?'), "AC-DC - 'Back' in Black")

    def test_sanitize_reserved_and_dots(self):
        self.assertEqual(sanitize_filename("CON"), "_CON")
        self.assertEqual(sanitize_filename("  Nome...  "), "Nome")

    def test_template_basic(self):
        vals = {"artist": "Mother Mother", "title": "Burning Pile", "album": "O My Heart", "track": "02"}
        self.assertEqual(render_template("{artist} - {title}", vals).as_posix(), "Mother Mother - Burning Pile")
        self.assertEqual(
            render_template("{artist}/{album}/{track} - {title}", vals).as_posix(),
            "Mother Mother/O My Heart/02 - Burning Pile",
        )

    def test_template_missing_values_are_cleaned(self):
        vals = {"artist": "A", "title": "T", "album": "", "track": ""}
        self.assertEqual(render_template("{artist}/{album}/{track} - {title}", vals).as_posix(), "A/T")

    def test_template_blocks_path_traversal(self):
        vals = {"artist": "..", "title": "x"}
        self.assertNotIn("..", render_template("{artist}/{title}", vals).parts)

    def test_title_with_dot_keeps_name(self):
        vals = {"artist": "The Killers", "title": "Mr. Brightside"}
        rel = render_template("{artist} - {title}", vals)
        self.assertEqual(rel.with_name(rel.name + ".mp3").name, "The Killers - Mr. Brightside.mp3")

    def test_unique_path(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.mp3"
            p.write_text("x")
            self.assertEqual(unique_path(p).name, "a (2).mp3")


class TestTitles(unittest.TestCase):
    def test_clean_title(self):
        self.assertEqual(clean_title("Burning Pile (Official Video)"), "Burning Pile")
        self.assertEqual(clean_title("Song [Official Audio] (HD)"), "Song")
        self.assertEqual(clean_title("Song (Live)"), "Song (Live)")

    def test_extract_featured(self):
        self.assertEqual(extract_featured("Song (feat. A & B)"), ("Song", ["A", "B"]))
        self.assertEqual(extract_featured("Song ft. C"), ("Song", ["C"]))

    def test_guess_artist_title(self):
        a, t, f = guess_artist_title("Mother Mother - Burning Pile (Official Video)", "MotherMotherVEVO")
        self.assertEqual((a, t, f), ("Mother Mother", "Burning Pile", []))
        a, t, f = guess_artist_title("Cool Song", "Some Artist - Topic")
        self.assertEqual((a, t), ("Some Artist", "Cool Song"))
        a, t, f = guess_artist_title("Main x Guest - Track (feat. Other)", "")
        self.assertEqual((a, t, f), ("Main", "Track", ["Guest", "Other"]))

    def test_similarity(self):
        self.assertGreater(similarity("Burning Pile", "burning pile!"), 0.95)
        self.assertLess(similarity("Burning Pile", "Another Song"), 0.5)
        self.assertGreater(similarity("Música Ação", "musica acao"), 0.95)


class TestLinks(unittest.TestCase):
    def test_spotify(self):
        l = classify("https://open.spotify.com/intl-pt/track/4uLU6hMCjMI75M1A2tKUQC?si=abc")
        self.assertEqual((l.kind, l.id), ("spotify_track", "4uLU6hMCjMI75M1A2tKUQC"))
        self.assertEqual(classify("spotify:playlist:37i9dQZF1DXcBWIGoYBM5M").kind, "spotify_playlist")
        self.assertEqual(classify("https://open.spotify.com/album/1DFixLWuPkv3KT3TnV35m3").kind, "spotify_album")

    def test_youtube(self):
        self.assertEqual(classify("https://www.youtube.com/watch?v=dQw4w9WgXcQ").kind, "youtube_video")
        self.assertEqual(classify("https://youtu.be/dQw4w9WgXcQ?t=10").id, "dQw4w9WgXcQ")
        self.assertEqual(classify("https://music.youtube.com/watch?v=dQw4w9WgXcQ").kind, "youtube_video")
        self.assertEqual(classify("https://www.youtube.com/playlist?list=PLabc123").kind, "youtube_playlist")
        self.assertEqual(classify("https://www.youtube.com/shorts/dQw4w9WgXcQ").id, "dQw4w9WgXcQ")

    def test_video_plus_list(self):
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLabc123"
        self.assertEqual(classify(url).kind, "youtube_video")
        self.assertEqual(classify(url, playlist_if_both=True).kind, "youtube_playlist")
        mix = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=RDdQw4w9WgXcQ"
        self.assertEqual(classify(mix, playlist_if_both=True).kind, "youtube_video")

    def test_search_and_unsupported(self):
        self.assertEqual(classify("Mother Mother - Burning Pile").kind, "search")
        self.assertEqual(classify("https://example.com/x").kind, "unsupported")

    def test_parse_inputs(self):
        text = "https://youtu.be/dQw4w9WgXcQ https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC\n\nArtista - Música\n"
        self.assertEqual(len(parse_inputs(text)), 3)


class TestMatching(unittest.TestCase):
    def setUp(self):
        self.meta = TrackMeta(title="Burning Pile", artists=["Mother Mother"], duration=258, origin="spotify")

    def test_prefers_topic_with_right_duration(self):
        cands = [
            {"id": "1", "title": "Mother Mother - Burning Pile (Live at the Roxy)", "channel": "Fan", "duration": 300},
            {"id": "2", "title": "Burning Pile", "channel": "Mother Mother - Topic", "duration": 258},
            {"id": "3", "title": "Burning Pile (Slowed + Reverb)", "channel": "Chill", "duration": 330},
        ]
        best, _ = pick_best(self.meta, cands)
        self.assertEqual(best["id"], "2")

    def test_rejects_unrelated(self):
        cands = [{"id": "x", "title": "Totally different thing", "channel": "Someone", "duration": 30}]
        best, _ = pick_best(self.meta, cands)
        self.assertIsNone(best)

    def test_original_remix_not_penalised(self):
        meta = TrackMeta(title="Song - Remix", artists=["A"], duration=200, origin="spotify")
        good = {"id": "1", "title": "A - Song - Remix", "channel": "A - Topic", "duration": 200}
        self.assertGreater(score_candidate(meta, good), 60)


if __name__ == "__main__":
    unittest.main()
