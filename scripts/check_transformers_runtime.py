"""Opt-in real SDK smoke with tiny random Qwen3 weights; no downloads/quality claims."""

import asyncio
import json
import tempfile
from dataclasses import asdict
from pathlib import Path

from roma.core.local_llm_config import LocalLLMSettings
from roma.providers.ai.contracts import LLMMessage, LLMRequest
from roma.providers.ai.transformers_llm import Qwen3TransformersLLM


async def run() -> None:
    import torch
    import transformers
    from tokenizers import Tokenizer
    from tokenizers.models import WordLevel
    from tokenizers.pre_tokenizers import Whitespace

    torch.manual_seed(7)
    torch.set_num_threads(1)
    vocabulary = {
        word: index
        for index, word in enumerate(
            [
                "[UNK]",
                "[PAD]",
                "[EOS]",
                "system",
                "user",
                "assistant",
                "hello",
                "reply",
                "Namaste",
                "ji",
            ]
        )
    }
    tokenizer = Tokenizer(WordLevel(vocabulary, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = Whitespace()
    fast = transformers.PreTrainedTokenizerFast(
        tokenizer_object=tokenizer,
        unk_token="[UNK]",
        pad_token="[PAD]",
        eos_token="[EOS]",
        chat_template="{% for message in messages %}{{ message['role'] }} {{ message['content'] }} {% endfor %}{% if add_generation_prompt %}assistant {% endif %}",
    )
    model = transformers.Qwen3ForCausalLM(
        transformers.Qwen3Config(
            vocab_size=len(vocabulary),
            hidden_size=32,
            intermediate_size=64,
            num_hidden_layers=1,
            num_attention_heads=4,
            num_key_value_heads=2,
            head_dim=8,
            max_position_embeddings=256,
            eos_token_id=2,
            pad_token_id=1,
        )
    )
    with tempfile.TemporaryDirectory(prefix="roma-tiny-qwen3-") as directory:
        model.save_pretrained(directory)
        fast.save_pretrained(directory)
        settings = LocalLLMSettings(
            _env_file=None,
            local_llm_model=str(Path(directory)),
            local_llm_device="cpu",
            local_llm_dtype="fp32",
        )
        provider = Qwen3TransformersLLM(settings)
        request = LLMRequest(
            (LLMMessage("system", "reply"), LLMMessage("user", "hello")),
            temperature=0,
            max_tokens=4,
        )
        chunks = [chunk async for chunk in provider.generate(request)]
        assert chunks[-1].metrics is not None
        assert 1 <= chunks[-1].metrics.generated_tokens <= 4
        assert chunks[-1].metrics.ttft_ms is not None
        assert provider._model.device.type == "cpu"
        print(
            json.dumps(
                {
                    "evidence": "tiny-random-Qwen3 SDK mechanics only; not Qwen3-8B or language quality",
                    "torch": torch.__version__,
                    "transformers": transformers.__version__,
                    "token_ids": fast("user hello")["input_ids"],
                    "raw_output": "".join(c.text for c in chunks),
                    "metrics": asdict(chunks[-1].metrics),
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    asyncio.run(run())
