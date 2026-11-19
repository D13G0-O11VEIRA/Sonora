#!/data/data/com.termux/files/usr/bin/bash
# Instalação do Sonora no Android (Termux). Rode uma única vez:  bash install-termux.sh
set -e
echo "==> Atualizando pacotes do Termux"
pkg update -y
pkg install -y python ffmpeg python-pillow nodejs termux-api unzip

echo "==> Permissão de armazenamento (aceite o pedido que vai aparecer na tela)"
termux-setup-storage || true
sleep 2

echo "==> Instalando dependências Python"
pip install -U yt-dlp mutagen requests
pip install -U yt-dlp-ejs || true

mkdir -p "$HOME/storage/music/Sonora" 2>/dev/null || true
echo
echo "Pronto! Para abrir o Sonora:  bash start-termux.sh"
