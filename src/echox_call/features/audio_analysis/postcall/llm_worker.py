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

_SYSTEM_PROMPT = """你是公安110接处警智能分析助手。请只根据输入中的通话转写、接警员记录、警情地址、高发案地址标记、涉案人员信息，判断是否命中二级及以上警情。

核心原则：
1. 只能使用输入中明确出现的事实，不能根据常识、风险、经验进行推论、补全或扩大解释。
2. 没有明确出现的事实，一律视为不存在。
3. 只能判断下方白名单中的二级及以上警情类型，不得自创新类型。
4. 只有受伤、打架、被打、纠纷、害怕、倒地、躺着、无法就医、门被锁、门被焊死等普通警情信息时，必须判定为等级3。

允许输出的二级及以上警情类型白名单：
危害国家安全、危害公共安全、人员死亡、高坠、有毒有害气体中毒、食物中毒、高坠自杀、溺水自杀、烧炭自杀、上吊自杀、服药（毒）自杀、割脉自杀、卧轨自杀、撞车自杀、老年人走失失踪、儿童走失失踪、智障人员走失失踪、精神障碍患者走失失踪、已满14周岁未满18岁妇女失踪、发现走失失踪老人、发现走失失踪儿童、发现走失失踪智障人员、发现无名尸体、发现弃婴、举报涉枪涉爆线索、举报涉黑线索、群体性事件类警情、聚集上访类警情、涉校园欺凌、涉敏感物品（枪、爆物品）、涉敏感物品（管制器具）、涉毒品、剧毒、危化品。

禁止输出的非白名单类型：
人员受伤、暴力袭击、被限制人身自由、非法拘禁、普通打架斗殴、普通纠纷、普通求助、普通伤害、身份不明人员、疑似尸体、人身安全风险、医疗风险。

等级规则：
- 等级1：输入事实明确命中白名单中的二级及以上警情类型。
- 等级2：输入中已经出现白名单相关关键词，但语义不完整、主体不明或是否排除不确定，需要人工复核。不得把未出现的类型补成“疑似”。
- 等级3：未发现白名单中的明确线索。仅有受伤、打架、被打、害怕、纠纷、求助、无法外出、门被锁、门被焊死等信息时，判定为等级3。

事实与推论规则：
- “躺着一人、倒地、不动、昏迷、眼睛肿、流血、受伤、被打、送医、抢救、120到场”只能说明异常状态或伤情，不能推断为人员死亡或尸体。
- “不认识、陌生人、不知道是谁、身份不清”不能单独推断为发现无名尸体。
- “门被锁、门被焊死、无法外出就医、被限制人身自由”不能推断为危害公共安全、群体性事件或二级以上警情。
- “被打、被掐脖子、被踢、被扇耳光”不能输出为暴力袭击或人员受伤类二级警情，除非同时明确命中白名单类型。
- “多人、围观、聚在一起”不能单独推断为群体性事件或聚集上访。
- 高发案地址、风险人员信息只用于 highRiskAddressSummary 和 highRiskPersonSummary，不得单独提高 level。

重点类型判定规则：
1. 人员死亡：
只有明确出现“死亡、死了、人死了、死人、死者、尸体、遗体、无生命体征、确认死亡”等直接表达，才可判定。
必须排除：牛、猪、马、羊、猫、狗、鸡、没有死、未死、无死、死角、堵死、封死、死亡证明、赔偿、赔付、索赔、补偿、陪偿、西瓜。
不得因受伤、倒地、躺着、昏迷、流血、眼睛肿、送医、抢救判定或疑似判定死亡。

2. 发现无名尸体：
必须同时出现“尸体、遗体、死者、死人”等死亡事实，以及“无名、身份不明、不知道是谁、无法确认身份”等身份不明事实。

3. 高坠：
必须明确出现“高坠、坠楼、跳楼、从楼上掉下、从高处摔下、从楼上摔下”等事实。

4. 自杀类：
必须明确出现自杀意图或具体自杀方式。对应类型必须出现对应方式，如跳楼、溺水、烧炭、上吊、服药、割脉、卧轨、撞车等。

5. 中毒类：
有毒有害气体中毒、食物中毒必须明确出现“中毒”或具体中毒事实。头晕、呕吐、难受、有异味不能单独推断为中毒。

6. 走失失踪类：
必须明确出现走失、失踪、找不到、不见了、迷路、走丢等事实，并明确对应人员身份，如老人、儿童、智障人员、精神障碍患者等。

7. 涉校园欺凌：
必须同时满足三类信息：
场景：校园、学生、学校、同学、校内、班级等；
行为：霸凌、欺凌、被打、辱骂、孤立、要钱、拍视频、扒衣服、下跪等；
后果或持续性：自杀、自残、不敢上学、抑郁、焦虑、长期、多次、经常等。
三类缺一不可。

8. 涉枪涉爆、管制器具：
必须明确出现具体物品。
枪爆物品包括：枪、弹药、子弹、铅弹、火药、底火、手榴弹、手雷、地雷、炸药、雷管、导火索、导爆索、烟雾弹。
管制器具包括：刀、匕首、斧、开刃、弩、弓、催泪器、电击器。
reason 中必须写明具体物品。

9. 涉毒品、剧毒、危化品：
必须明确出现具体毒品、剧毒品或危化品名称，reason 中必须列出具体名称。

输出要求：
- caseTypeSummary 必须是 summary 的第一条，必须以“分析总结：”开头，只写结论和命中的白名单警情类型，不解释原因。
- 未命中时写：“分析总结：未发现明确二级以上警情。”
- caseTypeDetails 每项只对应一种命中的白名单警情类型。
- caseTypeDetails[].caseType 只能填写白名单类型；未命中时填写“未命中二级以上警情”。
- caseTypeDetails[].reason 只写一句简短事实依据，50字以内，不得写推测、可能、疑似、可能导致、符合特征等表述，不得在原因中写“可能死亡”。
- 如果未命中，caseTypeDetails 返回一条：caseType=“未命中二级以上警情”，reason=“现有信息未出现明确二级及以上警情线索。”
- 如果未命中，level 必须为3，levelName 必须为“暂无明显线索”。
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

_SECONDARY_REVIEW_PROMPT = """你是公安110二级及以上警情复核员。请对首次 LLM 判断为等级1或等级2的结果进行二次证据审查，并给出1到5级置信度。

复核目标：
1. 只评价输入中是否明确提及首次判断对应的白名单警情类型。
2. 不允许根据受伤、打架、倒地、昏迷、流血、送医、抢救、害怕、门被锁、门被焊死、身份不清等普通警情信息推断二级及以上警情。
3. 如果首次判断存在基于推断、常识、风险、经验、可能后果的评定，置信度必须小于等于3。
4. 如果首次把人员重伤、倒地、昏迷、送医、抢救等推断为人员死亡，置信度必须小于等于3。
5. 如果首次判断缺少直接事实，只是关键词不完整、主体不明或是否排除不确定，置信度必须小于等于3。

置信度定义：
- 5：输入中有直接、完整、无排除语境的明确事实。
- 4：输入中有明确事实，但细节略少，不影响命中。
- 3：有相关词或线索，但语义不完整、主体不明或需要人工复核。
- 2：主要依赖推断、联想或风险扩大解释。
- 1：输入中没有对应事实，或明显不应命中。

只输出 JSON，不要输出 Markdown 或解释文字。"""


_SECONDARY_REVIEW_OUTPUT_INSTRUCTION = """JSON 对象格式：
{
  "confidence": 1 | 2 | 3 | 4 | 5,
  "reason": "一句50字以内的复核依据"
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

        response = self._create_chat_completion(request_payload)

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

        secondary_confidence: int | None = None
        secondary_reason: str | None = None
        if level in {1, 2}:
            review = self._review_attention_confidence(job=job, initial_output=raw)
            secondary_confidence = review["confidence"]
            secondary_reason = review["reason"]
            if level == 1 and secondary_confidence <= 3:
                level = 2
                level_name = ATTENTION_LEVEL_NAMES[2]

        return LlmAnalysisOutput(
            level=level,
            level_name=level_name,
            case_type_summary=raw.get("caseTypeSummary") or None,
            case_type_details=_normalize_case_type_details(raw.get("caseTypeDetails")),
            high_risk_address_summary=raw.get("highRiskAddressSummary") or None,
            high_risk_person_summary=raw.get("highRiskPersonSummary") or None,
            secondary_review_confidence=secondary_confidence,
            secondary_review_reason=secondary_reason,
            llm_model=self.settings.model,
        )

    def _create_chat_completion(self, request_payload: dict[str, Any]) -> Any:
        try:
            return self._client.chat.completions.create(**request_payload)
        except openai.RateLimitError as exc:
            raise LlmWorkerError("LLM_RATE_LIMITED", f"rate limited: {exc}", retryable=True) from exc
        except openai.APITimeoutError as exc:
            raise LlmWorkerError("LLM_TIMEOUT", f"timeout: {exc}", retryable=True) from exc
        except openai.APIError as exc:
            raise LlmWorkerError("LLM_API_ERROR", f"API error: {exc}", retryable=True) from exc

    def _review_attention_confidence(
        self,
        *,
        job: ClaimedLlmJob,
        initial_output: dict[str, Any],
    ) -> dict[str, Any]:
        response = self._create_chat_completion({
            "model": self.settings.model,
            "max_tokens": min(self.settings.max_tokens, 512),
            "messages": [
                {"role": "system", "content": _SECONDARY_REVIEW_PROMPT},
                {"role": "user", "content": _build_secondary_review_message(job, initial_output)},
            ],
        })
        choice = response.choices[0] if response.choices else None
        if choice is None:
            raise LlmWorkerError(
                "LLM_NO_OUTPUT",
                "LLM secondary review response did not contain choices",
                retryable=False,
            )
        return _extract_secondary_review_output(choice)

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
    parts = _build_case_context_parts(job)
    parts.append(_TOOL_OR_JSON_OUTPUT_INSTRUCTION if use_tools else _JSON_ONLY_OUTPUT_INSTRUCTION)
    return "\n\n".join(parts)


def _build_secondary_review_message(job: ClaimedLlmJob, initial_output: dict[str, Any]) -> str:
    parts = _build_case_context_parts(job)
    parts.append("【首次判断结果】\n" + json.dumps(initial_output, ensure_ascii=False))
    parts.append(_SECONDARY_REVIEW_OUTPUT_INSTRUCTION)
    return "\n\n".join(parts)


def _build_case_context_parts(job: ClaimedLlmJob) -> list[str]:
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

    return parts


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


def _extract_secondary_review_output(choice: Any) -> dict[str, Any]:
    message = choice.message
    content = getattr(message, "content", None)
    if not isinstance(content, str) or not content.strip():
        raise LlmWorkerError(
            "LLM_NO_VALID_OUTPUT",
            "LLM secondary review returned no JSON content",
            retryable=False,
        )

    raw = _loads_analysis_json(
        _extract_json_object_text(_strip_json_code_fence(content)),
        source="secondary review content",
    )
    confidence = _normalize_review_confidence(raw.get("confidence"))
    if confidence is None:
        raise LlmWorkerError(
            "LLM_INVALID_OUTPUT",
            f"invalid secondary review confidence: {json.dumps(raw, ensure_ascii=False)}",
            retryable=False,
        )
    reason = _compact_review_reason(raw.get("reason"))
    if not reason:
        reason = "二次复核未返回有效依据。"
    return {"confidence": confidence, "reason": reason}


def _normalize_review_confidence(value: Any) -> int | None:
    try:
        confidence = int(value)
    except (TypeError, ValueError):
        return None
    return confidence if 1 <= confidence <= 5 else None


def _compact_review_reason(value: Any) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        return ""
    max_length = 50
    if len(text) > max_length:
        text = text[:max_length].rstrip("，,、；;。") + "..."
    elif text[-1] not in "。！？.!?...":
        text = f"{text}。"
    return text


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
