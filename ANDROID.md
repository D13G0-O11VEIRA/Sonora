# 📱 Sonora no Android

O Sonora roda no celular dentro do **Termux** (um terminal Linux para Android) e a interface abre no **navegador do próprio
aparelho**, feita para tela de toque. As músicas são salvas em **Música/Sonora** e aparecem no seu player.

> Isto **não é um APK**. O ffmpeg (que converte para MP3) e o yt-dlp funcionam de forma confiável no Termux, e é a forma de
> ter o app completo no celular sem depender de um build de APK. Se quiser um APK de verdade, veja a seção final.

## Instalação (uma vez, ~5 minutos)

1. Instale o **Termux pela F-Droid** (<https://f-droid.org/packages/com.termux/>). A versão da Play Store está desatualizada e não funciona.
2. Opcional, mas recomendado: instale também o **Termux:API** pela F-Droid (permite que as músicas apareçam na hora no player).
3. Copie o `sonora.zip` para a pasta **Download** do celular.
4. Abra o Termux e rode:

```bash
termux-setup-storage          # aceite a permissão que aparecer
pkg install -y unzip
unzip ~/storage/downloads/sonora.zip -d ~/
cd ~/sonora
bash install-termux.sh
```

## Usar

```bash
cd ~/sonora
bash start-termux.sh
```

O navegador abre em `http://127.0.0.1:8765`. Cole um link e toque em **Baixar**. Deixe o Termux aberto em segundo plano
(o script já pede para o Android não suspender o Termux; se o sistema matar o app, desative a otimização de bateria do Termux).

### Compartilhar direto do YouTube/Spotify

1. No Chrome, abra `http://127.0.0.1:8765`, menu ⋮ → **Instalar app** (ou *Adicionar à tela inicial*).
2. Nos apps do YouTube/Spotify use **Compartilhar** → **Sonora**. O link chega preenchido; é só tocar em **Baixar**.

Se a opção de compartilhar não aparecer no seu aparelho, cole o link manualmente — o resto funciona igual.

## Atalho na tela inicial (opcional)

Instale o **Termux:Widget** (F-Droid) e crie o atalho:

```bash
mkdir -p ~/.shortcuts
printf '#!/data/data/com.termux/files/usr/bin/bash\ncd ~/sonora && bash start-termux.sh --no-open\n' > ~/.shortcuts/Sonora.sh
chmod +x ~/.shortcuts/Sonora.sh
```

## O que é diferente do PC

| Assunto | No Android |
|---|---|
| Pasta padrão | `~/storage/music/Sonora` = **Música/Sonora** do celular |
| Interpretador JavaScript (exigido pelo YouTube) | usa o **Node.js** instalado pelo script (o Deno não existe para Termux) |
| Cookies do navegador | indisponível; se o YouTube pedir login, exporte um `cookies.txt` e informe o caminho em *Ajustes* |
| Downloads simultâneos | padrão 2 (celular esquenta e gasta bateria) |
| Interface desktop (janelas) | não é usada; só a web |

## Problemas comuns

| Sintoma | Solução |
|---|---|
| "Sem conexão com o Sonora" na página | O Termux foi encerrado. Abra-o e rode `bash start-termux.sh` de novo |
| Música baixada não aparece no player | Instale o Termux:API, ou abra o app de arquivos e atualize a pasta Música/Sonora |
| Erro "signature" / "n challenge" | Em *Ajustes* toque em **Atualizar yt-dlp** e reinicie |
| "Sign in to confirm you're not a bot" | Use um `cookies.txt` (veja acima) |
| `pip` reclama de pacote do sistema | Rode `pip install -U yt-dlp mutagen requests --break-system-packages` |
| Porta ocupada | `bash start-termux.sh --port 8800` |

## Também funciona só pelo terminal do Termux

```bash
python main.py "https://www.youtube.com/watch?v=XXXXXXXXXXX"
python main.py "Mother Mother - Burning Pile" -b 320
```

## Usar a interface web no PC

`python main.py --web` abre a mesma interface no navegador do computador. Por padrão ela só aceita conexões do próprio
aparelho. `--host 0.0.0.0` libera acesso pela rede, **sem senha**: use só em Wi-Fi de confiança.

## E um APK de verdade?

Um APK exigiria empacotar Python + yt-dlp + ffmpeg para Android (Kivy/Buildozer ou um app nativo com a biblioteca
youtubedl-android) e compilar em Linux com o SDK do Android. É um projeto à parte, e eu não consigo compilar nem testar isso
aqui. A interface web deste projeto já está pronta para ser reaproveitada nesse caminho, caso você queira seguir adiante.
