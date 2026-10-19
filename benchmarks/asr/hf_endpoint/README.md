# Custom IndicConformer endpoint template

This directory is a reviewable **template** for the optional `hf_indic_conformer` provider, not a deployed endpoint or a generic Hugging Face serverless ASR integration. The separate `hf_public_lab` public-benchmark Space route and `hf_whisper` authenticated router do not use this handler; their commands/privacy boundaries are in [the runbook](../../../docs/runbook.md#sections-1517-speech-input-lab). No deployment, model download, inference call, or billing action occurs automatically. Copy `handler.py` into the dedicated endpoint artifact only after review. It has no dependency on the Roma package.

The handler implements the exact custom client contract:

```json
{"inputs":{"audio":"<base64-encoded complete WAV>","language":"hi","decoder":"ctc"}}
```

Successful response:

```json
{"text":"normalized transcript","language":"hi"}
```

Only `hi` and `gu` are accepted. English must use the separate Whisper comparison adapter; Hindi decoding is not English language support. `decoder` is `ctc` or `rnnt`. Audio must be uncompressed mono 16 kHz signed PCM16 WAV, with at most 960,000 PCM bytes (30 seconds); WAV metadata is also bounded. The handler normalizes NFC, decimal numerals and whitespace while preserving English terms such as SEO, Google Ads and Digital Marketing. Outputs remain below the client's 128 KiB JSON response limit. Serve identity-encoded JSON; the client rejects compressed responses.

Deployment prerequisites require an operator to:

1. Accept the gated model's access terms and review its MIT license and custom Python code. The [official AI4Bharat card](https://huggingface.co/ai4bharat/indic-conformer-600m-multilingual) documents the callable `model(waveform, language, decoder)` interface and the 22 Indic languages; this template deliberately exposes only Hindi/Gujarati.
2. Pin the exact model **commit revision** and reviewed custom-code/artifact identity in deployment configuration. Provision all model, tokenizer, ONNX and custom-code files separately into an immutable mounted directory, and pass that directory as `EndpointHandler(path)`. `path` is a local directory, not a Hub model identifier. A mutable branch name is insufficient provenance. No public inference-provider availability is assumed.
3. Set `HF_HUB_OFFLINE=1` in the process environment **before any Hub/Transformers imports**. The handler checks both startup environment and the Hub's imported offline constant, and uses `local_files_only=True` with the reviewed `trust_remote_code=True` model. Offline cache misses fail startup.
4. Build and validate a compatible endpoint runtime separately. The official card names `transformers`, `torch`/`torchaudio`, `onnx==1.20.1`, `onnxruntime==1.20.1` and `onnxruntime-gpu==1.20.1`. These are source requirements to evaluate, not a portable installation command: select the appropriate CPU/GPU runtime for the hosting platform and verify dependency compatibility. This template does not install them or establish a tested production dependency lock.
5. Configure authentication/TLS, body limits, a single in-flight model request, native execution deadlines, capacity and failure handling at the endpoint platform. The handler loads once per process and rejects concurrent model requests while its worker is busy; native inference is synchronous and cannot be forcibly cancelled by this Python template. Configure sanitized access/error logs without tokens, raw audio, request bodies or unnecessary transcripts.

Offloading moves weights, model RAM and compute to the **remote host**; it does not eliminate those requirements. Dedicated endpoints can incur ongoing hosting and inference charges. Creating or deploying one requires a separate operator decision and cost review; a Hugging Face token alone neither provisions a host nor guarantees free inference. This template does not prove speech quality, code-mix performance, latency, platform compatibility or a passed Level 4 gate.

Offline tests inject a loader with the signature `loader(path) -> infer(pcm_bytes, language, decoder) -> text`. Injection avoids all model imports, downloads and external calls. The production default loads the mounted model in the constructor and invokes the official API with a normalized mono waveform.
