# The listening helpers: what they use

Everything runs on your own Mac. Audio never leaves it and is never saved, except the short clips you record when you
teach Jarvis your voice, which are deleted as soon as the model is made.

Nothing below is stored in this repository. `voice/setup.py` fetches each file into your data folder and keeps it only
if it matches the checksum written here.

## Wake word: openWakeWord

Code Apache-2.0, <https://github.com/dscripka/openWakeWord>. Files from its release v0.5.1.

| file | bytes | sha256 | licence |
|---|---|---|---|
| melspectrogram.onnx | 1087958 | ba2b0e0f8b7b875369a2c89cb13360ff53bac436f2895cced9f479fa65eb176f | Apache-2.0 |
| embedding_model.onnx | 1326578 | 70d164290c1d095d1d4ee149bc5e00543250a7316b59f31d056cff7bd3075c1f | Apache-2.0 (Google speech embedding, through openWakeWord) |
| hey_jarvis_v0.1.onnx | 1271370 | 94a13cfe60075b132f6a472e7e462e8123ee70861bc3fb58434a73712ee0d2cb | CC BY-NC-SA 4.0: personal, non-commercial use |

`wake.py` runs openWakeWord's feature pipeline directly on onnxruntime and numpy. Your own one-word model
(`personal.npz`) is trained on your Mac by `train.py` from your own recordings and from macOS stock voices (`say`).

## Turn-taking

| file | bytes | sha256 | licence | source |
|---|---|---|---|---|
| silero_vad.onnx | 2327524 | 1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3 | MIT | Silero VAD v6.2.3, <https://github.com/snakers4/silero-vad> |
| smart-turn-v3.2-cpu.onnx | 8679182 | 2bb026316b14a660486a75b1733cd3fbab8c2fd0314dc9af7be49f8cca967e4f | BSD-2-Clause | Smart Turn v3.2 by Pipecat/Daily, <https://huggingface.co/pipecat-ai/smart-turn-v3> |

Smart Turn's audio features (Whisper log-mel, 8 s) are a numpy port of the WhisperFeatureExtractor of the transformers
library (Apache-2.0). Reference inference code: <https://github.com/pipecat-ai/smart-turn> (BSD-2-Clause).

## Python packages

numpy (BSD-3-Clause) and onnxruntime (MIT), from PyPI, in an environment of their own inside your data folder.

## Speech

Jarvis speaks with the voices that ship with macOS (AVSpeechSynthesizer). Nothing is downloaded for speech.
