from __future__ import annotations

import json
import os
import ssl
import time
import urllib.error
import urllib.request
from typing import Any

from dotenv import load_dotenv

from .prompts import ACTOR_SYSTEM, EVALUATOR_SYSTEM, REFLECTOR_SYSTEM
from .schemas import QAExample, JudgeResult, ReflectionEntry
from .utils import normalize_answer

load_dotenv()

FIRST_ATTEMPT_WRONG = {"hp2": "London", "hp4": "Atlantic Ocean", "hp6": "Red Sea", "hp8": "Andes"}
FAILURE_MODE_BY_QID = {"hp2": "incomplete_multi_hop", "hp4": "wrong_final_answer", "hp6": "entity_drift", "hp8": "entity_drift"}

_LAST_USAGE = {"tokens": 0, "latency_ms": 0}


def get_last_usage() -> dict[str, int]:
    return dict(_LAST_USAGE)


def _record_usage(tokens: int, latency_ms: int) -> None:
    _LAST_USAGE["tokens"] = max(0, int(tokens))
    _LAST_USAGE["latency_ms"] = max(0, int(latency_ms))


def _provider() -> str:
    return os.getenv("LLM_PROVIDER", "mock").strip().lower()


def get_provider_name() -> str:
    return _provider()


def _context_text(example: QAExample) -> str:
    return "\n\n".join(f"[{chunk.title}]\n{chunk.text}" for chunk in example.context)


def _post_json(url: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request_headers = {
        "Accept": "application/json",
        "Connection": "close",
        "Content-Type": "application/json",
        "User-Agent": "reflexion-lab/1.0",
        **(headers or {}),
    }
    timeout = float(os.getenv("LLM_TIMEOUT_SECONDS", "60"))
    attempts = int(os.getenv("LLM_RETRY_ATTEMPTS", "4"))
    backoff = float(os.getenv("LLM_RETRY_BACKOFF_SECONDS", "2"))
    request_delay = float(os.getenv("LLM_REQUEST_DELAY_SECONDS", "0"))
    last_error: BaseException | None = None

    for attempt in range(1, attempts + 1):
        if request_delay > 0:
            time.sleep(request_delay)
        request = urllib.request.Request(url, data=body, headers=request_headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code < 500 and exc.code not in {408, 409, 425, 429}:
                raise RuntimeError(f"LLM request failed: HTTP {exc.code}: {detail}") from exc
            last_error = RuntimeError(f"LLM request failed: HTTP {exc.code}: {detail}")
        except (urllib.error.URLError, TimeoutError, ssl.SSLError) as exc:
            last_error = exc

        if attempt < attempts:
            time.sleep(backoff * attempt)

    raise RuntimeError(f"LLM request failed after {attempts} attempts: {last_error}") from last_error


def _strip_thinking(text: str) -> str:
    while "<think>" in text and "</think>" in text:
        before, rest = text.split("<think>", 1)
        _, after = rest.split("</think>", 1)
        text = before + after
    return text.strip()


def _chat(system: str, user: str, *, json_mode: bool = False) -> tuple[str, int, int]:
    provider = _provider()
    started = time.perf_counter()

    if provider in {"openai", "vllm", "dashscope", "qwen"}:
        prefix_by_provider = {
            "openai": "OPENAI",
            "vllm": "VLLM",
            "dashscope": "DASHSCOPE",
            "qwen": "DASHSCOPE",
        }
        prefix = prefix_by_provider[provider]
        base_url = os.getenv(f"{prefix}_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        model = os.getenv("LLM_MODEL") or os.getenv(f"{prefix}_MODEL")
        api_key = os.getenv(f"{prefix}_API_KEY", "")
        if not model:
            raise RuntimeError(f"Missing {prefix}_MODEL or LLM_MODEL")
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": float(os.getenv("LLM_TEMPERATURE", "0")),
            "max_tokens": int(os.getenv("LLM_MAX_TOKENS", "512")),
        }
        if prefix == "DASHSCOPE":
            payload["enable_thinking"] = os.getenv("DASHSCOPE_ENABLE_THINKING", "false").lower() == "true"
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        data = _post_json(f"{base_url}/chat/completions", payload, headers)
        content = _strip_thinking(data["choices"][0]["message"]["content"])
        tokens = int(data.get("usage", {}).get("total_tokens", 0))
        return content, tokens, round((time.perf_counter() - started) * 1000)

    if provider == "ollama":
        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
        model = os.getenv("LLM_MODEL") or os.getenv("OLLAMA_MODEL", "llama3.1")
        payload = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "stream": False,
            "options": {"temperature": float(os.getenv("LLM_TEMPERATURE", "0"))},
        }
        if json_mode:
            payload["format"] = "json"
        data = _post_json(f"{base_url}/api/chat", payload)
        content = _strip_thinking(data["message"]["content"])
        tokens = int(data.get("prompt_eval_count", 0)) + int(data.get("eval_count", 0))
        return content, tokens, round((time.perf_counter() - started) * 1000)

    if provider == "gemini":
        api_key = os.getenv("GEMINI_API_KEY")
        model = os.getenv("LLM_MODEL") or os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
        if not api_key:
            raise RuntimeError("Missing GEMINI_API_KEY")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "temperature": float(os.getenv("LLM_TEMPERATURE", "0")),
                "maxOutputTokens": int(os.getenv("LLM_MAX_TOKENS", "512")),
            },
        }
        if json_mode:
            payload["generationConfig"]["responseMimeType"] = "application/json"
        data = _post_json(url, payload)
        content = _strip_thinking(data["candidates"][0]["content"]["parts"][0]["text"])
        usage = data.get("usageMetadata", {})
        tokens = int(usage.get("totalTokenCount", 0))
        return content, tokens, round((time.perf_counter() - started) * 1000)

    raise RuntimeError(f"Unsupported LLM_PROVIDER={provider!r}. Use mock, openai, gemini, ollama, vllm, or dashscope.")


def _json_from_text(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.removeprefix("json").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"Expected JSON object, got: {text[:200]}")
    return json.loads(text[start : end + 1])


def _mock_actor_answer(example: QAExample, attempt_id: int, agent_type: str, reflection_memory: list[str]) -> str:
    _record_usage(320 + attempt_id * 65, 160 + attempt_id * 40)
    if example.qid not in FIRST_ATTEMPT_WRONG:
        return example.gold_answer
    if agent_type == "react":
        return FIRST_ATTEMPT_WRONG[example.qid]
    if attempt_id == 1 and not reflection_memory:
        return FIRST_ATTEMPT_WRONG[example.qid]
    return example.gold_answer


def _mock_evaluator(example: QAExample, answer: str) -> JudgeResult:
    _record_usage(80, 30)
    if normalize_answer(example.gold_answer) == normalize_answer(answer):
        return JudgeResult(score=1, reason="Final answer matches the gold answer after normalization.")
    if normalize_answer(answer) == "london":
        return JudgeResult(
            score=0,
            reason="The answer stopped at the birthplace city and never completed the second hop to the river.",
            missing_evidence=["Need to identify the river that flows through London."],
            spurious_claims=[],
        )
    return JudgeResult(
        score=0,
        reason="The final answer selected the wrong second-hop entity.",
        missing_evidence=["Need to ground the answer in the second paragraph."],
        spurious_claims=[answer],
    )


def _mock_reflector(example: QAExample, attempt_id: int, judge: JudgeResult) -> ReflectionEntry:
    _record_usage(120, 50)
    strategy = (
        "Do the second hop explicitly: birthplace city -> river through that city."
        if example.qid == "hp2"
        else "Verify the final entity against the second paragraph before answering."
    )
    return ReflectionEntry(
        attempt_id=attempt_id,
        failure_reason=judge.reason,
        lesson="A partial first-hop answer is not enough; the final answer must complete all hops.",
        next_strategy=strategy,
    )


def actor_answer(example: QAExample, attempt_id: int, agent_type: str, reflection_memory: list[str]) -> str:
    if _provider() == "mock":
        return _mock_actor_answer(example, attempt_id, agent_type, reflection_memory)

    user = f"""Question:
{example.question}

Context:
{_context_text(example)}

Reflection memory:
{json.dumps(reflection_memory, ensure_ascii=False)}
"""
    content, tokens, latency_ms = _chat(ACTOR_SYSTEM, user)
    _record_usage(tokens, latency_ms)
    return content.strip().strip('"')


def evaluator(example: QAExample, answer: str) -> JudgeResult:
    if _provider() == "mock":
        return _mock_evaluator(example, answer)

    user = f"""Question:
{example.question}

Gold answer:
{example.gold_answer}

Predicted answer:
{answer}
"""
    content, tokens, latency_ms = _chat(EVALUATOR_SYSTEM, user, json_mode=True)
    _record_usage(tokens, latency_ms)
    return JudgeResult.model_validate(_json_from_text(content))


def reflector(example: QAExample, attempt_id: int, judge: JudgeResult) -> ReflectionEntry:
    if _provider() == "mock":
        return _mock_reflector(example, attempt_id, judge)

    user = f"""Attempt id: {attempt_id}

Question:
{example.question}

Context:
{_context_text(example)}

Evaluator feedback:
{judge.model_dump_json()}
"""
    content, tokens, latency_ms = _chat(REFLECTOR_SYSTEM, user, json_mode=True)
    _record_usage(tokens, latency_ms)
    return ReflectionEntry.model_validate(_json_from_text(content))
