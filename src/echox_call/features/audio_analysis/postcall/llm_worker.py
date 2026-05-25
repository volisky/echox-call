"""LLM worker: OpenAI-compatible API alarm call analysis."""

from __future__ import annotations

import json
import os
import socket
import time
from typing import Any
from uuid import uuid4

import openai

from echox_call.core.settings import LlmWorkerSettings, load_llm_worker_settings
from echox_call.features.audio_analysis.postcall.llm_repository import (
    PostcallLlmJobRepository,
    PostcallLlmJobStaleError,
)
from echox_call.features.audio_analysis.postcall.llm_worker_models import (
    ClaimedLlmJob,
    LlmAnalysisOutput,
)
from echox_call.features.audio_analysis.postcall.schemas import ATTENTION_LEVEL_NAMES

RETRYABLE_LLM_ERROR_CODES = {
    "LLM_API_ERROR",
    "LLM_TIMEOUT",
    "LLM_RATE_LIMITED",
}

_ANALYZE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "submit_analysis",
        "description": "提交对报警通话的综合分析结论",
        "parameters": {
            "type": "object",
            "properties": {
                "level": {
                    "type": "integer",
                    "enum": [1, 2, 3],
                    "description": "综合关注等级：1=需要关注，2=建议复核，3=暂无明显线索",
                },
                "levelName": {
                    "type": "string",
                    "enum": ["需要关注", "建议复核", "暂无明显线索"],
                    "description": "关注等级名称",
                },
                "caseTypeSummary": {
                    "type": ["string", "null"],
                    "description": "summary第一条，必须以“分析总结：”开头，只列是否命中以及涉及哪些警情类型，不解释原因",
                },
                "caseTypeDetails": {
                    "type": "array",
                    "description": "二级及以上警情类型明细；每项对应summary中一条“警情类型：原因”",
                    "items": {
                        "type": "object",
                        "properties": {
                            "caseType": {
                                "type": "string",
                                "description": "命中的二级及以上警情类型；未命中时填写“未命中二级以上警情”",
                            },
                            "reason": {
                                "type": "string",
                                "description": "一句简短判定依据；涉及毒品、凶器、枪爆、危化品时列出具体名称",
                            },
                        },
                        "required": ["caseType", "reason"],
                    },
                },
                "highRiskAddressSummary": {
                    "type": ["string", "null"],
                    "description": "高发案地址摘要（一句话），仅当isHighIncidentAddress为true时填写，否则为null",
                },
                "highRiskPersonSummary": {
                    "type": ["string", "null"],
                    "description": "涉案人员风险摘要（一句话），仅当存在riskPerson信息时填写，否则为null",
                },
            },
            "required": ["level", "levelName", "caseTypeSummary", "caseTypeDetails"],
        },
    },
}

_SYSTEM_PROMPT = """你是一个公安110接处警智能分析助手。请基于案件已有信息判断是否属于二级及以上警情，并说明具体命中的二级及以上警情类型。

只能依据输入中的通话转写、接警员记录、警情地址、高发案地址标记、涉案人员信息进行判断。不要补充输入中没有出现的事实。信息不足时只能判定为“建议复核”或“暂无明显线索”，不能臆测。

严格证据规则：
- 只允许根据输入文本中已经明确出现的事实判断，不得根据常识、风险、经验进行联想、延申或补全。
- “受伤”“打架”“斗殴”“被打”“打伤”“流血”“倒地”“昏迷”“送医”“抢救”“120到场”等，只能说明存在伤情或冲突，不能推断为“人员死亡”。
- 未明确出现“死亡”“死了”“人死了”“尸体”“无生命体征”“确认死亡”等直接指向人员死亡的表达时，不得判定或疑似判定“人员死亡”，也不得在原因中写“可能死亡”。
- 等级2只能用于复核输入中已经出现但语义不完整的线索；不能把未出现的二级以上警情类型作为“可能线索”。
- 如果只有人员受伤、打架、纠纷、冲突，但没有明确二级及以上警情关键词，应判定为等级3，caseTypeDetails 填写未命中。

关注等级定义：
- 等级1（需要关注）：现有信息明确命中任一二级及以上警情类型，或出现明确高危关键词且无排除语境。
- 等级2（建议复核）：存在疑似二级及以上警情线索，但语义不完整、主体不明、是否排除不确定。
- 等级3（暂无明显线索）：未发现二级及以上警情线索。

需要识别的二级及以上警情类型：
1. 敏感警情类型：危害国家安全、危害公共安全、人员死亡、高坠、有毒有害气体中毒、食物中毒、高坠自杀、溺水自杀、烧炭自杀、上吊自杀、服药（毒）自杀、割脉自杀、卧轨自杀、撞车自杀、老年人走失失踪、儿童走失失踪、智障人员走失失踪、精神障碍患者走失失踪、已满14周岁未满18岁妇女失踪、发现走失失踪老人、发现走失失踪儿童、发现走失失踪智障人员、发现无名尸体、发现弃婴、举报涉枪涉爆线索、举报涉黑线索、群体性事件类警情、聚集上访类警情。
2. 涉人员死亡：命中词为“死”，或明确表达“死亡”“死了”“人死了”“尸体”“无生命体征”“确认死亡”。必须排除：牛、猪、马、羊、猫、狗、鸡、没有死、未死、无死、死角、堵死、封死、死亡证明、赔偿、赔付、索赔、补偿、陪偿、西瓜。只有语义明确指向人员死亡时才判定为“人员死亡”。受伤、打架、流血、倒地、昏迷、送医、抢救均不是人员死亡依据。
3. 涉校园欺凌：需要同时满足场景、欺凌行为、严重后果或持续性三类信息。场景词包括：校园、学生、学校、同学、涉校、涉生、校内、班级、涉青少年。欺凌行为词包括：霸凌、霸陵、欺凌、被打、暴力、打人、打伤、拍视频、录视频、扒衣服、下跪、辱骂、孤立、耳光、厕所、要钱、抢钱、打巴掌、扇巴掌、欺负、要我钱、抢我钱、脱裤子拍、语言攻击、人身攻击、喝尿、抽嘴巴、拍了视频、录了视频、无故挑衅、开盒、脱衣服拍。严重后果或持续性词包括：抑郁、自闭、自杀、不敢上学、不敢去上学、不敢去学校、畏惧上学、跳楼、厌学、自残、焦虑、割腕、害怕去学校、害怕去上学、轻生、不想活、寻死、想死、服毒、喝农药、忧郁症、躁郁、燥郁、上吊、跳河、长期、多次、经常。
4. 涉敏感物品（枪、爆物品）：枪、弹药、子弹、铅弹、火药、底火、手榴弹、手雷、地雷、炸药、雷管、导火索、导爆索、烟雾弹。
5. 涉敏感物品（管制器具）：刀、匕首、斧、开刃、弩、弓、催泪器、电击器。
6. 涉毒品、剧毒、危化品：如果输入中出现具体毒品、剧毒品、危化品名称，必须在原因中列出具体名称。

输出要求：
- caseTypeSummary 必须是 summary 的第一条，必须以“分析总结：”开头，只列结论和涉及的警情类型，不解释原因。示例：“分析总结：涉及人员死亡、举报涉枪涉爆线索。” 或 “分析总结：未发现明确二级以上警情。”
- caseTypeDetails 中每一项对应一种命中的二级及以上警情，后续会格式化为“警情类型：判定依据”。
- caseTypeDetails[].reason 只写一句简短判定依据，不展开政策解释，不重复警情类型，长度控制在50字以内。
- 判定依据必须引用输入中已经存在的明确事实；不得出现“可能导致”“可能死亡”“疑似死亡”“推测”“有可能”等基于联想的表述。
- 涉及毒品、剧毒、危化品时，必须列出具体名称。
- 涉及凶器、枪爆、管制器具时，必须指出具体物品，例如“菜刀”“匕首”“手枪”“子弹”。
- 如果未命中，caseTypeDetails 返回一条：caseType=“未命中二级以上警情”，reason=“现有信息未出现明确二级及以上警情线索。”
- highRiskAddressSummary 仅当 isHighIncidentAddress 为 true 时填写，否则为 null。
- highRiskPersonSummary 仅当存在 riskPerson 信息时填写，否则为 null。"""


_TOOL_OR_JSON_OUTPUT_INSTRUCTION = """请提交结构化分析结论。

如果当前模型支持工具调用，优先调用 submit_analysis 工具。
如果当前模型不支持工具调用，请只输出一个 JSON 对象，不要输出 Markdown 或解释文字。

JSON 对象格式：
{
  "level": 1 | 2 | 3,
  "levelName": "需要关注" | "建议复核" | "暂无明显线索",
  "caseTypeSummary": string | null,
  "caseTypeDetails": [
    {"caseType": string, "reason": string}
  ],
  "highRiskAddressSummary": string | null,
  "highRiskPersonSummary": string | null
}"""


_JSON_ONLY_OUTPUT_INSTRUCTION = """请只输出一个 JSON 对象，不要输出 Markdown 或解释文字。

JSON 对象格式：
{
  "level": 1 | 2 | 3,
  "levelName": "需要关注" | "建议复核" | "暂无明显线索",
  "caseTypeSummary": string | null,
  "caseTypeDetails": [
    {"caseType": string, "reason": string}
  ],
  "highRiskAddressSummary": string | null,
  "highRiskPersonSummary": string | null
}"""


class LlmWorkerError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class LlmWorker:
    def __init__(
        self,
        *,
        settings: LlmWorkerSettings | None = None,
        repository: PostcallLlmJobRepository | None = None,
        worker_id: str | None = None,
    ) -> None:
        self.settings = settings or load_llm_worker_settings()
        self.repository = repository or PostcallLlmJobRepository()
        self.worker_id = worker_id or f"llm-{socket.gethostname()}-{uuid4().hex[:8]}"
        self._client = openai.OpenAI(
            api_key=self.settings.api_key,
            base_url=self.settings.base_url or None,
        )

    def run_once(self, *, batch_size: int | None = None) -> int:
        limit = self.settings.batch_size if batch_size is None else batch_size
        self.repository.recover_expired_jobs()
        processed = 0
        for _ in range(limit):
            job = self.repository.claim_next_job(
                worker_id=self.worker_id,
                lock_seconds=self.settings.lock_seconds,
            )
            if job is None:
                break
            self.process_job(job)
            processed += 1
        return processed

    def run_loop(self, *, sleep_seconds: float = 5.0) -> None:
        while True:
            processed = self.run_once()
            if processed == 0:
                time.sleep(sleep_seconds)

    def process_job(self, job: ClaimedLlmJob) -> None:
        try:
            output = self._analyze(job)
            self.repository.persist_success(job=job, output=output)
        except LlmWorkerError as exc:
            self._record_failure(job=job, error_code=exc.code, error_message=str(exc), retryable=exc.retryable)
        except PostcallLlmJobStaleError:
            return
        except Exception as exc:
            self._record_failure(
                job=job,
                error_code="LLM_WORKER_FAILED",
                error_message=f"{exc.__class__.__name__}: {exc}",
                retryable=True,
            )

    def _analyze(self, job: ClaimedLlmJob) -> LlmAnalysisOutput:
        use_tools = _env_bool("LLM_WORKER_USE_TOOLS", True)
        user_message = _build_user_message(job, use_tools=use_tools)
        request_payload: dict[str, Any] = {
            "model": self.settings.model,
            "max_tokens": self.settings.max_tokens,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
        }
        if use_tools:
            request_payload["tools"] = [_ANALYZE_TOOL]
            request_payload["tool_choice"] = {
                "type": "function",
                "function": {"name": "submit_analysis"},
            }

        try:
            response = self._client.chat.completions.create(**request_payload)
        except openai.RateLimitError as exc:
            raise LlmWorkerError("LLM_RATE_LIMITED", f"rate limited: {exc}", retryable=True) from exc
        except openai.APITimeoutError as exc:
            raise LlmWorkerError("LLM_TIMEOUT", f"timeout: {exc}", retryable=True) from exc
        except openai.APIError as exc:
            raise LlmWorkerError("LLM_API_ERROR", f"API error: {exc}", retryable=True) from exc

        choice = response.choices[0] if response.choices else None
        if choice is None:
            raise LlmWorkerError(
                "LLM_NO_OUTPUT",
                "LLM response did not contain choices",
                retryable=False,
            )

        raw = _extract_analysis_output(choice)

        level = raw.get("level")
        level_name = raw.get("levelName")
        if level not in {1, 2, 3} or level_name not in set(ATTENTION_LEVEL_NAMES.values()):
            raise LlmWorkerError(
                "LLM_INVALID_OUTPUT",
                f"invalid level/levelName in LLM output: {json.dumps(raw, ensure_ascii=False)}",
                retryable=False,
            )

        return LlmAnalysisOutput(
            level=level,
            level_name=level_name,
            case_type_summary=raw.get("caseTypeSummary") or None,
            case_type_details=_normalize_case_type_details(raw.get("caseTypeDetails")),
            high_risk_address_summary=raw.get("highRiskAddressSummary") or None,
            high_risk_person_summary=raw.get("highRiskPersonSummary") or None,
            llm_model=self.settings.model,
        )

    def _record_failure(
        self,
        *,
        job: ClaimedLlmJob,
        error_code: str,
        error_message: str,
        retryable: bool,
    ) -> None:
        try:
            self.repository.record_failure(
                job=job,
                error_code=error_code,
                error_message=error_message,
                retryable=retryable,
                retry_delay_seconds=_retry_delay_seconds(
                    attempt_count=job.attempt_count,
                    base_delay_seconds=self.settings.retry_base_delay_seconds,
                    max_delay_seconds=self.settings.retry_max_delay_seconds,
                ),
            )
        except PostcallLlmJobStaleError:
            return


def _build_user_message(job: ClaimedLlmJob, *, use_tools: bool = True) -> str:
    parts: list[str] = []

    if job.asr_result:
        lines = "\n".join(
            f"{seg.get('speaker', '未知')}：{seg.get('text', '')}"
            for seg in job.asr_result
        )
        parts.append(f"【通话转写】\n{lines}")
    else:
        parts.append("【通话转写】\n（无转写内容）")

    if job.alarm_content:
        parts.append(f"【警情内容（接警员记录）】\n{job.alarm_content}")

    if job.alarm_address:
        is_high = "是" if job.is_high_incident_address else "否"
        parts.append(f"【警情地址】\n{job.alarm_address}（高发案地址：{is_high}）")
    elif job.is_high_incident_address is not None:
        is_high = "是" if job.is_high_incident_address else "否"
        parts.append(f"【高发案地址】{is_high}")

    risk = job.risk_person
    if isinstance(risk, dict):
        risk_lines: list[str] = []
        if risk.get("idcard"):
            risk_lines.append(f"身份证号：{risk['idcard']}")
        tags = risk.get("tags")
        if isinstance(tags, list) and tags:
            risk_lines.append(f"风险标签：{'、'.join(str(t) for t in tags)}")
        if risk.get("report"):
            risk_lines.append(f"说明：{risk['report']}")
        if risk_lines:
            parts.append("【涉案人员信息】\n" + "\n".join(risk_lines))

    parts.append(_TOOL_OR_JSON_OUTPUT_INSTRUCTION if use_tools else _JSON_ONLY_OUTPUT_INSTRUCTION)
    return "\n\n".join(parts)


def _extract_analysis_output(choice: Any) -> dict[str, Any]:
    message = choice.message
    tool_calls = getattr(message, "tool_calls", None) or []
    tool_call = next(
        (
            item
            for item in tool_calls
            if getattr(getattr(item, "function", None), "name", None) == "submit_analysis"
        ),
        None,
    )
    if tool_call is not None:
        arguments = getattr(tool_call.function, "arguments", "")
        return _loads_analysis_json(arguments, source="tool arguments")

    content = getattr(message, "content", None)
    if not isinstance(content, str) or not content.strip():
        raise LlmWorkerError(
            "LLM_NO_VALID_OUTPUT",
            "LLM returned neither submit_analysis tool call nor JSON content",
            retryable=False,
        )
    content_json = _extract_json_object_text(_strip_json_code_fence(content))
    return _loads_analysis_json(content_json, source="message content")


def _loads_analysis_json(raw_text: str, *, source: str) -> dict[str, Any]:
    try:
        raw = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise LlmWorkerError(
            "LLM_INVALID_OUTPUT",
            f"invalid JSON in {source}: {exc}; raw={raw_text[:500]}",
            retryable=False,
        ) from exc

    if not isinstance(raw, dict):
        raise LlmWorkerError(
            "LLM_INVALID_OUTPUT",
            f"LLM {source} must be a JSON object",
            retryable=False,
        )
    return raw


def _normalize_case_type_details(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []

    details: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        case_type = str(item.get("caseType") or "").strip()
        reason = str(item.get("reason") or "").strip()
        if not case_type or not reason:
            continue
        details.append({"caseType": case_type, "reason": reason})
    return details


def _strip_json_code_fence(content: str) -> str:
    text = content.strip()
    if not text.startswith("```"):
        return text

    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _extract_json_object_text(content: str) -> str:
    text = content.strip()
    if text.startswith("{") and text.endswith("}"):
        return text

    start = text.find("{")
    if start < 0:
        return text

    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]

    return text


def _env_bool(name: str, default: bool) -> bool:
    raw_value = os.environ.get(name)
    if raw_value is None or raw_value.strip() == "":
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _retry_delay_seconds(*, attempt_count: int, base_delay_seconds: int, max_delay_seconds: int) -> int:
    exponent = max(0, attempt_count - 1)
    return min(max_delay_seconds, base_delay_seconds * (2**exponent))
