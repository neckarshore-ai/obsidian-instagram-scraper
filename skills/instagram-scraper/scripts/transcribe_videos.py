#!/usr/bin/env python3
"""Transcribe Instagram Reel videos referenced in a scraped raw.json file.

Pipeline per Reel (productType == 'clips' with a videoUrl):
  1. Download the .mp4 to a tempfile
  2. ffmpeg → 16 kHz mono mp3 (small + Whisper-friendly)
  3. whisper-cli (whisper.cpp) → transcript text
  4. Mutate the JSON in place: latestPosts[i].transcript / .transcript_model / .transcribed_at

Usage:
  transcribe_videos.py --input <raw.json>                      # default model: medium.en
  transcribe_videos.py --input <raw.json> --model medium       # multilingual variant
  transcribe_videos.py --input <raw.json> --skip-existing      # don't re-transcribe Reels that already have a transcript

Requires (system):
  - ffmpeg on $PATH (brew install ffmpeg)
  - whisper-cli on $PATH (brew install whisper-cpp)
  - A Whisper model at ~/.local/share/whisper-cpp/models/ggml-<model>.bin
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
except ImportError:
    sys.stderr.write(
        "ERROR: 'requests' is not installed. Run:\n"
        "  pip install -r " + str(Path(__file__).parent.parent / "requirements.txt") + "\n"
    )
    sys.exit(2)

REEL_PRODUCT_TYPE = "clips"
DEFAULT_MODEL = "medium.en"
MODEL_DIR = Path.home() / ".local" / "share" / "whisper-cpp" / "models"
DOWNLOAD_TIMEOUT_SECONDS = 120
DOWNLOAD_CHUNK_BYTES = 1024 * 1024  # 1 MB


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument("--input", type=Path, required=True, help="Path to a raw.json from scrape_profile.py")
    p.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=(
            f"Whisper model name (default: {DEFAULT_MODEL}). "
            "Resolves to ~/.local/share/whisper-cpp/models/ggml-<MODEL>.bin"
        ),
    )
    p.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip Reels that already have a non-empty 'transcript' field.",
    )
    p.add_argument(
        "--whisper-bin",
        default="whisper-cli",
        help="Path to the whisper-cli binary (default: whisper-cli on $PATH).",
    )
    p.add_argument(
        "--ffmpeg-bin",
        default="ffmpeg",
        help="Path to the ffmpeg binary (default: ffmpeg on $PATH).",
    )
    return p.parse_args()


def require_binary(name: str, hint: str) -> None:
    if shutil.which(name) is None:
        sys.stderr.write(f"ERROR: '{name}' not found on $PATH. {hint}\n")
        sys.exit(2)


def resolve_model_path(model: str) -> Path:
    candidate = MODEL_DIR / f"ggml-{model}.bin"
    if not candidate.exists():
        sys.stderr.write(
            f"ERROR: Whisper model not found at {candidate}.\n"
            f"Download it with:\n"
            f"  curl -L -o {candidate} https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-{model}.bin\n"
        )
        sys.exit(2)
    return candidate


def download_video(url: str, dest: Path) -> None:
    with requests.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT_SECONDS) as resp:
        resp.raise_for_status()
        with dest.open("wb") as f:
            for chunk in resp.iter_content(chunk_size=DOWNLOAD_CHUNK_BYTES):
                if chunk:
                    f.write(chunk)


def extract_audio(ffmpeg_bin: str, video_path: Path, audio_path: Path) -> None:
    """Extract audio as 16 kHz mono mp3 — optimal for Whisper, small payload."""
    subprocess.run(
        [
            ffmpeg_bin,
            "-y",
            "-loglevel", "error",
            "-i", str(video_path),
            "-vn",
            "-ar", "16000",
            "-ac", "1",
            "-c:a", "libmp3lame",
            "-q:a", "9",
            str(audio_path),
        ],
        check=True,
    )


def run_whisper(whisper_bin: str, model_path: Path, audio_path: Path) -> str:
    """Run whisper-cli on an audio file, return the transcript text."""
    out_prefix = audio_path.with_suffix("")  # strips .mp3
    subprocess.run(
        [
            whisper_bin,
            "-m", str(model_path),
            "-f", str(audio_path),
            "-otxt",
            "-of", str(out_prefix),
            "-nt",  # no timestamps in the .txt output
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    txt_path = out_prefix.with_suffix(".txt")
    if not txt_path.exists():
        raise RuntimeError(f"whisper-cli did not produce {txt_path}")
    return txt_path.read_text(encoding="utf-8").strip()


def transcribe_one(
    post: dict,
    *,
    workdir: Path,
    whisper_bin: str,
    ffmpeg_bin: str,
    model_path: Path,
) -> str:
    video_url = post.get("videoUrl")
    if not video_url:
        raise ValueError("post has no videoUrl")
    shortcode = post.get("shortCode") or post.get("id") or "unknown"
    video_path = workdir / f"{shortcode}.mp4"
    audio_path = workdir / f"{shortcode}.mp3"
    download_video(video_url, video_path)
    extract_audio(ffmpeg_bin, video_path, audio_path)
    transcript = run_whisper(whisper_bin, model_path, audio_path)
    # cleanup tempfiles immediately (don't accumulate ~10MB per Reel)
    video_path.unlink(missing_ok=True)
    audio_path.unlink(missing_ok=True)
    audio_path.with_suffix(".txt").unlink(missing_ok=True)
    return transcript


def main() -> int:
    args = parse_args()

    require_binary(args.ffmpeg_bin, "Install with: brew install ffmpeg")
    require_binary(args.whisper_bin, "Install with: brew install whisper-cpp")
    model_path = resolve_model_path(args.model)

    if not args.input.exists():
        sys.stderr.write(f"ERROR: input file not found: {args.input}\n")
        return 2

    profile = json.loads(args.input.read_text(encoding="utf-8"))
    posts = profile.get("latestPosts", [])
    if not isinstance(posts, list):
        sys.stderr.write("ERROR: latestPosts is not a list — is this a profile-details JSON?\n")
        return 2

    reels = [
        (i, p)
        for i, p in enumerate(posts)
        if p.get("productType") == REEL_PRODUCT_TYPE and p.get("videoUrl")
    ]

    if not reels:
        sys.stderr.write(f"INFO: no transcribable Reels in {args.input}\n")
        print(json.dumps({"transcribed": 0, "skipped": 0, "failed": 0}, ensure_ascii=False))
        return 0

    sys.stderr.write(f"INFO: {len(reels)} Reel(s) to consider in {args.input.name}\n")

    transcribed = 0
    skipped = 0
    failed: list[dict] = []

    with tempfile.TemporaryDirectory(prefix="ig-transcribe-") as tmpdir:
        workdir = Path(tmpdir)
        for idx, (post_index, post) in enumerate(reels, start=1):
            shortcode = post.get("shortCode") or f"#{post_index}"
            if args.skip_existing and post.get("transcript"):
                sys.stderr.write(f"  [{idx}/{len(reels)}] {shortcode}: skip (already transcribed)\n")
                skipped += 1
                continue
            sys.stderr.write(f"  [{idx}/{len(reels)}] {shortcode}: transcribing ...\n")
            try:
                transcript = transcribe_one(
                    post,
                    workdir=workdir,
                    whisper_bin=args.whisper_bin,
                    ffmpeg_bin=args.ffmpeg_bin,
                    model_path=model_path,
                )
            except Exception as exc:  # broad on purpose — keep going on per-Reel errors
                sys.stderr.write(f"    ERROR: {exc}\n")
                failed.append({"shortcode": shortcode, "reason": str(exc)[:200]})
                continue
            post["transcript"] = transcript
            post["transcript_model"] = args.model
            post["transcribed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            transcribed += 1

    # Persist the mutated JSON
    args.input.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {
                "input": str(args.input),
                "transcribed": transcribed,
                "skipped": skipped,
                "failed": failed,
                "model": args.model,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
