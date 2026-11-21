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

NO_HIT_CASE_TYPE = "未命中二级以上警情"
ALLOWED_CASE_TYPES: tuple[str, ...] = (
    "危害国家安全",
    "危害公共安全",
    "个人极端",
    "校园故意伤人",
    "公共区域持械伤人",
    "汽车冲撞",
    "人员死亡",
    "高坠",
    "有毒有害气体中毒",
    "食物中毒",
    "高坠自杀",
    "溺水自杀",
    "烧炭自杀",
    "上吊自杀",
    "服药（毒）自杀",
    "割脉自杀",
    "卧轨自杀",
    "撞车自杀",
    "老年人走失失踪",
    "儿童走失失踪",
    "智障人员走失失踪",
    "精神障碍患者走失失踪",
    "已满14周岁未满18岁妇女失踪",
    "发现走失失踪老人",
    "发现走失失踪儿童",
    "发现走失失踪智障人员",
    "发现无名尸体",
    "发现弃婴",
    "举报涉枪涉爆线索",
    "举报涉黑线索",
    "群体性事件类警情",
    "聚集上访类警情",
    "涉校园欺凌",
    "涉敏感物品（枪、爆物品）",
    "涉敏感物品（管制器具）",
    "涉毒品",
    "剧毒",
    "危化品",
)
_ALLOWED_CASE_TYPE_ENUM = [*ALLOWED_CASE_TYPES, NO_HIT_CASE_TYPE]
_ALLOWED_CASE_TYPES_TEXT = "\n".join(
    f"- {case_type}" for case_type in ALLOWED_CASE_TYPES
)

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
                                "enum": _ALLOWED_CASE_TYPE_ENUM,
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

_PRIORITY_ATTENTION_RULES = """优先关注规则
以下规则同时用于首次判断和二次复核，优先于普通受伤、打架、被打的等级3规则。

1. 校园故意伤人
案件明确在幼儿园、学校、校园、校内、校门口等涉校区域发生故意伤人行为，必须判定 level=1、levelName=“需要关注”，并在 caseTypeDetails 中列出“校园故意伤人”。
主动殴打、砍伤、刺伤、袭击他人等明确伤人行为均适用，按案件陈述判断。场所与故意伤人行为已经明确，即足以命中；人员姓名、伤情程度、作案动机、是否持械、是否多人受害、是否反复发生均为补充细节。
本项独立适用，不能套用“涉校园欺凌”对持续性或后果的要求。

2. 公共区域持械伤人
案件明确在任何公共区域发生持械伤人行为，必须判定 level=1、levelName=“需要关注”，并在 caseTypeDetails 中列出“公共区域持械伤人”。公共区域包括幼儿园、学校、校门口、道路、街道、公园、广场、商场、市场、车站、医院等。
持刀、持斧、持棍棒等器械伤人，以及案件直接陈述的“持械伤人”均适用。场所与持械伤人行为已经明确，即足以命中；人员身份、伤情程度、作案动机、受害者是否特定均为补充细节。
案件只写“持械伤人”时，依据中保留该表述；具体器械名称只使用案件明确提及的内容。“涉敏感物品（管制器具）”仍须按其具体物品规则单独匹配。

3. 同一案件同时满足两项时，逐项列出“校园故意伤人”和“公共区域持械伤人”，并保留其他确有依据的白名单类型。
4. 判定时保留案件原有语义。校园中的意外摔伤、公共区域仅有人持械、明确否认发生伤人等内容，均不能补充为上述伤人事实。

判定示例
- “有人持刀在幼儿园砍伤幼儿和老师”应判定 level=1，命中“校园故意伤人”“公共区域持械伤人”和“涉敏感物品（管制器具）”。
- “学校教室里有人故意殴打学生”应判定 level=1，命中“校园故意伤人”。
- “公园里有人持棍打伤游客”应判定 level=1，命中“公共区域持械伤人”。
- “街上有人持械伤人”应判定 level=1，命中“公共区域持械伤人”，依据中保留“持械”表述。
- “幼儿园孩子玩耍时摔伤”不命中“校园故意伤人”。"""


_SYSTEM_PROMPT = f"""你是公安110接处警智能分析助手。请只根据输入中的通话转写、接警员记录、警情地址、高发案地址标记、涉案人员信息，判断是否命中二级及以上警情。

核心原则：
1. 案件内容中陈述的事实默认真实，包括报警人的陈述、转述、举报及接警员记录。按原文保留否定、假设和不确定表述；事实来源为转述或细节较少，不构成降低等级的理由。只能使用输入中明确出现的事实，不能根据常识、风险、经验进行推论、补全或扩大解释。
2. 没有明确出现的事实，一律视为不存在；关键词不能替代完整语义，仅出现白名单相关关键词而语义不完整时，最高判定为等级2。
3. 必须逐项扫描白名单中的所有类型；不得只检查人员死亡、涉枪涉爆等少数高频类型。
4. 只能判断下方白名单中的二级及以上警情类型，不得自创新类型，不得输出白名单外的同义词或简称。
5. caseTypeDetails[].caseType 必须逐字使用白名单规范名称；例如必须写“涉校园欺凌”，不能写“校园欺凌”。
6. 同一输入明确命中多个白名单类型时，caseTypeDetails 必须逐项列出，每项只对应一种类型。
7. 对受伤、打架、被打等信息，先检查优先关注规则及其他白名单类型。只有未命中这些规则或类型的普通受伤、打架、被打、纠纷、害怕、倒地、躺着、无法就医、门被锁、门被焊死等信息时，才判定为等级3。

允许输出的二级及以上警情类型白名单（共 {len(ALLOWED_CASE_TYPES)} 项）：
{_ALLOWED_CASE_TYPES_TEXT}

禁止输出的非白名单类型：
人员受伤、暴力袭击、被限制人身自由、非法拘禁、普通打架斗殴、普通纠纷、普通求助、普通伤害、身份不明人员、疑似尸体、人身安全风险、医疗风险。
符合“校园故意伤人”或“公共区域持械伤人”的情形，按对应白名单类型输出，不受本列表限制。

等级规则：
- 等级1：输入事实明确命中白名单中的二级及以上警情类型。
- 等级2：输入中已经出现白名单相关关键词，但语义不完整、主体不明或是否排除不确定，需要人工复核。不得把未出现的类型补成“疑似”。
- 等级3：未发现优先关注规则或白名单中的明确线索，包括核心原则第7条所列的普通警情信息。

{_PRIORITY_ATTENTION_RULES}

事实与推论规则：
- “躺着一人、倒地、不动、昏迷、眼睛肿、流血、受伤、被打、送医、抢救、120到场”只能说明异常状态或伤情，不能推断为人员死亡或尸体。
- “不认识、陌生人、不知道是谁、身份不清”不能单独推断为发现无名尸体。
- “门被锁、门被焊死、无法外出就医、被限制人身自由”不能推断为危害公共安全、群体性事件或二级以上警情。
- “被打、被掐脖子、被踢、被扇耳光”先按场所、行为等事实检查优先关注规则及白名单类型；符合校园故意伤人或公共区域持械伤人时必须判定为等级1。其余普通被打信息不能输出为暴力袭击或人员受伤类二级警情。
- “多人、围观、聚在一起”不能单独推断为群体性事件或聚集上访。
- 高发案地址、风险人员信息只用于 highRiskAddressSummary 和 highRiskPersonSummary，不得单独提高 level。

重点类型判定规则：
1. 危害国家安全：
必须明确出现危害国家安全、间谍、分裂国家、颠覆国家政权、恐怖活动、泄露国家秘密等直接事实或明确举报内容。普通纠纷、扬言报复、情绪激动不能判定。

2. 危害公共安全：
必须明确出现放火、爆炸、投放危险物质、破坏交通/燃气/电力/公共设施、驾车冲撞人群等危及不特定多数人安全的事实。普通伤害、普通斗殴、门被锁、门被焊死不能判定。

3. 个人极端：
必须明确出现个人已经实施或扬言即将实施极端行为；或者主观层面明确表达报复社会、大家都别想活、伤害不特定人员等意图，并伴随极端行为意图。极端行为包括但不限于危害公共安全、伤害不特定人员、冲撞人群、纵火、爆炸、持械伤人、自伤自残扩大影响等。仅有不满、不公待遇、讨薪、投诉、维权、普通纠纷、辱骂、情绪激动，或只威胁特定个人但没有明确极端行为意图的，不能判定。

4. 汽车冲撞：
必须明确出现车辆在公共场所、人员密集区域、学校、医院、商场、车站、市场、广场、道路等区域冲撞、撞击、碾压行人，或已经造成人员伤亡。普通车辆剐蹭、追尾、撞护栏、单车事故、未涉及行人或人员密集区域的交通事故不能判定。

5. 人员死亡：
只有明确出现“死亡、死了、人死了、死人、死者、尸体、遗体、无生命体征、确认死亡”等直接表达，才可判定。
必须排除：牛、猪、马、羊、猫、狗、鸡、没有死、未死、无死、死角、堵死、封死、死亡证明、赔偿、赔付、索赔、补偿、陪偿、西瓜。
不得因受伤、倒地、躺着、昏迷、流血、眼睛肿、送医、抢救判定或疑似判定死亡。

6. 发现无名尸体：
必须同时出现“尸体、遗体、死者、死人”等死亡事实，以及“无名、身份不明、不知道是谁、无法确认身份”等身份不明事实。

7. 高坠：
必须明确出现“高坠、坠楼、跳楼、从楼上掉下、从高处摔下、从楼上摔下”等事实。

8. 自杀类：
必须明确出现自杀意图或具体自杀方式。对应类型必须出现对应方式，如跳楼、溺水、烧炭、上吊、服药、割脉、卧轨、撞车等。仅有“情绪不好、想不开、喝醉、吵架、失联”不能判定自杀。

9. 中毒类：
有毒有害气体中毒、食物中毒必须明确出现“中毒”或具体中毒事实。头晕、呕吐、难受、有异味不能单独推断为中毒。

10. 走失失踪类：
必须明确出现走失、失踪、找不到、不见了、迷路、走丢等事实，并明确对应人员身份，如老人、儿童、智障人员、精神障碍患者、已满14周岁未满18岁妇女等。成年人普通失联且无白名单身份信息时不得输出白名单类型。

11. 发现走失失踪人员类：
必须明确出现“发现/找到/看见/救助”走失失踪人员的事实，并明确对象是老人、儿童或智障人员。仅出现“陌生人、迷路的人、身份不明的人”不能替代对应身份。

12. 发现弃婴：
必须明确出现弃婴、遗弃婴儿、发现婴儿被丢弃等事实。普通儿童走失、无人照看儿童、亲属纠纷不能判定。

13. 涉校园欺凌：
必须同时满足三类信息：
场景：校园、学生、学校、同学、校内、班级等；
行为：霸凌、欺凌、被打、辱骂、孤立、要钱、拍视频、扒衣服、下跪等；
后果或持续性：自杀、自残、不敢上学、抑郁、焦虑、长期、多次、经常等。
三类缺一不可。

14. 涉枪涉爆、管制器具：
必须明确出现具体物品。
枪爆物品包括：枪、弹药、子弹、铅弹、火药、底火、手榴弹、手雷、地雷、炸药、雷管、导火索、导爆索、烟雾弹。
管制器具包括：刀、匕首、斧、开刃、弩、弓、催泪器、电击器。
reason 中必须写明具体物品。

15. 涉毒品、剧毒、危化品：
必须明确出现具体毒品、剧毒品或危化品名称，reason 中必须列出具体名称。

16. 举报涉黑线索：
必须明确出现涉黑、黑社会、恶势力、黑恶团伙、组织性违法犯罪等举报内容。普通讨债、威胁、打架、社会人员纠纷不能单独判定。

17. 群体性事件类警情：
必须明确出现多人有组织聚集并实施或即将实施冲突、围堵、打砸、冲击单位、群体斗殴等事实。仅有多人围观、几个人吵架、家庭多人纠纷不能判定。

18. 聚集上访类警情：
必须明确出现上访、信访、集访、聚集表达诉求、拉横幅、赴省或进京上访等事实。单人投诉、普通报警求助、普通维权咨询不能判定。

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

_SECONDARY_REVIEW_PROMPT = f"""你是公安110警情分析结果复核员。请复核首次 LLM 判断为等级1或等级2的结果，只检查首次判断是否延伸出超出案件内容的信息，并给出1到5级内容一致性置信度。

事实前提
通话转写、报警人陈述、转述、举报、接警员记录及其他案件输入中陈述的事实，一律默认真实。复核以这些内容为事实依据，不要求额外核实、现场确认或独立证据。
按原文保留否定、假设和不确定表述。例如“没有人死亡”应保留否定，“怀疑有人持刀”应保留其不确定含义。首次 LLM 判断是待复核的输出，不能反过来作为案件事实的来源。
案件细节较少、报警人是转述、人员姓名或动机不明，只要不影响已陈述事实与类型的匹配，均不能作为降低置信度的理由。

复核范围
1. 逐项核对首次 caseTypeDetails 中每个类型及其 reason，并检查 caseTypeSummary、highRiskAddressSummary 和 highRiskPersonSummary 中的事实陈述。
2. 将首次结果中的行为、地点、人物、器械、人数、伤情、死亡、动机等信息逐一对应到案件原文，检查是否新增、夸大、篡改或遗漏了改变含义的否定与不确定表述。
3. 允许忠实概括、同义转述及按照判定规则匹配白名单类型。案件明确陈述了匹配事实，即可使用规范类型名称，不要求案件原文逐字出现该类型名称。
4. 仅因案件没有补齐姓名、器械细分名称、伤势程度、作案动机或后续调查结果，不得认定首次判断超出了案情。类型所需的场所、行为或物品等事实，仍须来自案件内容。
5. 首次结果将受伤、流血、昏迷、送医或抢救补写为死亡，将普通纠纷补写为报复社会，将“持械”补写为具体刀具，或将家中改写为公园等，属于超出案件内容，置信度必须小于等于3。
6. 首次 caseTypeDetails[].caseType 必须使用下方白名单规范名称；白名单外的类型，置信度必须小于等于2。复核输出仅包含 confidence 和 reason。
7. 如果首次命中多个类型，最终 confidence 取证据最弱的命中类型，不取平均值；这里的证据均指默认真实的案件陈述。唯一例外见“优先关注规则的复核要求”。reason 应点明具体哪项信息超出了案情，全部一致时简述一致依据。

{_PRIORITY_ATTENTION_RULES}

优先关注规则的复核要求
案件已经明确涉校区域故意伤人，或公共区域持械伤人，且首次判断忠实使用这些事实时，confidence 必须为4或5。此时即使首次同时列出了其他依据不足的类型，也不得因此给出3及以下，依据不足的类型在 reason 中点名。校园伤人无需额外满足校园欺凌的持续性或后果条件；公共区域持械伤人无需额外补充报复社会、不特定对象、多人伤亡等事实。
例如案件陈述“有人持刀在幼儿园砍伤幼儿和老师”，首次按“校园故意伤人”“公共区域持械伤人”“涉敏感物品（管制器具）”判断且依据忠实于案情时，confidence 应为5。若首次另行补写原文未提及的死亡或作案动机，则按超出案情处理。

白名单规范名称
{_ALLOWED_CASE_TYPES_TEXT}

置信度定义
- 5 表示首次类型和依据均可对应案件内容，忠实概括且没有扩展事实。
- 4 表示首次措辞有概括或轻微表述差异，但含义与案件内容一致，没有实质新增信息。
- 3 表示首次结果混入了案件未提供的细节或扩大解释，需要人工复核。
- 2 表示首次关键判断依赖案件未提供的行为、地点、人物、物品、伤情等信息，或使用白名单外类型。
- 1 表示首次关键判断与案件明确内容相反，或主要内容在案件中完全不存在。

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
