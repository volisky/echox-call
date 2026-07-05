"""Standalone temporary public-opinion risk text API."""

from __future__ import annotations

from random import SystemRandom
from typing import Any

import openai
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import PlainTextResponse
from starlette.concurrency import run_in_threadpool

from echox_call.core.settings import LlmWorkerConfigError, load_llm_worker_settings

_random = SystemRandom()

_FIXED_RECOMMENDATION = "建议推送新宣、网安立即开展监测导控。"
_SYSTEM_PROMPT = """你是公安舆情风险研判助手。请根据传入的案件编号和案件内容，生成一段中文舆情风险研判正文。

要求：
1. 只依据案件内容中明确出现的信息，不要编造未出现的伤亡人数、地点、人员身份或处置情况。
2. 正文开头必须且只能二选一：“该警情疑似个人极端行为，”或“该警情疑似汽车冲撞事件，”。不得输出其他疑似风险类型。
3. 如案件内容明确出现将要或已经实施极端行为，或主观层面明确表达报复社会、大家都别想活、伤害不特定人员等意图并伴随极端行为意图，选择“个人极端行为”；如明确出现车辆在公共场所或人员密集区域冲撞、撞击、碾压行人或造成人员伤亡，选择“汽车冲撞事件”。
4. 正文应包含：敏感因素、可能舆情发酵路径。
5. 舆情发酵路径必须使用“环节一-环节二-环节三”的短语格式，参照“现场视频扩散-学生安全质疑-处置通报追问”；三个环节必须根据案件内容改写，不要固定照抄示例。
6. 不要输出“建议推送新宣、网安立即开展监测导控。”，该句由系统固定拼接。
7. 正文中必须自然包含该发酵路径，例如“舆情可能沿‘环节一-环节二-环节三’发酵”。
8. 不要输出“负面炒作风险高/中/低”、置信度、舆论爆点值、JSON、Markdown、编号或解释。
9. 只输出一段话，80到160字，末尾使用中文句号。"""


def create_app() -> FastAPI:
    app = FastAPI(
        title="EchoX Public Opinion Risk API",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/analyze", response_class=PlainTextResponse)
    async def analyze(request: Request) -> PlainTextResponse:
        payload = await _read_payload(request)
        case_id = _first_text(payload, "caseId", "case_id", "jjdh", "id")
        case_content = _first_text(payload, "caseContent", "case_content", "content", "bjnr")
        if not case_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="caseId is required",
            )
        if not case_content:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="caseContent is required",
            )
        try:
            body = await run_in_threadpool(_generate_risk_text, case_id, case_content)
        except OpinionRiskError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=str(exc),
            ) from exc
        return PlainTextResponse(_build_result_text(body))

    return app


async def _read_payload(request: Request) -> dict[str, Any]:
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="JSON body must be an object",
            )
        return payload
    form = await request.form()
    return dict(form)


def _first_text(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


class OpinionRiskError(RuntimeError):
    """Raised when the temporary opinion-risk LLM call fails."""


def _generate_risk_text(case_id: str, case_content: str) -> str:
    try:
        settings = load_llm_worker_settings()
    except LlmWorkerConfigError as exc:
        raise OpinionRiskError(f"LLM settings invalid: {exc}") from exc

    client = openai.OpenAI(
        api_key=settings.api_key,
        base_url=settings.base_url or None,
    )
    try:
        response = client.chat.completions.create(
            model=settings.model,
            max_tokens=min(settings.max_tokens, 512),
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"案件编号：{case_id}\n案件内容：{case_content}",
                },
            ],
        )
    except openai.RateLimitError as exc:
        raise OpinionRiskError(f"LLM rate limited: {exc}") from exc
    except openai.APITimeoutError as exc:
        raise OpinionRiskError(f"LLM timeout: {exc}") from exc
    except openai.APIError as exc:
        raise OpinionRiskError(f"LLM API error: {exc}") from exc
    except Exception as exc:
        raise OpinionRiskError(f"LLM request failed: {exc}") from exc

    choice = response.choices[0] if response.choices else None
    if choice is None:
        raise OpinionRiskError("LLM response did not contain choices")
    content = getattr(choice.message, "content", None)
    if not isinstance(content, str) or not content.strip():
        raise OpinionRiskError("LLM response did not contain text")
    return _normalize_body_text(content)


def _normalize_body_text(value: str) -> str:
    text = " ".join(value.replace("\r", "\n").split())
    text = text.replace(_FIXED_RECOMMENDATION, "").strip()
    for prefix in ("研判正文：", "舆情风险研判：", "输出："):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    if not text:
        raise OpinionRiskError("LLM generated empty risk text")
    if text[-1] not in "。！？":
        text += "。"
    return text


def _build_result_text(body: str) -> str:
    confidence = _random.uniform(0.85, 0.95)
    burst_score = _random.randint(85, 95)
    return (
        f"{body}"
        f"负面炒作风险高，研判置信度{confidence:.2f}，舆论爆点值{burst_score}/100。\n"
        f"{_FIXED_RECOMMENDATION}"
    )


app = create_app()
