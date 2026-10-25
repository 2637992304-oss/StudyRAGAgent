"""Check configured OpenAI-compatible streaming without exposing the API key."""

from app.core.config import get_settings
from app.providers.llm import OpenAICompatibleLLM


def main() -> None:
    settings = get_settings()
    assert settings.llm_base_url and settings.llm_api_key and settings.llm_model
    parts = []
    for delta in OpenAICompatibleLLM().stream(
        [{"role": "user", "content": "请用一句话解释什么是导数。"}]
    ):
        if delta.text:
            parts.append(delta.text)
    if not "".join(parts).strip():
        raise RuntimeError("LLM 未返回文本")
    print(f"PASS: {settings.llm_model} streamed {len(''.join(parts))} characters")


if __name__ == "__main__":
    main()
