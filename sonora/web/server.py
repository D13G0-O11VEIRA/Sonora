"""Servidor web local do Sonora (somente biblioteca padrão do Python).

Pensado para o Android/Termux: o Sonora roda no celular e a interface abre no
navegador em http://127.0.0.1:8765 (pode ser instalada na tela inicial como app).
Também funciona em qualquer outro sistema:  python main.py --web
"""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
import webbrowser
from collections import deque
from dataclasses import asdict, fields
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, quote, urlparse

from .. import APP_NAME, __version__
from ..config import BITRATES, Settings
from ..texts import CAPPED_HELP

if TYPE_CHECKING:  # o Manager só é importado em serve(): o servidor em si é testável sem yt-dlp/mutagen
    from ..manager import Manager

STATIC_DIR = Path(__file__).resolve().parent / "static"
INDEX_FILE = Path(__file__).resolve().parent / "index.html"
STATIC_TYPES = {".png": "image/png", ".svg": "image/svg+xml", ".js": "text/javascript", ".json": "application/json"}
MAX_BODY = 64 * 1024

# Campos que a tela de Ajustes pode ler/gravar (o tema fica de fora: a página segue o sistema).
EDITABLE = [f.name for f in fields(Settings) if f.name != "theme"]
SECRET_FIELDS = {"spotify_client_secret"}


# ----------------------------------------------------------------- lógica (testável)
def settings_to_dict(settings: Settings) -> dict:
    data = {k: v for k, v in asdict(settings).items() if k in EDITABLE}
    data["bitrates"] = BITRATES
    return data


def apply_settings_dict(settings: Settings, data: dict) -> list[str]:
    """Valida e aplica os campos recebidos. Retorna os nomes alterados."""
    changed = []
    for f in fields(Settings):
        if f.name not in EDITABLE or f.name not in data:
            continue
        cur, raw = getattr(settings, f.name), data[f.name]
        try:
            if isinstance(cur, bool):
                val = bool(raw)
            elif isinstance(cur, int):
                val = int(raw)
            else:
                val = str(raw)
        except (TypeError, ValueError):
            continue
        if f.name == "bitrate" and val not in BITRATES:
            continue
        if f.name == "concurrent_downloads":
            val = max(1, min(6, val))
        if f.name == "batch_size":
            val = max(0, min(1000, val))
        if f.name == "artist_separator" and not val:
            val = "; "
        if f.name == "filename_template" and not val.strip():
            val = "{artist} - {title}"
        if f.name in ("js_runtime",) and val not in ("", "deno", "node", "bun", "quickjs"):
            continue
        if val != cur:
            setattr(settings, f.name, val)
            changed.append(f.name)
    return changed


def job_to_dict(job) -> dict:
    m = job.meta
    return {
        "id": job.id,
        "title": m.title or m.youtube_url or m.source_url or "…",
        "artists": ", ".join(m.artists),
        "album": m.album,
        "status": job.status.value,
        "code": job.status.name.lower(),
        "progress": round(job.progress, 1),
        "speed": job.speed,
        "error": job.error,
        "cover": m.cover_url or m.thumb_url,
        "active": job.active,
        "file": job.output_path.name if job.output_path else "",
    }


# ----------------------------------------------------------------- servidor
class SonoraServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, manager: Manager):
        super().__init__(addr, Handler)
        self.manager = manager
        self.log_lines: deque[str] = deque(maxlen=80)
        self.notice = ""
        self.notice_at = 0.0
        self._lib_cache: tuple[float, list] = (0.0, [])
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        """Consome os eventos do Manager (log/avisos); o estado dos jobs é lido ao vivo."""
        while True:
            kind, data = self.manager.events.get()
            if kind == "log":
                self.log_lines.append(data)
            elif kind == "notice":
                self.notice, self.notice_at = data, time.time()
            elif kind == "idle":
                self.notice, self.notice_at = "Tudo pronto!", time.time()

    def state(self) -> dict:
        jobs = [job_to_dict(j) for j in list(self.manager.jobs.values())]
        return {
            "jobs": jobs,
            "active": sum(1 for j in jobs if j["active"]),
            "notice": self.notice if time.time() - self.notice_at < 20 else "",
            "log": list(self.log_lines)[-40:],
            "batches": getattr(self.manager, "batch_info", lambda: [])(),
            "capped_help": CAPPED_HELP,
            "version": __version__,
        }

    def library(self) -> list[dict]:
        ts, cached = self._lib_cache
        if time.time() - ts < 15:
            return cached
        from ..tagger import read_tags  # importado só aqui (precisa do mutagen)

        folder = self.manager.settings.out_path
        rows = []
        if folder.exists():
            for p in sorted(folder.rglob("*.mp3"))[:3000]:
                t = read_tags(p)
                rows.append({"title": t["title"] or p.stem, "artist": t["artist"], "album": t["album"],
                             "year": t["year"], "duration": round(t["duration"]), "cover": t["has_cover"]})
        self._lib_cache = (time.time(), rows)
        return rows


class Handler(BaseHTTPRequestHandler):
    server: SonoraServer
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # silencia o log padrão
        pass

    # ---- respostas
    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _redirect(self, location: str) -> None:
        self._send(302, b"", "text/plain", {"Location": location})

    # ---- segurança: impede que OUTROS sites abertos no navegador falem com este servidor
    def _same_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        return urlparse(origin).netloc == self.headers.get("Host", "")

    # ---- GET
    def do_GET(self) -> None:
        url = urlparse(self.path)
        path = url.path
        if not self._same_origin():
            return self._json({"error": "origem não permitida"}, 403)

        if path in ("/", "/index.html"):
            return self._send(200, INDEX_FILE.read_bytes(), "text/html; charset=utf-8")
        if path == "/manifest.json":
            return self._json(self._manifest())
        if path == "/sw.js":
            js = b"self.addEventListener('fetch', () => {});"
            return self._send(200, js, "text/javascript")
        if path.startswith("/static/"):
            f = (STATIC_DIR / path[len("/static/"):]).resolve()
            if f.parent == STATIC_DIR.resolve() and f.is_file() and f.suffix in STATIC_TYPES:
                return self._send(200, f.read_bytes(), STATIC_TYPES[f.suffix])
            return self._json({"error": "não encontrado"}, 404)
        if path == "/share":
            # Recebe links do menu "Compartilhar" do Android. NÃO baixa sozinho: só preenche a tela.
            q = parse_qs(url.query)
            text = " ".join(q.get(k, [""])[0] for k in ("title", "text", "url")).strip()
            return self._redirect("/?text=" + quote(text))
        if path == "/api/state":
            return self._json(self.server.state())
        if path == "/api/settings":
            data = settings_to_dict(self.server.manager.settings)
            for k in SECRET_FIELDS:                       # não devolve segredos; só avisa que existem
                data[k + "_set"] = bool(data.pop(k, ""))
            return self._json(data)
        if path == "/api/library":
            try:
                return self._json({"items": self.server.library(), "folder": str(self.server.manager.settings.out_path)})
            except Exception as e:
                return self._json({"items": [], "error": str(e)})
        self._json({"error": "não encontrado"}, 404)

    # ---- POST
    def do_POST(self) -> None:
        if not self._same_origin():
            return self._json({"error": "origem não permitida"}, 403)
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            return self._json({"error": "use application/json"}, 415)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self._json({"error": "corpo muito grande"}, 413)
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, OSError):
            return self._json({"error": "JSON inválido"}, 400)

        mgr, path = self.server.manager, urlparse(self.path).path
        jobs = mgr.jobs

        if path == "/api/add":
            n = mgr.add_links(str(body.get("text", "")))
            return self._json({"added": n})
        if path in ("/api/cancel", "/api/retry"):
            job = jobs.get(int(body.get("id", 0) or 0))
            if not job:
                return self._json({"error": "faixa não encontrada"}, 404)
            (mgr.cancel if path.endswith("cancel") else mgr.retry)(job)
            return self._json({"ok": True})
        if path == "/api/cancel_all":
            mgr.cancel_all()
            return self._json({"ok": True})
        if path in ("/api/continue", "/api/dismiss"):
            bid = int(body.get("id", 0) or 0)
            if path.endswith("continue"):
                return self._json({"started": mgr.continue_batch(bid)})
            mgr.dismiss_batch(bid)
            return self._json({"ok": True})
        if path == "/api/clear":
            return self._json({"cleared": len(mgr.forget_finished())})
        if path == "/api/settings":
            s = mgr.settings
            body = dict(body)
            if not str(body.get("spotify_client_secret", "")).strip():
                body.pop("spotify_client_secret", None)   # campo vazio = manter o atual
            changed = apply_settings_dict(s, body)
            s.save()
            mgr.apply_settings()
            self.server._lib_cache = (0.0, [])
            return self._json({"changed": changed})
        if path == "/api/update_ytdlp":
            from ..downloader import update_ytdlp

            threading.Thread(target=lambda: update_ytdlp(mgr.log), daemon=True).start()
            return self._json({"ok": True})
        self._json({"error": "não encontrado"}, 404)

    @staticmethod
    def _manifest() -> dict:
        return {
            "name": APP_NAME, "short_name": APP_NAME, "lang": "pt-BR",
            "start_url": "/", "scope": "/", "display": "standalone",
            "background_color": "#141a3a", "theme_color": "#141a3a",
            "icons": [
                {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
                {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
            ],
            # aparece no menu "Compartilhar" do Android depois de instalar o app
            "share_target": {"action": "/share", "method": "GET",
                             "params": {"title": "title", "text": "text", "url": "url"}},
        }


def _open_browser(url: str) -> None:
    for exe in ("termux-open-url", "xdg-open"):
        path = shutil.which(exe)
        if path:
            try:
                subprocess.Popen([path, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
            except OSError:
                pass
    webbrowser.open(url)


def serve(settings: Settings, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> int:
    from ..manager import Manager

    manager = Manager(settings)
    try:
        httpd = SonoraServer((host, port), manager)
    except OSError as e:
        print(f"Não foi possível abrir a porta {port}: {e}\nTente outra: python main.py --web --port 8800")
        return 1
    url = f"http://127.0.0.1:{port}"
    print(f"\n  {APP_NAME} {__version__} no ar: {url}")
    print(f"  Músicas em: {settings.out_path}")
    if host not in ("127.0.0.1", "localhost"):
        print("  ATENÇÃO: a interface está acessível na rede e não tem senha. Use só em Wi-Fi confiável.")
    print("  Pressione Ctrl+C para encerrar.\n")
    if open_browser:
        _open_browser(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nEncerrando…")
    finally:
        manager.shutdown()
        httpd.server_close()
    return 0
