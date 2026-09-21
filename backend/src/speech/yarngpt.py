"""Text-to-speech with YarnGPT: Nigerian-accented English, Yoruba, Igbo and Hausa.

Ported from AudioTokenizerV2 in https://github.com/saheedniyi02/yarngpt (commit 8bb0eb2) so it runs
outside Colab, with weights downloaded from Hugging Face at pinned revisions. Long text is spoken a
sentence at a time, like the news-reader example on the YarnGPT2b model card.
"""

import json
import math
import re
from pathlib import Path

import inflect
import numpy as np
import torch
import uroman
from huggingface_hub import hf_hub_download
from outetts.wav_tokenizer.decoder import WavTokenizer
from transformers import AutoModelForCausalLM, AutoTokenizer

MODEL_ID = "saheedniyi/YarnGPT2"
MODEL_REVISION = "bf654c72c1abaaa1aae4f1809be7c21708f4effe"

# WavTokenizer turns the model's audio codes (75 per second) into 24 kHz audio. YarnGPT was trained
# with wavtokenizer_large_speech_320_24k.ckpt, which was deleted from the repo's main branch in
# March 2025, so it is pinned to the commit that uploaded it.
CODEC_CONFIG = {
    "repo_id": "novateur/WavTokenizer-medium-speech-75token",
    "filename": "wavtokenizer_mediumdata_frame75_3s_nq1_code4096_dim512_kmeans200_attn.yaml",
    "revision": "8858552e69270816d6aeb37bfcf3b770769d4899",
}
CODEC_CHECKPOINT = {
    "repo_id": "novateur/WavTokenizer-large-speech-75token",
    "filename": "wavtokenizer_large_speech_320_24k.ckpt",
    "revision": "1cc9faee31025548fbae6ffe11115d7207093638",
}

SPEAKERS_DIR = Path(__file__).parent / "speakers"
VOICES = sorted(path.stem for path in SPEAKERS_DIR.glob("*.json"))
# The model card's top-ranked voice per language, except English: in side-by-side tests Whisper
# transcribed chinenye (ranked second) more accurately than idera.
DEFAULT_SPEAKERS = {"english": "chinenye", "yoruba": "yoruba_male2", "igbo": "igbo_female2", "hausa": "hausa_female1"}

# As in the news-reader example: generate at most 25 words at a time, and pause for half a second
# between sentences (code 453 decodes to silence).
MAX_WORDS_PER_GENERATION = 25
SENTENCE_PAUSE = [453] * 38
SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|\n+")


class YarnGPT:
    sample_rate = 24_000

    def __init__(self, device: str | None = None):
        self.device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))
        self.tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, revision=MODEL_REVISION)
        # The weights are bfloat16, as in YarnGPT's CUDA reference setup. Elsewhere use float32: bfloat16
        # is slower on CPUs without native support (e.g. Apple M1) and garbled some speech on MPS.
        dtype = "auto" if self.device.type == "cuda" else torch.float32
        self.model = AutoModelForCausalLM.from_pretrained(MODEL_ID, revision=MODEL_REVISION, dtype=dtype)
        self.model.to(self.device).eval()
        self.codec = WavTokenizer.from_pretrained0802(
            hf_hub_download(**CODEC_CONFIG), hf_hub_download(**CODEC_CHECKPOINT)
        ).to(self.device)
        self._inflect = inflect.engine()
        self._uroman = uroman.Uroman()

    @torch.inference_mode()
    def synthesize(self, text: str, lang: str = "english", speaker: str | None = None) -> np.ndarray:
        """Speak `text` in one of the voices in speakers/, returning mono float32 audio at 24 kHz."""
        if lang not in DEFAULT_SPEAKERS:
            raise ValueError(f"lang must be one of {list(DEFAULT_SPEAKERS)}, got {lang!r}")
        speaker = speaker or DEFAULT_SPEAKERS[lang]
        if speaker not in VOICES:
            raise ValueError(f"speaker must be one of {VOICES}, got {speaker!r}")

        # Each prompt starts with the voice's transcript and audio, so the model continues in that voice.
        voice = json.loads((SPEAKERS_DIR / f"{speaker}.json").read_text())
        voice_words = self._normalize(voice["text"])
        voice_audio = "\n".join(
            f"{w['word']}<|t_{float(w['duration']):.2f}|><|code_start|>"
            + "".join(f"<|{c}|>" for c in w["codes"])
            + "<|code_end|>"
            for w in voice["words"]
        )

        codes = []
        for sentence in SENTENCE_BREAK.split(text):
            words = self._normalize(sentence)
            if not words:
                continue
            if codes:
                codes += SENTENCE_PAUSE
            parts = math.ceil(len(words) / MAX_WORDS_PER_GENERATION)
            for i in range(parts):
                part = words[i * len(words) // parts : (i + 1) * len(words) // parts]
                prompt = (
                    f"<|im_start|>\n<|text_start|>{'<|text_sep|>'.join(voice_words + part)}<|text_end|>\n"
                    f"<|{lang}|>\n<|audio_start|>\n{voice_audio}"
                )
                codes += self._generate_codes(prompt)
        if not codes:
            raise ValueError(f"No speakable words in {text!r}")

        features = self.codec.codes_to_features(torch.tensor([[codes]], device=self.device))
        audio = self.codec.decode(features, bandwidth_id=torch.tensor([0], device=self.device))
        return audio[0].float().cpu().numpy()

    def _normalize(self, text: str) -> list[str]:
        """Romanize, spell out numbers and keep only lowercase words, as YarnGPT was trained."""
        text = self._uroman.romanize_string(text)
        text = re.sub(r"\d+(\.\d+)?", lambda m: self._inflect.number_to_words(m.group()), text.lower())
        text = re.sub(r"[-_/,.\\]", " ", text)
        text = re.sub(r"[^a-z\s]", "", text)
        return text.split()

    def _generate_codes(self, prompt: str) -> list[int]:
        """Generate the audio codes that continue `prompt`, up to the model's <|audio_end|>."""
        inputs = self.tokenizer(prompt, add_special_tokens=False, return_tensors="pt").to(self.device)
        # Greedy, like the Colab example: its temperature=0.1 is ignored because do_sample is off.
        output = self.model.generate(
            **inputs,
            do_sample=False,
            repetition_penalty=1.1,
            max_length=4000,
            eos_token_id=[self.tokenizer.convert_tokens_to_ids("<|audio_end|>"), self.tokenizer.eos_token_id],
        )
        new_text = self.tokenizer.decode(output[0, inputs.input_ids.shape[1] :])
        return [int(c) for c in re.findall(r"<\|(\d+)\|>", new_text)]
