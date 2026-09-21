"""Command line: `uv run python -m src.speech "Good morning, how are you?" -o hello.wav`."""

import argparse

import soundfile as sf

from .yarngpt import DEFAULT_SPEAKERS, VOICES, YarnGPT


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m src.speech", description="Text-to-speech with YarnGPT")
    parser.add_argument("text")
    parser.add_argument("-o", "--output", default="speech.wav", help="WAV file to write (default: speech.wav)")
    parser.add_argument("--lang", default="english", choices=list(DEFAULT_SPEAKERS))
    parser.add_argument("--speaker", choices=VOICES, metavar="VOICE", help="a voice from src/speech/speakers/")
    parser.add_argument("--device", help="torch device such as cpu, mps or cuda (default: cuda if available, else cpu)")
    args = parser.parse_args()

    audio = YarnGPT(device=args.device).synthesize(args.text, args.lang, args.speaker)
    sf.write(args.output, audio, YarnGPT.sample_rate)
    print(f"Wrote {args.output} ({len(audio) / YarnGPT.sample_rate:.1f} seconds)")


if __name__ == "__main__":
    main()
