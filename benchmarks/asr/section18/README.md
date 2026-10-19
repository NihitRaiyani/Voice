# Section 18 public ASR evidence

Manifest and reports use `google/fleurs`, validation row 0 for `gu_in`, `hi_in` and `en_us`, provided under [CC-BY-4.0](https://huggingface.co/datasets/google/fleurs). Attribution: FLEURS dataset authors/contributors and the Google FLEURS project. Reports contain public reference/hypothesis text, never caller/project recordings or credentials. Derived noise/telephone and stitched language-pair cases are explicitly identified in the manifest.

Raw WAVs and exported failure clips remain ignored runtime artifacts. Reproduce/download public audio and read failures using the [runbook](../../../docs/runbook.md#section-18-asr-benchmark-and-failure-review). The [Level 4 brief](../../../docs/levels/level-04.md#section-18-asr-benchmark) owns interpretation and remaining gates.

The separate [local base report](whisper-base-local-report.json) uses the same 15-case manifest, verified [base artifacts](../whisper-base-artifacts.json) and offline CPU INT8 inference. All requests completed; Gujarati/Hindi/stitched quality failed. Hosted reports remain unchanged. Execution success is distinct from quality approval.
