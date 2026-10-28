"""Widgets reutilizáveis: carregador de miniaturas e linha de download."""
from __future__ import annotations

import io
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import customtkinter as ctk
from PIL import Image

from .. import enrich
from ..models import Job, Status

GREEN, RED, AMBER, GRAY = "#2fa572", "#e5484d", "#d9a441", "#8b8b8b"
STATUS_COLOR = {Status.DONE: GREEN, Status.ERROR: RED, Status.SKIPPED: AMBER, Status.CANCELLED: GRAY}
SUB_DEFAULT = ("gray35", "gray65")


class ThumbLoader:
    """Baixa miniaturas em threads; a criação da imagem do Tk acontece na thread da UI."""

    def __init__(self, ui_call: Callable[[Callable], None]):
        self._ui_call = ui_call
        self._pool = ThreadPoolExecutor(4, thread_name_prefix="thumb")
        self._cache: dict[str, Image.Image] = {}

    def load(self, url: str, callback: Callable[[Image.Image], None], size: int = 96) -> None:
        if url in self._cache:
            callback(self._cache[url])
            return

        def work() -> None:
            try:
                img = Image.open(io.BytesIO(enrich.fetch_bytes(url, timeout=10))).convert("RGB")
                w, h = img.size
                side = min(w, h)
                left, top = (w - side) // 2, (h - side) // 2
                img = img.crop((left, top, left + side, top + side)).resize((size, size))
                self._cache[url] = img
                self._ui_call(lambda: callback(img))
            except Exception:
                pass

        self._pool.submit(work)


class JobRow(ctk.CTkFrame):
    """Uma faixa na fila: miniatura, título, artistas • álbum, progresso e ação."""

    def __init__(self, master, job: Job, on_action: Callable[[str, Job], None], thumbs: ThumbLoader):
        super().__init__(master, corner_radius=10)
        self.job, self._on_action, self._thumbs = job, on_action, thumbs
        self._sig: tuple | None = None
        self._thumb_url = ""
        self._img = None

        self.grid_columnconfigure(1, weight=1)
        self.thumb = ctk.CTkLabel(
            self, text="♪", width=48, height=48, corner_radius=8,
            fg_color=("gray82", "gray25"), font=ctk.CTkFont(size=20),
        )
        self.thumb.grid(row=0, column=0, rowspan=3, padx=10, pady=8)

        self.title = ctk.CTkLabel(self, text="", anchor="w", font=ctk.CTkFont(size=14, weight="bold"))
        self.title.grid(row=0, column=1, sticky="ew", pady=(8, 0))
        self.sub = ctk.CTkLabel(self, text="", anchor="w", font=ctk.CTkFont(size=12), text_color=SUB_DEFAULT)
        self.sub.grid(row=1, column=1, sticky="ew")
        self.bar = ctk.CTkProgressBar(self, height=8)
        self.bar.set(0)
        self.bar.grid(row=2, column=1, sticky="ew", pady=(4, 10), padx=(0, 12))

        self.status = ctk.CTkLabel(self, text="", width=200, anchor="e")
        self.status.grid(row=0, column=2, rowspan=3, padx=(0, 8))
        self.btn = ctk.CTkButton(self, text="Cancelar", width=110, command=self._clicked)
        self.btn.grid(row=0, column=3, rowspan=3, padx=(0, 12))
        self.refresh()

    # ------------------------------------------------------------------
    def _clicked(self) -> None:
        j = self.job
        if j.active:
            self.btn.configure(state="disabled", text="Cancelando…")
            self._on_action("cancel", j)
        elif j.status in (Status.ERROR, Status.CANCELLED):
            self._on_action("retry", j)
        elif j.output_path:
            self._on_action("reveal", j)

    def _set_thumb(self, pil: Image.Image) -> None:
        try:
            self._img = ctk.CTkImage(light_image=pil, dark_image=pil, size=(48, 48))
            self.thumb.configure(image=self._img, text="")
        except Exception:
            pass  # a linha pode ter sido removida antes de a imagem chegar

    def refresh(self) -> None:
        j, m = self.job, self.job.meta
        pct = int(j.progress)
        sig = (j.status, pct, j.speed, m.title, tuple(m.artists), m.album, j.error)
        if sig == self._sig:
            return
        self._sig = sig

        self.title.configure(text=m.title or m.youtube_url or m.source_url or "…")
        sub = " • ".join(p for p in (m.artist_line(), m.album) if p)
        if j.status == Status.ERROR and j.error:
            self.sub.configure(text=j.error, text_color=RED)
        else:
            self.sub.configure(text=sub or m.source_url, text_color=SUB_DEFAULT)

        color = STATUS_COLOR.get(j.status)
        if j.status == Status.DOWNLOADING:
            label = f"Baixando {pct}%  {j.speed}".strip()
        else:
            label = j.status.value
        self.status.configure(text=label, text_color=color or ("gray10", "gray90"))
        self.bar.configure(progress_color=color or ("#3b8ed0", "#1f6aa5"))
        self.bar.set(1.0 if j.status in (Status.DONE, Status.SKIPPED) else j.progress / 100)

        if j.active:
            if self.btn.cget("text") != "Cancelando…":
                self.btn.configure(text="Cancelar", state="normal")
        elif j.status in (Status.ERROR, Status.CANCELLED):
            self.btn.configure(text="Tentar de novo", state="normal")
        else:
            self.btn.configure(text="Mostrar", state="normal" if j.output_path else "disabled")

        url = m.cover_url or m.thumb_url
        if url and url != self._thumb_url:
            self._thumb_url = url
            self._thumbs.load(url, self._set_thumb)
