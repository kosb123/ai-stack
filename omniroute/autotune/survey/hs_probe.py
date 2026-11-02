"""Runs INSIDE the hindsight container: Hindsight's own LLMProvider against OmniRoute, one model per line of stdin.

retain = LLMProvider.call with Hindsight's real retain prompt and schema (build_chunk_prompt_parts, live config)
reflect = LLMProvider.call_with_tools(tool_choice=REQUIRED), the reflect / page-refresh shape
Prints one JSON object per model. No retries: a model must answer the first time.
"""
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone

from hindsight_api.config import _get_raw_config as get_config  # bank-level fields need the raw config
from hindsight_api.engine.llm_interface import LLM_TOOL_CHOICE_REQUIRED
from hindsight_api.engine.llm_wrapper import LLMProvider
from hindsight_api.engine.retain.fact_extraction import _coerce_fact_response, build_chunk_prompt_parts

BASE = "http://host.docker.internal:20128/v1"
CONVO = "\n".join(f"user: 오늘 {i}번째 작업으로 가계부 CLI의 월별 합계 기능을 고쳤고 테스트 {i * 3}개가 통과했다.\n"
                  f"assistant: 확인했습니다. ledger.py의 monthly_total 함수를 수정했고 pytest 결과를 기록했습니다."
                  for i in range(1, 25))
TOOLS = [{"type": "function", "function": {
    "name": "search_observations", "description": "Search consolidated observations about the user",
    "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "reason": {"type": "string"}},
                   "required": ["query"]}}}]


def llm(model: str, timeout: float) -> LLMProvider:
    return LLMProvider(provider="openai", api_key=os.environ["HINDSIGHT_API_LLM_API_KEY"], base_url=BASE, model=model,
                       reasoning_effort="low", default_headers={"x-omniroute-no-memory": "true"},
                       timeout=timeout, max_retries=0)


def err(e: Exception) -> str:
    return f"{type(e).__name__}: {' '.join(str(e).split())[:220]}"


CONFIG = get_config()
PARTS = build_chunk_prompt_parts(CONFIG, chunk=CONVO, event_date=datetime.now(timezone.utc))


async def retain(model: str) -> dict:
    t0 = time.perf_counter()
    try:
        res = await asyncio.wait_for(llm(model, 120).call(
            messages=[{"role": "system", "content": PARTS.system_prompt},
                      {"role": "user", "content": PARTS.user_message}],
            response_format=PARTS.response_schema, strict_schema=CONFIG.llm_strict_schema_retain,
            max_completion_tokens=CONFIG.retain_max_completion_tokens, temperature=CONFIG.llm_temperature_retain,
            scope="retain_extract_facts", max_retries=0, skip_validation=True), 150)
        coerced = _coerce_fact_response(res.content)
        facts = [f for f in (coerced or {}).get("facts", []) if isinstance(f, dict) and str(f.get("what") or "").strip()]
        ok = len(facts) > 0
        detail = f"{len(facts)} facts" if ok else f"no usable facts: {str(res.content)[:160]}"
        return {"ok": ok, "secs": round(time.perf_counter() - t0, 1), "detail": detail}
    except Exception as e:
        return {"ok": False, "secs": round(time.perf_counter() - t0, 1), "detail": err(e)}


async def reflect(model: str) -> dict:
    t0 = time.perf_counter()
    try:
        res = await asyncio.wait_for(llm(model, 120).call_with_tools(
            messages=[{"role": "system", "content": "You answer questions about the user from their memory bank. "
                                                    "Use the tool to look things up."},
                      {"role": "user", "content": "What operating system does the user use?"}],
            tools=TOOLS, tool_choice=LLM_TOOL_CHOICE_REQUIRED, max_completion_tokens=1000, scope="reflect",
            max_retries=0), 150)
        names = [c.name for c in res.tool_calls]
        ok = "search_observations" in names
        detail = f"tool_calls={names}" if names else f"no tool call: {(res.content or '')[:120]!r}"
        return {"ok": ok, "secs": round(time.perf_counter() - t0, 1), "detail": detail}
    except Exception as e:
        return {"ok": False, "secs": round(time.perf_counter() - t0, 1), "detail": err(e)}


async def one(model: str) -> dict:
    r = await retain(model)
    t = await reflect(model)
    return {"model": model, "retain": r, "reflect": t}


async def main() -> None:
    models = [m.strip() for m in sys.stdin if m.strip()]
    sem = asyncio.Semaphore(int(os.environ.get("HS_PROBE_PARALLEL", "6")))

    async def guarded(m):
        async with sem:
            out = await one(m)
            print(json.dumps(out, ensure_ascii=False), flush=True)

    await asyncio.gather(*(guarded(m) for m in models))


asyncio.run(main())
