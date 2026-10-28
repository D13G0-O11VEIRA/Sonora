"""Funções utilitárias puras (sem rede) - fáceis de testar."""
from __future__ import annotations

import difflib
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

# ----------------------------------------------------------------- arquivos
_FS_REPLACE = {
    ":": " -", "/": "-", "\\": "-", "|": "-", '"': "'",
    "?": "", "*": "", "<": "", ">": "",
}
_RESERVED = {"CON", "PRN", "AUX", "NUL",
             *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def sanitize_filename(name: str, max_len: int = 150) -> str:
    """Remove caracteres inválidos no Windows/Linux/macOS."""
    name = name or ""
    for bad, good in _FS_REPLACE.items():
        name = name.replace(bad, good)
    name = re.sub(r"[\x00-\x1f]", "", name)
    name = re.sub(r"\s+", " ", name).strip().strip(".")
    if name.upper() in _RESERVED:
        name = f"_{name}"
    return name[:max_len].rstrip(" .")


def render_template(template: str, values: dict[str, str]) -> Path:
    """Preenche um modelo como '{artist}/{album}/{track} - {title}'.

    Cada valor é higienizado; partes vazias da pasta são descartadas e
    separadores soltos ("- Título") são limpos. Retorna caminho relativo SEM extensão.
    """
    def repl(m: re.Match) -> str:
        return sanitize_filename(str(values.get(m.group(1), "") or ""))

    filled = re.sub(r"\{(\w+)\}", repl, template.replace("\\", "/"))
    parts = []
    for part in filled.split("/"):
        part = re.sub(r"\(\s*\)|\[\s*\]", "", part)
        part = re.sub(r"^[\s\-–_.]+|[\s\-–_.]+$", "", part)
        part = re.sub(r"\s+-\s+-\s+", " - ", part)
        part = sanitize_filename(part)
        if part:
            parts.append(part)
    return Path(*parts) if parts else Path("faixa")


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for i in range(2, 1000):
        cand = path.with_name(f"{path.stem} ({i}){path.suffix}")
        if not cand.exists():
            return cand
    return path


# ------------------------------------------------------------- texto/título
_NOISE_WORDS = (
    r"official|oficial|lyrics?|letra|v[ií]deo|[aá]udio|audio|visuali[sz]er|hd|hq|4k|1080p|"
    r"clip|m/?v|remaster(?:ed)?(?:\s*\d{4})?|legendado|tradu[cç][aã]o|videoclipe?|full\s*album|"
    r"with\s+lyrics|lyric\s*video|explicit"
)
_BRACKET_NOISE = re.compile(
    r"\s*[\(\[\{][^\)\]\}]*\b(?:" + _NOISE_WORDS + r")\b[^\)\]\}]*[\)\]\}]", re.I
)
_TRAIL_NOISE = re.compile(
    r"\s*[-–|]\s*(?:official\s*)?(?:music\s*)?(?:lyric\s*)?(?:video|audio|visuali[sz]er)\s*$", re.I
)
_FEAT_BRACKET = re.compile(r"[\(\[]\s*(?:feat(?:uring)?\.?|ft\.?)\s+([^\)\]]+)[\)\]]", re.I)
_FEAT_TAIL = re.compile(r"\s+(?:feat(?:uring)?\.?|ft\.?)\s+(.+)$", re.I)


def clean_title(title: str) -> str:
    t = title or ""
    t = _BRACKET_NOISE.sub("", t)
    t = _TRAIL_NOISE.sub("", t)
    return re.sub(r"\s+", " ", t).strip(" -–|")


def split_names(s: str, split_ampersand: bool = True) -> list[str]:
    pattern = r",|;|\s+x\s+|\s+and\s+"
    if split_ampersand:
        pattern += r"|\s+&\s+"
    return [p.strip() for p in re.split(pattern, s or "", flags=re.I) if p.strip()]


def extract_featured(text: str) -> tuple[str, list[str]]:
    """'Song (feat. A & B)' -> ('Song', ['A', 'B'])"""
    feats: list[str] = []

    def grab(m: re.Match) -> str:
        feats.extend(split_names(m.group(1)))
        return ""

    text = _FEAT_BRACKET.sub(grab, text or "")
    text = _FEAT_TAIL.sub(grab, text)
    return re.sub(r"\s+", " ", text).strip(), feats


def clean_channel(name: str) -> str:
    n = re.sub(r"\s*-\s*Topic$", "", name or "", flags=re.I)
    n = re.sub(r"(?i)\s*(vevo|official|oficial)\s*$", "", n)
    return n.strip()


def dedupe(items: list[str]) -> list[str]:
    seen, out = set(), []
    for it in items:
        k = norm(it)
        if it and k not in seen:
            seen.add(k)
            out.append(it)
    return out


def guess_artist_title(raw_title: str, channel: str = "") -> tuple[str, str, list[str]]:
    """Heurística para títulos do YouTube: devolve (artista, título, participações)."""
    t = clean_title(raw_title)
    artist, song = "", t
    parts = re.split(r"\s+[-–—|]\s+", t, maxsplit=1)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        artist, song = parts[0].strip(), parts[1].strip()
    else:
        artist = clean_channel(channel)
    song, feats = extract_featured(song)
    artist, feats_a = extract_featured(artist)
    main = split_names(artist, split_ampersand=False)
    if len(main) > 1:                     # "A x B - Música" => A principal, B participante
        artist, feats_a = main[0], main[1:] + feats_a
    return artist, song, dedupe(feats_a + feats)


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"[\W_]+", " ", s).strip()


def similarity(a: str, b: str) -> float:
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return 0.0
    r = difflib.SequenceMatcher(None, na, nb).ratio()
    short, long_ = (na, nb) if len(na) <= len(nb) else (nb, na)
    if len(short) >= 6 and short in long_:
        r = max(r, 0.8)
    return r


# ------------------------------------------------------------- formatação
def fmt_duration(sec: float) -> str:
    sec = int(sec or 0)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_speed(bps: float | None) -> str:
    if not bps:
        return ""
    for unit in ("B/s", "KB/s", "MB/s", "GB/s"):
        if bps < 1024:
            return f"{bps:.1f} {unit}"
        bps /= 1024
    return f"{bps:.1f} TB/s"


def fmt_eta(sec: float | None) -> str:
    if sec is None:
        return ""
    m, s = divmod(int(sec), 60)
    return f"{m}:{s:02d}"


# ----------------------------------------------------------------- sistema
def find_ffmpeg() -> str | None:
    """ffmpeg do sistema ou o binário embutido do pacote imageio-ffmpeg."""
    path = shutil.which("ffmpeg")
    if path:
        return path
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def reveal_in_file_manager(path: Path) -> None:
    path = Path(path)
    try:
        if sys.platform.startswith("win"):
            if path.is_file():
                subprocess.Popen(["explorer", "/select,", str(path)])
            else:
                os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)] if path.is_file() else ["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path.parent if path.is_file() else path)])
    except Exception:
        pass
