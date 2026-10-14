"""Ready-made target settings for common APIs: `[target] preset = "openai"` and you are done."""

from __future__ import annotations

PRESETS: dict[str, dict] = {
    "openai": {
        "label": "OpenAI",
        "description": "OpenAI chat completions.",
        "model": "gpt-4o-mini",
        "base_url": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
        "kind": "openai",
    },
    "groq": {
        "label": "Groq",
        "description": "Groq (OpenAI-compatible).",
        "model": "llama-3.1-8b-instant",
        "base_url": "https://api.groq.com/openai/v1",
        "api_key_env": "GROQ_API_KEY",
        "kind": "openai",
    },
    "openrouter": {
        "label": "OpenRouter",
        "description": "OpenRouter (OpenAI-compatible, many models).",
        "model": "openai/gpt-4o-mini",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
        "kind": "openai",
    },
    "openai-compatible": {
        "label": "Any OpenAI-compatible server",
        "description": "vLLM, LM Studio, LiteLLM, Together... set base_url and model.",
        "model": "my-model",
        "base_url": "http://localhost:8000/v1",
        "api_key_env": "",
        "kind": "openai",
    },
    "anthropic": {
        "label": "Anthropic (Claude)",
        "description": "Anthropic messages API.",
        "model": "claude-haiku-4-5",
        "base_url": "https://api.anthropic.com/v1",
        "api_key_env": "ANTHROPIC_API_KEY",
        "kind": "anthropic",
    },
    "gemini": {
        "label": "Google Gemini",
        "description": "Gemini generateContent.",
        "model": "gemini-flash-lite-latest",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "api_key_env": "GEMINI_API_KEY",
        "kind": "gemini",
    },
    "ollama": {
        "label": "Ollama (local)",
        "description": "A model running on your computer with Ollama.",
        "model": "llama3.2",
        "base_url": "http://localhost:11434",
        "api_key_env": "",
        "kind": "ollama",
    },
    "rag": {
        "label": "Simple Q&A API",
        "description": 'Your own API: POST {"question": ...}, reply has "answer" and optionally "contexts".',
        "model": "",
        "base_url": "http://localhost:8000/ask",
        "api_key_env": "",
        "kind": "simple",
    },
}


def catalog() -> list[dict]:
    return [{"name": k, **{f: v for f, v in p.items() if f != "kind"}} for k, p in PRESETS.items()]


def _messages(system: str | None) -> list[dict]:
    msgs = [{"role": "system", "content": system}] if system else []
    return [*msgs, {"role": "user", "content": "{{input}}"}]


def expand(cfg: dict) -> dict:
    """Turn `{preset, model, ...}` into a full http target config. Anything you set yourself wins."""
    cfg = dict(cfg)
    name = cfg.pop("preset")
    if name not in PRESETS:
        raise ValueError(f"unknown preset '{name}'. Choose from: {', '.join(PRESETS)}")
    p = PRESETS[name]
    model = cfg.pop("model", None) or p["model"]
    base = (cfg.pop("base_url", None) or p["base_url"]).rstrip("/")
    key_env = cfg.pop("api_key_env", None)
    key_env = p["api_key_env"] if key_env is None else key_env
    system = cfg.pop("system", None)
    temperature = cfg.pop("temperature", 0)
    max_tokens = cfg.pop("max_tokens", 1024)
    kind = p["kind"]
    bearer = {"Authorization": f"Bearer ${{{key_env}}}"} if key_env else {}

    if kind == "openai":
        d = {
            "url": f"{base}/chat/completions",
            "headers": bearer,
            "body": {"model": model, "messages": _messages(system), "temperature": temperature},
            "answer_path": "choices[0].message.content",
            "tokens_in_path": "usage.prompt_tokens",
            "tokens_out_path": "usage.completion_tokens",
        }
    elif kind == "anthropic":
        body = {"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": "{{input}}"}]}
        if system:
            body["system"] = system
        d = {
            "url": f"{base}/messages",
            "headers": {"x-api-key": f"${{{key_env}}}", "anthropic-version": "2023-06-01"},
            "body": body,
            "answer_path": "content[0].text",
            "tokens_in_path": "usage.input_tokens",
            "tokens_out_path": "usage.output_tokens",
        }
    elif kind == "gemini":
        body = {
            "contents": [{"role": "user", "parts": [{"text": "{{input}}"}]}],
            "generationConfig": {"temperature": temperature},
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        d = {
            "url": f"{base}/models/{model}:generateContent",
            "headers": {"x-goog-api-key": f"${{{key_env}}}"},
            "body": body,
            "answer_path": "candidates[0].content.parts[0].text",
            "tokens_in_path": "usageMetadata.promptTokenCount",
            "tokens_out_path": "usageMetadata.candidatesTokenCount",
        }
    elif kind == "ollama":
        d = {
            "url": f"{base}/api/chat",
            "body": {"model": model, "messages": _messages(system), "stream": False},
            "answer_path": "message.content",
            "tokens_in_path": "prompt_eval_count",
            "tokens_out_path": "eval_count",
        }
    else:  # a plain Q&A endpoint of your own
        d = {"url": base, "body": {"question": "{{input}}"}, "answer_path": "answer", "contexts_path": "contexts"}

    headers = {**d.get("headers", {}), **(cfg.pop("headers", None) or {})}
    merged = {**d, **cfg}
    if headers:
        merged["headers"] = headers
    return merged
