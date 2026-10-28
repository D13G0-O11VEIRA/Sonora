"""Modo linha de comando:  python main.py <links...> [opções]"""
from __future__ import annotations

import argparse
import queue
import sys
import threading
from pathlib import Path

from .config import BITRATES, Settings
from .manager import Manager
from .models import Status


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sonora",
        description="Baixa músicas do YouTube/Spotify em MP3 com metadados completos. "
                    "Sem argumentos, abre a interface gráfica.",
    )
    p.add_argument("links", nargs="*", help="links (YouTube/Spotify), playlists ou 'Artista - Música'")
    p.add_argument("-f", "--file", help="arquivo .txt com um link por linha")
    p.add_argument("-o", "--output", help="pasta de destino")
    p.add_argument("-b", "--bitrate", choices=BITRATES, help="qualidade do MP3 (kbps)")
    p.add_argument("-t", "--template", help="modelo do nome do arquivo, ex.: '{artist} - {title}'")
    p.add_argument("-j", "--jobs", type=int, help="downloads simultâneos (1-6)")
    p.add_argument("--no-lyrics", action="store_true", help="não buscar letras")
    p.add_argument("--no-online", action="store_true", help="não completar metadados online (iTunes)")
    p.add_argument("--no-cover", action="store_true", help="não embutir capa")
    p.add_argument("--save-lrc", action="store_true", help="salvar letra sincronizada (.lrc)")
    p.add_argument("--force", action="store_true", help="baixar mesmo que já exista")
    p.add_argument("--gui", action="store_true", help="forçar a interface gráfica")
    return p


def apply_args(settings: Settings, a: argparse.Namespace) -> None:
    if a.output:
        settings.output_dir = str(Path(a.output).expanduser())
    if a.bitrate:
        settings.bitrate = a.bitrate
    if a.template:
        settings.filename_template = a.template
    if a.jobs:
        settings.concurrent_downloads = max(1, min(6, a.jobs))
    if a.no_lyrics:
        settings.fetch_lyrics = False
    if a.no_online:
        settings.enrich_online = False
    if a.no_cover:
        settings.embed_cover = False
    if a.save_lrc:
        settings.save_lrc = True
    if a.force:
        settings.skip_existing = False


def run_cli(settings: Settings, args: argparse.Namespace) -> int:
    text = "\n".join(args.links)
    if args.file:
        text += "\n" + Path(args.file).read_text(encoding="utf-8")
    manager = Manager(settings)
    print(f"Destino: {settings.out_path}  |  {settings.bitrate} kbps  |  {settings.concurrent_downloads} simultâneos\n")
    if not manager.add_links(text):
        print("Nenhum link informado.")
        return 1

    last_pct: dict[int, int] = {}
    stats = {"ok": 0, "skip": 0, "err": 0, "cancel": 0}
    stop = threading.Event()

    def printer() -> None:
        while not stop.is_set() or not manager.events.empty():
            try:
                kind, data = manager.events.get(timeout=0.2)
            except queue.Empty:
                continue
            if kind == "log":
                print(data)
            elif kind == "job_update":
                job = data
                name = f"{job.meta.artist_line() or '?'} - {job.meta.title or job.meta.youtube_url}"
                if job.status == Status.DOWNLOADING:
                    pct = int(job.progress // 10) * 10
                    if last_pct.get(job.id) != pct:
                        last_pct[job.id] = pct
                        print(f"  ↓ {name}: {int(job.progress)}% {job.speed}")
                elif job.finished:
                    key = {Status.DONE: "ok", Status.SKIPPED: "skip", Status.ERROR: "err"}.get(job.status, "cancel")
                    stats[key] += 1
                    extra = f" — {job.error}" if job.error else ""
                    print(f"  [{job.status.value}] {name}{extra}")

    t = threading.Thread(target=printer, daemon=True)
    t.start()
    try:
        manager.wait_idle()
    except KeyboardInterrupt:
        print("\nCancelando...")
        manager.cancel_all()
        manager.wait_idle()
    stop.set()
    t.join(timeout=2)
    print(f"\nResumo: {stats['ok']} baixada(s), {stats['skip']} já existia(m), "
          f"{stats['err']} erro(s), {stats['cancel']} cancelada(s).")
    manager.shutdown()
    return 0 if stats["err"] == 0 else 2


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings.load()
    apply_args(settings, args)

    if args.gui or not (args.links or args.file):
        try:
            from .gui.app import run_gui
        except ImportError as e:
            print(f"Interface gráfica indisponível ({e}). Rode: pip install -r requirements.txt")
            return 1
        run_gui(settings)
        return 0
    return run_cli(settings, args)


if __name__ == "__main__":
    sys.exit(main())
