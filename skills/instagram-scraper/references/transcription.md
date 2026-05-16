# Reference: Reel transcription via `whisper.cpp`

The `--transcribe` flag on `scrape_profile.py` (or running `transcribe_videos.py` directly)
adds spoken-content transcripts to every Instagram Reel in a profile JSON. Local pipeline,
zero per-call cost after a one-time setup.

## Pipeline

```
videoUrl (Apify) → mp4 download → ffmpeg → 16 kHz mono mp3 → whisper-cli → transcript text
                                                                   ↓
                                                  back into latestPosts[i].transcript
```

## One-time setup

```bash
# 1. Binaries
brew install whisper-cpp ffmpeg

# 2. Model — medium.en is the default (English-optimized, balanced size/quality)
mkdir -p ~/.local/share/whisper-cpp/models
curl -L -o ~/.local/share/whisper-cpp/models/ggml-medium.en.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-medium.en.bin

# 3. Smoke test
whisper-cli -m ~/.local/share/whisper-cpp/models/ggml-medium.en.bin \
  -f /opt/homebrew/Cellar/whisper-cpp/*/share/whisper-cpp/jfk.wav -nt
# expected: "And so my fellow Americans, ask not what your country can do for you, ..."
```

## Model choices — trade-offs

| # | Model | Size | Languages | Speed (M-chip) | When to use |
|---|---|---|---|---|---|
| 1 | `tiny.en` / `tiny` | 75 MB | EN-only / multi | very fast | rough drafts, smoke tests |
| 2 | `base.en` / `base` | 150 MB | EN / multi | fast | clean English speech, low-stakes |
| 3 | `small.en` / `small` | 500 MB | EN / multi | medium | good baseline |
| 4 | **`medium.en`** | **1.5 GB** | **EN** | **medium-slow** | **Default — Chase AI / English Reels** |
| 5 | `medium` | 1.5 GB | multi | medium-slow | German/multilingual creators |
| 6 | `large-v3` | 3 GB | multi | slow | accents, noisy audio, max quality |
| 7 | `large-v3-turbo` | 1.5 GB | multi | medium | "best quality / fast" sweet spot — try if `medium.en` fails |

Pass non-default models via `--whisper-model <name>` to `scrape_profile.py` (or
`transcribe_videos.py`). The script resolves to `~/.local/share/whisper-cpp/models/ggml-<name>.bin`,
so make sure the matching `.bin` is downloaded before running.

## ffmpeg-Command — warum diese Settings

```bash
ffmpeg -i video.mp4 -vn -ar 16000 -ac 1 -c:a libmp3lame -q:a 9 audio.mp3
```

| Flag | Bedeutung | Warum |
|---|---|---|
| `-vn` | drop video stream | Whisper braucht nur Audio |
| `-ar 16000` | 16 kHz Sample-Rate | Whisper-trainierte Default-Rate |
| `-ac 1` | mono | Reels sind eh meist mono Speech, halbiert Filegröße |
| `-c:a libmp3lame -q:a 9` | mp3 niedrige Bitrate | Speech-only — niedrige Bitrate spart Filesize ohne ASR-Qualitätsverlust |

Resultat: ~30-Sekunden-Reel landet bei ~80 KB statt 5–15 MB Original-Video. Schneller
Transfer, kleiner Whisper-Input, identische Transkriptions-Qualität.

## Pricing-Vergleich (warum lokal)

| Option | Kosten pro 12-Reel-Profil (~12 min Audio) | Setup |
|---|---|---|
| OpenAI Whisper API (`whisper-1`) | $0.072 | API-Key, 1 Zeile Code |
| **Lokaler whisper.cpp + medium.en** | **$0** | brew install + 1.5 GB Modell |
| Replicate / AssemblyAI | ~$0.05–0.10 | API-Key |

Lokale Pipeline gewinnt bei laufender Nutzung (5+ Profile/Woche → Setup-Aufwand amortisiert).

## Performance (M-Chip)

Grob 1× Echtzeit für `medium.en` auf Apple Silicon — d.h. 30-Sekunden-Reel braucht ~30 Sekunden
Wall-Clock. 12 Reels eines Profils ≈ 6 Minuten Gesamtzeit. Seriell ist OK; Parallelisierung
würde Speed verdreifachen aber Setup verkomplizieren — Skill bleibt seriell.

## Error-Handling im Skript

`transcribe_videos.py` arbeitet best-effort:

- Reel ohne `videoUrl` → skip + Warnung, weitere Reels werden trotzdem versucht
- Whisper / ffmpeg / Download-Fehler pro Reel → in `failed[]` reported, keine Abbruch
- JSON wird nur am Ende geschrieben, mutiert die Datei in-place
- Tempfiles im Systemtemp, automatisch geräumt nach jedem Reel (~10 MB Peak pro File)

## Idempotenz

- `--skip-existing` (Default beim Aufruf aus `scrape_profile.py`): Reels mit bereits gefülltem
  `transcript`-Feld werden nicht neu transkribiert.
- Ohne den Flag: jedes Re-Run überschreibt bestehende Transkripte.
- Nach erfolgreicher Transkription stehen drei Felder im JSON: `transcript`, `transcript_model`,
  `transcribed_at`. Renderer liest die Felder, kein extra State.

## Fallback auf Cloud-API

Wenn whisper.cpp lokal nicht verfügbar ist und Setup zu aufwendig: `transcribe_videos.py`
müsste angepasst werden, um stattdessen die OpenAI-Whisper-API zu nutzen. Aktuell **nicht**
implementiert — bleibt als Erweiterung. Der Modell-Resolve und der Whisper-Command-Block
wären die einzigen zu ersetzenden Stellen.
