# 🎧 Sonora — baixador de músicas com metadados completos

Cole um link do **YouTube, YouTube Music ou Spotify** (música, álbum ou playlist) e o Sonora baixa tudo em **MP3**, já com
**título, artistas participantes, álbum, ano, nº da faixa, gênero, capa e letra** gravados no arquivo — exatamente os campos
que o Windows mostra no painel *Detalhes* (como na sua captura de tela).

## Recursos

- **Interface moderna** (tema escuro/claro) com fila, miniaturas, progresso por faixa, cancelar e tentar de novo
- **Linha de comando** para automação: `python main.py "<link>"`
- Links de **vídeo, playlist, YouTube Music, Spotify (faixa/álbum/playlist)** — vários de uma vez, um por linha
- Também aceita texto livre: `Mother Mother - Burning Pile`
- **Tags ID3v2.3** (compatíveis com o Explorador do Windows): título, artistas, artista do álbum, álbum, ano, faixa/total,
  disco, gênero, ISRC, comentário com a origem, capa e **letra**
- **Completa metadados faltantes** (álbum, ano, gênero, nº da faixa, capa em alta) pelo iTunes Search — sem chave de API
- **Letras** via LRCLIB, opcionalmente também em `.lrc` sincronizado
- Para o Spotify, escolhe no YouTube a versão certa (compara duração, título, canal "Topic", penaliza ao vivo/cover/remix)
- **Playlists/álbuns** em subpasta própria + arquivo **.m3u8**
- **Biblioteca**: lista seus MP3 e abre um **editor de tags** (capa, letra, "buscar dados online", renomear pelo modelo)
- **Modelos de nome de arquivo** configuráveis (`{artist} - {title}`, `{artist}/{album}/{track} - {title}`…)
- Histórico em SQLite para **não baixar duas vezes**, downloads simultâneos (1–6), log em arquivo
- Botão para **atualizar o yt-dlp** (o YouTube muda sempre)

## Instalação (Windows + VS Code)

1. Instale o **Python 3.10+** (python.org — marque *Add Python to PATH*).
2. Abra esta pasta no VS Code (`File > Open Folder`).
3. No terminal do VS Code: `setup.bat` (cria `.venv` e instala as dependências).
4. Rode com **F5** (já há configurações em `.vscode/launch.json`) ou com `run.bat`.

Linux/macOS: `./setup.sh` e depois `./run.sh` (no Linux pode ser preciso `sudo apt install python3-tk`).

> **ffmpeg**: o Sonora usa o do sistema se existir; senão usa o binário embutido do pacote `imageio-ffmpeg`. Nada a configurar.
>
> **Deno (recomendado)**: o YouTube hoje exige um interpretador de JavaScript para resolver desafios.
> Instale com `winget install DenoLand.Deno` e mantenha o yt-dlp atualizado (botão em *Configurações*).

## Uso

**Interface:** `python main.py` → cole os links → *Adicionar à fila* (ou `Ctrl+Enter`).

**Terminal:**

```bash
python main.py "https://www.youtube.com/watch?v=XXXXXXXXXXX"
python main.py "https://open.spotify.com/track/..." "https://open.spotify.com/playlist/..."
python main.py "Mother Mother - Burning Pile" -b 320 -o "G:\JULIA\Musicas Julia"
python main.py -f links.txt -t "{artist}/{album}/{track} - {title}" -j 4
python main.py "<link>" --no-lyrics --no-online --force
```

## Como os metadados são montados

| Origem do link | Título / artistas / álbum | Capa | Áudio |
|---|---|---|---|
| **Spotify** | do próprio Spotify (API ou página pública) + iTunes p/ o que faltar | capa do Spotify | melhor correspondência no YouTube |
| **YouTube Music** | metadados oficiais da faixa (quando existem) + iTunes | iTunes → miniatura | o próprio vídeo |
| **YouTube comum** | deduzidos do título/canal ("Artista - Música", `feat.`, `x`) + iTunes | iTunes → miniatura | o próprio vídeo |

Os campos só são preenchidos quando há correspondência confiável (nome + artista + duração). Se não houver, ficam vazios
em vez de errados — e você pode corrigir na aba **Biblioteca** (duplo clique em qualquer música).

## Spotify: o que esperar

O Spotify protege seu áudio com DRM, então **nenhum programa baixa o áudio de lá**: o Sonora lê os *metadados* do Spotify e
baixa o áudio equivalente do YouTube.

- **Sem configuração**, links de faixa/álbum/playlist funcionam pela página pública do Spotify (playlists: até ~100 faixas).
- **Credenciais (opcional)** em *Configurações → Spotify*: crie um app em <https://developer.spotify.com/dashboard>.
  Desde fev/2026 o Spotify exige conta **Premium** para apps em *Development Mode* e só libera os itens de playlists
  **suas ou colaborativas** pela API. Por isso o Sonora tenta a API e, se ela recusar, **volta sozinho** para a página pública.
- Links de **artista** não são suportados (use álbuns/playlists).

## Qualidade do áudio

O YouTube entrega áudio em ~128–160 kbps (Opus/AAC). Escolher 320 kbps cria um MP3 compatível com seu player e com a
mesma qualidade da fonte — não melhora o som original.

## Problemas comuns

| Sintoma | Solução |
|---|---|
| "Sign in to confirm you're not a bot" / restrição de idade | *Configurações → Cookies do navegador* → escolha o navegador onde você está logado no YouTube |
| Erros de "signature" / "n challenge" / "JavaScript runtime" | Instale o Deno e clique em *Atualizar yt-dlp* |
| Spotify: "não consegui ler" | Veja `data/debug_spotify.json` (se existir) e a aba *Log*; use o link do YouTube como alternativa |
| Álbum/ano vazio | A faixa não foi achada com confiança no iTunes; preencha na Biblioteca (*Buscar dados online* ou manual) |
| Explorador do Windows não mostra a capa/artistas | Aguarde alguns segundos ou reabra a pasta; as tags são ID3v2.3 |

Logs: `data/sonora.log`. Configurações: `data/config.json`. Histórico: `data/history.db`.

## Estrutura (onde mexer)

```
main.py                    ponto de entrada (GUI ou CLI)
sonora/
  config.py                configurações e modelos de nome
  links.py                 reconhece o tipo de link
  resolvers/youtube.py     lê vídeos/playlists do YouTube (yt-dlp)
  resolvers/spotify.py     lê faixa/álbum/playlist do Spotify (API + página pública)
  matching.py              pontua resultados do YouTube p/ faixas do Spotify
  downloader.py            yt-dlp + ffmpeg → MP3
  enrich.py                iTunes (álbum/ano/gênero), LRCLIB (letras), capas
  tagger.py                leitura/escrita de tags ID3 (mutagen)
  manager.py               fila, threads, pipeline completo, .m3u8
  history.py               SQLite (evita duplicados)
  cli.py                   modo terminal
  gui/                     interface (app, configurações, biblioteca/editor, widgets)
tests/test_core.py         testes das partes puras  (python -m unittest discover -s tests -v)
```

Quer mudar o pipeline? Tudo passa por `Manager._process` em `sonora/manager.py`.

## Uso responsável

Use apenas com conteúdo que você tem direito de baixar (suas próprias músicas, conteúdo livre/Creative Commons, ou onde a
lei do seu país permitir cópia privada). Baixar vídeos pode violar os Termos de Serviço do YouTube e o Spotify proíbe
extrair seu áudio; a responsabilidade pelo uso é sua.
