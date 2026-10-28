"""Orquestra tudo: lê links, cria a fila, baixa, enriquece, grava tags e organiza.

A GUI e a CLI conversam com o Manager por eventos (manager.events), então a lógica
de negócio fica totalmente separada da interface.

Eventos (tupla (tipo, dado)):
    ("job_added", Job)  ("job_update", Job)  ("log", str)  ("notice", str)  ("idle", None)
"""
from __future__ import annotations

import logging
import os
import queue
import re
import shutil
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import enrich
from .config import DATA_DIR, DB_FILE, LOG_FILE, TMP_DIR, Settings
from .downloader import Cancelled, download_audio, find_best_match
from .history import History
from .links import classify, parse_inputs
from .models import Batch, Job, Resolved, Status, TrackMeta
from .resolvers import spotify
from .resolvers import youtube as yt
from .tagger import write_tags
from .utils import render_template, sanitize_filename, unique_path

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


class _QueueLogHandler(logging.Handler):
    def __init__(self, q: queue.Queue):
        super().__init__()
        self.q = q

    def emit(self, record: logging.LogRecord) -> None:
        self.q.put(("log", self.format(record)))


class Manager:
    def __init__(self, settings: Settings):
        self.settings = settings
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(TMP_DIR, ignore_errors=True)       # restos de execuções anteriores
        TMP_DIR.mkdir(parents=True, exist_ok=True)

        self.history = History(DB_FILE)
        self.events: queue.Queue = queue.Queue()
        self.jobs: dict[int, Job] = {}

        self._resolve_pool = ThreadPoolExecutor(2, thread_name_prefix="resolve")
        self._pool = ThreadPoolExecutor(8, thread_name_prefix="job")
        self._sem = threading.Semaphore(settings.concurrent_downloads)
        self._work = 0
        self._cv = threading.Condition()

        self.logger = logging.getLogger("sonora")
        self.logger.setLevel(logging.INFO)
        for h in list(self.logger.handlers):
            self.logger.removeHandler(h)
        fh = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        qh = _QueueLogHandler(self.events)
        qh.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))
        self.logger.addHandler(fh)
        self.logger.addHandler(qh)

    # ------------------------------------------------------------ infra
    def emit(self, kind: str, payload=None) -> None:
        self.events.put((kind, payload))

    def log(self, msg: str) -> None:
        self.logger.info(msg)

    def apply_settings(self) -> None:
        self._sem = threading.Semaphore(self.settings.concurrent_downloads)

    def _inc_work(self, n: int = 1) -> None:
        with self._cv:
            self._work += n

    def _dec_work(self) -> None:
        with self._cv:
            self._work -= 1
            if self._work <= 0:
                self._work = 0
                self.emit("idle")
                self._cv.notify_all()

    def wait_idle(self) -> None:
        with self._cv:
            self._cv.wait_for(lambda: self._work == 0)

    def shutdown(self) -> None:
        self.cancel_all()
        self._resolve_pool.shutdown(wait=False, cancel_futures=True)
        self._pool.shutdown(wait=False, cancel_futures=True)
        shutil.rmtree(TMP_DIR, ignore_errors=True)

    # ------------------------------------------------------------ entrada
    def add_links(self, text: str) -> int:
        items = parse_inputs(text)
        if not items:
            self.emit("notice", "Cole pelo menos um link ou nome de música.")
            return 0
        for item in items:
            self._inc_work()
            self._resolve_pool.submit(self._resolve_link, item)
        return len(items)

    def _resolve_link(self, item: str) -> None:
        s = self.settings
        try:
            link = classify(item, s.watch_list_is_playlist)
            self.log(f"Lendo: {item}")
            if link.kind == "unsupported":
                self._notice(f"Link não suportado: {item}")
                return
            if link.kind == "spotify_artist":
                self._notice("Links de artista não são suportados: cole uma faixa, álbum ou playlist.")
                return
            if link.kind.startswith("spotify_"):
                res = spotify.resolve(link.kind.split("_", 1)[1], link.id, item, s, self.log)
            elif link.kind == "youtube_video":
                res = yt.resolve_video(link.url, s)
            elif link.kind == "youtube_playlist":
                res = yt.resolve_playlist(link.url, s, self.log)
            else:  # busca por texto
                artist, sep, title = item.partition(" - ")
                meta = (TrackMeta(title=title.strip(), artists=[artist.strip()], origin="search")
                        if sep else TrackMeta(title=item, origin="search"))
                res = Resolved([meta], "", "single")
            self._enqueue(res)
        except Exception as e:
            self.logger.error("Falha ao ler '%s':\n%s", item, traceback.format_exc())
            self._notice(f"Não consegui ler o link: {self._short_error(e)}")
        finally:
            self._dec_work()

    def _notice(self, msg: str) -> None:
        self.log(msg)
        self.emit("notice", msg)

    def _enqueue(self, res: Resolved) -> None:
        s = self.settings
        if not res.tracks:
            self._notice("Nenhuma faixa encontrada nesse link.")
            return
        batch = None
        if res.kind in ("album", "playlist"):
            folder = s.out_path / sanitize_filename(res.name) if s.playlist_subfolder else s.out_path
            batch = Batch(res.name, res.kind, folder)
            batch.remaining = len(res.tracks)
        jobs: list[Job] = []
        for i, meta in enumerate(res.tracks, 1):
            if batch:
                meta.playlist_name, meta.playlist_index = res.name, i
            job = Job(meta, batch)
            self.jobs[job.id] = job
            if batch:
                batch.jobs.append(job)
            jobs.append(job)
        self._inc_work(len(jobs))            # antes de liberar o resolvedor (evita "idle" falso)
        self.log(f"{len(jobs)} faixa(s) adicionada(s)" + (f" de '{res.name}'" if res.name else ""))
        for job in jobs:
            self.emit("job_added", job)
        for job in jobs:
            self._pool.submit(self._run_job, job)

    # ------------------------------------------------------------ controle
    def cancel(self, job: Job) -> None:
        if job.active:
            job.cancel_event.set()

    def cancel_all(self) -> None:
        for job in list(self.jobs.values()):
            self.cancel(job)

    def retry(self, job: Job) -> None:
        if not job.finished:
            return
        job.cancel_event.clear()
        job.status, job.error, job.progress, job.speed, job.eta = Status.QUEUED, "", 0.0, "", ""
        if job.batch:
            with job.batch.lock:
                job.batch.remaining += 1
        self._inc_work()
        self.emit("job_update", job)
        self._pool.submit(self._run_job, job)

    def forget_finished(self) -> list[Job]:
        done = [j for j in self.jobs.values() if j.finished]
        for j in done:
            self.jobs.pop(j.id, None)
        return done

    # ------------------------------------------------------------ execução
    def _set(self, job: Job, status: Status) -> None:
        job.status = status
        self.emit("job_update", job)

    def _run_job(self, job: Job) -> None:
        sem, acquired = None, False
        while True:                                   # espera vaga, mas permite cancelar na fila
            if job.cancel_event.is_set():
                break
            sem = self._sem
            if sem.acquire(timeout=0.3):
                acquired = True
                break
        final, err = Status.CANCELLED, ""
        try:
            if acquired and not job.cancel_event.is_set():
                final = self._process(job)
        except Cancelled:
            final = Status.CANCELLED
        except Exception as e:
            final, err = Status.ERROR, self._short_error(e)
            self.logger.error("Erro em '%s':\n%s", job.meta.title or job.meta.youtube_url, traceback.format_exc())
        finally:
            if acquired and sem:
                sem.release()
            shutil.rmtree(TMP_DIR / f"job{job.id}", ignore_errors=True)
        self._finish(job, final, err)

    def _finish(self, job: Job, status: Status, error: str = "") -> None:
        job.status, job.error = status, error
        if status == Status.DONE:
            job.progress = 100.0
        job.speed = job.eta = ""
        self.emit("job_update", job)
        if job.batch:
            b = job.batch
            with b.lock:
                b.remaining -= 1
                last = b.remaining <= 0
            if last and self.settings.create_m3u:
                self._write_m3u(b)
        self._dec_work()

    @staticmethod
    def _short_error(e: Exception) -> str:
        lines = _ANSI.sub("", str(e)).replace("ERROR:", "").strip().splitlines()
        msg = lines[0].strip() if lines else type(e).__name__
        low = msg.lower()
        if ("confirm you" in low and "bot" in low) or "sign in" in low:
            msg += " → em Configurações, escolha seu navegador em 'Cookies do navegador'."
        elif "javascript runtime" in low or "n challenge" in low or "signature" in low:
            msg += " → atualize o yt-dlp (Configurações) e instale o Deno (winget install DenoLand.Deno)."
        elif "ffmpeg" in low:
            msg += " → instale o ffmpeg ou rode: pip install imageio-ffmpeg"
        return msg[:260]

    def _target_path(self, job: Job) -> Path:
        m, s = job.meta, self.settings
        album_artist = m.album_artist or m.main_artist
        values = {
            "artist": m.main_artist or "Desconhecido",
            "artists": ", ".join(m.artists),
            "title": m.title or "Sem título",
            "album": m.album,
            "album_artist": album_artist,
            "year": m.year,
            "track": f"{m.track_no:02d}" if m.track_no else "",
            "disc": str(m.disc_no) if m.disc_no else "",
            "genre": m.genre,
            "index": f"{m.playlist_index:02d}" if m.playlist_index else "",
            "playlist": m.playlist_name,
        }
        rel = render_template(s.filename_template, values)
        base = job.batch.folder if job.batch else s.out_path
        return base / rel.with_name(rel.name + ".mp3")   # (não usar with_suffix: "Mr. Bright" quebraria)

    def _process(self, job: Job) -> Status:
        s, m = self.settings, job.meta
        self._set(job, Status.RESOLVING)

        if s.skip_existing:
            prior = self.history.find(m.spotify_id, m.youtube_id)
            if prior:
                job.output_path = Path(prior)
                return Status.SKIPPED

        authoritative = m.origin == "spotify" or (m.origin == "search" and bool(m.artists))

        if not m.youtube_url:
            cand = find_best_match(m, s, self.log)
            if not cand:
                raise RuntimeError("Nenhuma versão correspondente encontrada no YouTube.")
            m.youtube_url, m.youtube_id = cand["url"], cand.get("id") or ""
            if not m.duration and cand.get("duration"):
                m.duration = float(cand["duration"])
            if s.skip_existing and (prior := self.history.find(m.youtube_id)):
                job.output_path = Path(prior)
                return Status.SKIPPED

        if s.skip_existing and authoritative and m.title:
            target = self._target_path(job)
            if target.exists():
                job.output_path = target
                return Status.SKIPPED

        # ---------------------------------------------------- download
        tmp = TMP_DIR / f"job{job.id}"
        self._set(job, Status.DOWNLOADING)
        last_emit = [0.0]

        def on_progress(pct: float, speed: str, eta: str) -> None:
            job.progress, job.speed, job.eta = pct, speed, eta
            now = time.monotonic()
            if now - last_emit[0] > 0.25:
                last_emit[0] = now
                self.emit("job_update", job)

        def on_stage(_: str) -> None:
            job.progress, job.speed, job.eta = 99.0, "", ""
            self._set(job, Status.CONVERTING)

        path, info = download_audio(m.youtube_url, tmp, s, on_progress, on_stage, job.cancel_event)

        # ---------------------------------------------------- metadados
        self._set(job, Status.TAGGING)
        if not authoritative:
            yt.meta_from_info(info, m)
        m.duration = m.duration or float(info.get("duration") or 0)
        m.title = m.title or info.get("title") or "Sem título"
        if s.enrich_online:
            enrich.itunes_enrich(m, self.log)

        cover = None
        if s.embed_cover:
            for url in (m.cover_url, m.thumb_url, info.get("thumbnail")):
                if not url:
                    continue
                try:
                    cover = enrich.make_cover(enrich.fetch_bytes(url))
                    break
                except Exception as e:
                    self.log(f"Capa indisponível ({type(e).__name__}); tentando outra fonte.")

        lyrics = synced = ""
        if s.fetch_lyrics:
            lyrics, synced = enrich.fetch_lyrics(m, self.log)

        write_tags(path, {
            "title": m.title,
            "artist": s.artist_separator.join(m.artists),
            "album": m.album,
            "album_artist": m.album_artist or m.main_artist,
            "year": m.year,
            "track": m.track_no, "track_total": m.track_total,
            "disc": m.disc_no, "disc_total": m.disc_total,
            "genre": m.genre,
            "isrc": m.isrc,
            "comment": f"Baixado com Sonora | {m.source_url or m.youtube_url}",
            "lyrics": lyrics,
            "source_url": m.source_url or m.youtube_url,
        }, cover)

        # ---------------------------------------------------- organizar
        target = self._target_path(job)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if s.skip_existing:
                job.output_path = target
                return Status.SKIPPED
            target = unique_path(target)
        shutil.move(str(path), str(target))
        if s.save_lrc and synced:
            target.with_suffix(".lrc").write_text(synced, encoding="utf-8")
        job.output_path = target
        self.history.add(m, target)
        self.log(f"✔ {target.name}")
        return Status.DONE

    # ------------------------------------------------------------ playlists
    def _write_m3u(self, batch: Batch) -> None:
        ok = [j for j in batch.jobs
              if j.status in (Status.DONE, Status.SKIPPED) and j.output_path and j.output_path.exists()]
        if not ok:
            return
        m3u = batch.folder / f"{sanitize_filename(batch.name) or 'playlist'}.m3u8"
        lines = ["#EXTM3U"]
        for j in ok:
            rel = os.path.relpath(j.output_path, batch.folder).replace("\\", "/")
            lines.append(f"#EXTINF:{int(j.meta.duration or 0)},{j.meta.artist_line()} - {j.meta.title}")
            lines.append(rel)
        try:
            batch.folder.mkdir(parents=True, exist_ok=True)
            m3u.write_text("\n".join(lines) + "\n", encoding="utf-8")
            self.log(f"Playlist criada: {m3u.name}")
        except OSError as e:
            self.log(f"Não foi possível criar a playlist .m3u8: {e}")
