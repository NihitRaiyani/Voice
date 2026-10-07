# Local LLM — Transformers and Apple INT4

**Level 2 section 11.** Direct `Qwen3TransformersLLM` and optional Apple-native `Qwen3MLXLLM` implement the same `LLMProvider`. The owner authorized Qwen3-1.7B INT4 or a suitable larger model on 2026-10-07. The pinned Mac development profile selects `mlx-community/Qwen3-1.7B-4bit`; production model approval still requires native language and latency review. Qwen3-8B remains a suitable-host comparison candidate. The console and multilingual lab are separate from the live Twilio/Pipecat cloud comparison profile.

## Data flow and ownership

Caller text → validated discovery/time extraction → existing `advance_turn()` controller → system rules/current state/one narrow task → tokenizer/chat template → token IDs on the selected device → model forward/logits → greedy or sampled decoding → streamed decoded chunks → complete raw diagnostic buffer → existing confirmation/time/sign-off guards → deterministic sentence safety → final response → checkpoint.

The model can supply wording and bounded extraction. It cannot change the stage, calculate absolute dates, create an appointment or approve unsafe text. Discovery/time extraction uses the same local provider and Pydantic validation; malformed JSON, extra fields, blank values, invalid time bounds and provider failure re-ask rather than mutate slots. The existing code resolves relative time in Asia/Kolkata and corroborates booking signals. Clear profile answers use code-owned next-field questions after validated extraction. This avoids observed 1.7B repetition/wrong-field wording and a second generation. Question cues, mixed answer/question turns and deflections retain model wording; the shortcut must not silently discard a legitimate course question. Education/year wording covers current study or completed study without guessing the caller's status. Other extraction-bearing turns can require two sequential generations: extraction, then wording; deterministic readback also avoids wording generation. The separate language lab still tests model wording for every stage and does not substitute profile questions to inflate scores. Automated tests use mocks.

`TextConversationService` emits before/after state, extracted slot JSON with validity/prompt/raw output, route, response prompt, raw output, safety verdicts, final response and generation metrics. Raw output is never the final response. The console is for synthetic callers; prompts/raw outputs are diagnostic data and can contain caller information. Review artifacts are owner-only under ignored `var/roma/llm-lab/`. The service accepts a state-store adapter; default console checkpoints are process-local. Section 10's PostgreSQL store can be injected for an existing persisted provider call ID, outside inference transactions. A process-local checkpoint is not restart recovery.

This lab performs no appointment commits and reports `booking_committed=false`. A conversational lock holds at deterministic readback. Live booking truth belongs to Level 3. Section 12 still owns durable structured safety-event audit and fuller injection evidence; today's lexical guards do not prove every generated fact or promise correct.

## Loading, devices and precision

The optional `local-llm` extra locks Torch, Transformers (Qwen3 support, 4.x), Accelerate and psutil. Nothing imports those SDKs or downloads weights when a provider is constructed. Load on first generation, use `AutoTokenizer` and `AutoModelForCausalLM`, disable remote repository code, set a revision, load once and call `eval()`. `HF_TOKEN` (and common aliases) is a `SecretStr` read only at the download boundary. Cache-only is the default; `token=False` avoids silently taking another cached account token when none is configured.

- `auto` chooses CUDA, then Apple MPS, then CPU. Explicit unavailable devices fail with `ProviderUnavailable`.
- CUDA auto precision uses BF16 when supported, otherwise FP16. MPS uses FP16; CPU defaults to FP32. Explicit CPU BF16 requires an operator-measured suitable host; CPU FP16 and this adapter's MPS BF16 are rejected.
- INT4 uses bitsandbytes NF4/double quantization with BF16/FP16 compute on CUDA. Install `local-llm-int4` separately; this path is not enabled on the 8 GB Mac. INT4 weight storage differs from the compute dtype and needs its own language/performance review.
- The 8B lab requires at least 20 GiB free for half precision, 40 GiB for FP32, or 8 GiB CUDA memory for INT4. These conservative admission floors reserve headroom and are not a throughput or OOM guarantee. Configuring another model explicitly requires measuring its own limits.

On 2026-10-07 the development Mac reported **8 GiB RAM, arm64**. The requested 8.2-billion-parameter model's half-precision weights alone need about 16.4 GB before activations, KV cache or OS usage. This host is therefore not admitted for Qwen3-8B. Do not silently replace it with a smaller model or claim the 8B quality gate passed. The owner explicitly authorized a smaller candidate. Official `Qwen/Qwen3-1.7B` is the post-trained model; there is no official `Qwen/Qwen3-1.7B-Instruct` repository. Its full weight files occupy about 4.06 GB. The community Apple conversion occupies about 968 MB and is pinned at `3b1b1768f8f8cf8351c712464f906e86c2b8269e`. Choose it for development headroom, then measure responses; parameter count alone cannot establish the best voice model. A 4B/8B promotion needs its own memory, latency, extraction and native-quality evidence.

## Decoding and measurement

Use `apply_chat_template(..., enable_thinking=False)` to keep reasoning out of replies. `max_new_tokens` is a completion budget, not the input/context budget. Temperature zero is greedy decoding; positive temperature samples with top-p 0.8/top-k 20. Prompt overflow is rejected instead of truncating the system rules. Completion is bounded to 512 tokens and a configured timeout.

A worker thread runs `model.generate()` under `torch.inference_mode()` and feeds `TextIteratorStreamer`; the async interface stays responsive while waiting for chunks. One adapter serializes its generations. Cancellation/timeout requests a stop at the next token boundary and joins the worker before another request uses that model. A running device forward pass cannot be forcibly interrupted; this lab is not Level 7's production queue/admission/cancellation guarantee.

Terminal `LLMChunk.metrics` records model/revision, selected device/storage precision, cold-load seconds, prompt/generated token counts, first-generated-token latency, generation wall time, generated tokens/second and CUDA peak allocated bytes when available. TTFT starts after loading/tokenization and ends when the first new token reaches the streamer, rather than when its first printable word appears. Report load time separately. Tokens/second is generated tokens (including an emitted EOS) divided by generation wall time; it includes prefill/streaming overhead. MPS/CPU CUDA memory is null, never a made-up VRAM value. Capture the resolved model commit in metrics; pin `LOCAL_LLM_REVISION` for repeatable comparisons.

## Apple INT4 adapter and commands

`Qwen3MLXLLM` loads pre-quantized 4-bit weights through optional MLX LM 0.29.1 on Apple silicon. This is a separate backend, not the Transformers bitsandbytes path. The adapter checks the stored quantization format, disables remote repository code and thinking, uses 256-token prefill steps, rejects oversized prompts and serializes generations. Loading and generation remain on one dedicated worker per adapter to preserve SDK stream ownership. Cancellation/timeout waits for that worker before reuse, including bounded prefill checks. Metrics retain the resolved revision, INT4 storage, token counts and timings; no CUDA memory is claimed on Apple hardware. MLX's TTFT is observed when its first generation response is yielded, so compare timing definitions when benchmarking different SDKs.

```bash
make local-llm-download      # install locked Apple runtime; download pinned model once
make text-console           # real trained Qwen3-1.7B INT4, synthetic callers only
make text-console-mock      # deterministic offline demo
make language-lab           # real 40-case multilingual review artifact
uv run --no-sync python scripts/local_llm_language_eval.py --score var/roma/llm-lab/review.json
```

`configs/local-llm-mac.json` contains model settings only, never credentials. Explicit profile settings override `.env`/Makefile mock defaults. Python scripts also accept `--provider mock` to override a profile. General application settings still default to mocks. `LLM_PROFILE` switches the console/lab profile; live Pipecat selection is separate and remains OpenAI/Sarvam. HF credentials stay in environment variables/ignored `.env`; local inference requires no paid API key, carrier key or database connection. Model data stays in the ignored Hugging Face user cache, not Git. Cache-only loading is the default; downloading is an explicit setup action.

The direct Transformers learning path is preserved through `make local-llm-sync` and `--provider qwen3_transformers` without a Mac profile. Environment settings or an explicit non-secret profile choose its model, pinned revision and precision. Qwen3-8B still requires a suitable host. `uv sync` removes unselected optional extras; select both runtime extras explicitly when comparing them in one environment.

On a suitable CUDA host, install the separate Transformers INT4 extra and invoke the script directly so the Mac profile cannot override device/model settings:

```bash
uv sync --locked --extra dev --extra telephony --extra workers --extra local-llm --extra local-llm-int4
LOCAL_LLM_DEVICE=cuda LOCAL_LLM_DTYPE=int4 uv run --no-sync python scripts/local_llm_console.py --provider qwen3_transformers
```

Set a pinned model/revision in the environment or an explicit non-secret profile. Allow downloads only when intended (`LOCAL_LLM_LOCAL_FILES_ONLY=false`). Never print HF tokens. No live carrier calls or appointment commits occur in this text lab.

## Language gate agreed before scoring

`benchmarks/llm/language_cases.json` contains **40 scripted cases**, ten per Gujarati, Hindi, English and code-mix group. Every group covers all seven stages plus money, injection and missing-information cases. The separate lab removes only the live language directives from an in-memory prompt copy; runtime prompt files and live Hinglish policy remain unchanged. Safe substitutions remain the approved current text; a wrong-language substitution can reduce native naturalness/correctness scores. Do not bypass safety to improve a score.

The project owner agreed on 2026-10-07 to the recommended policy in `language_policy.json`: mean correctness **≥4/5** and mean naturalness **≥4/5 in each group**, every individual score **≥3/5**, and **zero unsafe final replies**. Native reviewers score the final reply, inspect the raw reply and task/state, and record their name plus safety judgment. Scores 1–5 mean unusable, major errors, usable with edits, correct/natural with minor issues, and fully correct/natural. Correctness includes task, facts, slots and required language; naturalness includes grammar, respectful tone and gender. Review code-mix for natural Hindi-base Hinglish.

Generation writes blank scores, never auto-generated native grades. Scoring returns a nonzero exit for missing/duplicate/mislabeled cases, missing reviewers, invalid scores, stale captured prompts, mock-only outputs or a failed threshold. A pass means eligible for engineering review, not automatic production promotion. Reviewer assignment and native scores for the selected 1.7B candidate are pending. Qwen3-8B comparison evidence is also pending. If a candidate fails, tighten prompts or explicitly evaluate another open model, regenerate all 40 cases and record the reason/identity/results in the level brief and decision register. Do not manufacture results.

## Optional SDK mechanics smoke

```bash
uv run --no-sync python scripts/check_transformers_runtime.py
```

This opt-in command creates a tiny random-weight Qwen3 model/tokenizer in a temporary directory, loads it through the real adapter on CPU and checks actual decoding, token IDs and timing. It downloads no model weights and is not a language-quality benchmark or proof that Qwen3-8B fits. Mock-only CI does not require Torch. On 2026-10-07 this smoke passed with locked Torch 2.14.1/Transformers 4.57.6: five prompt tokens and three generated tokens on CPU/FP32, with TTFT/throughput metrics present. These tiny-model measurements are not extrapolated to 8B. Real host admission selected MPS/FP16 and refused Qwen3-8B before loading.

## Trained Mac verification

`uv run --no-sync python scripts/check_local_llm.py` runs a short trained generation plus synthetic name extraction/controller/safety turn, saves an owner-only diagnostic artifact and rejects mock-only evidence. It makes no carrier/DB calls or booking commits.

The 40-case trained run is complete; four city fixtures were corrected before review and changed replies regenerated. Sample wrong-language/task replies prevent production approval today. Exact prompt matching rejects stale review artifacts. The 1.7B model remains a development baseline; native scoring and controlled voice-latency work are open.

Real trained-model measurements and native-review status are recorded in [Level 2](levels/level-02.md). Mock CI and tiny random weights remain separate evidence.

## Official references

[Qwen3-1.7B model card](https://huggingface.co/Qwen/Qwen3-1.7B), [Apple 4-bit conversion](https://huggingface.co/mlx-community/Qwen3-1.7B-4bit) and [MLX LM streaming API](https://github.com/ml-explore/mlx-lm) document the Mac candidate/backend. [Qwen3-8B model card](https://huggingface.co/Qwen/Qwen3-8B) documents its parameter count, Transformers support and non-thinking chat template. [Transformers generation utilities](https://huggingface.co/docs/transformers/internal/generation_utils) document streaming/stopping APIs; [bitsandbytes quantization](https://huggingface.co/docs/transformers/quantization/bitsandbytes) documents NF4 and compute precision.

Return to [Level 2](levels/level-02.md), the [provider contract](23-provider-contracts.md) or the [documentation index](README.md).
