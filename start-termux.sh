#!/data/data/com.termux/files/usr/bin/bash
# Abre o Sonora no Android. A interface aparece no navegador do celular.
cd "$(dirname "$0")"
termux-wake-lock 2>/dev/null || true     # mantém o Termux ativo com a tela apagada
exec python main.py --web "$@"
