# Postcall 模型输出与规则线索数据合同

本文档约定报警音频分析当前数据库和 API 结果结构。

模型真实输出字段、中文注释和 API/数据库逐项对应关系，以 [postcall-model-raw-output-checklist.md](/Users/xumaowen/Workspace/Develop/PyCharm/echox-call/docs/postcall-model-raw-output-checklist.md:1) 为准。

当前阶段做两件事：数据库保留模型原始输出时间线，并用确定性规则生成对外 `level`、`levelName` 和 `reviewSegments[]`。当前不做风险等级、不做大模型生成、不做融合结论。

## 1. 三层边界

必须严格区分三层：

| 层级 | 当前是否做 | 说明 |
| --- | --- | --- |
| 模型原始输出 | 做 | pyannote 说话人分段；BEATs 527 类 AudioSet 分数；WavLM 9 类情绪、3 个连续维度；17 类细粒度分数只入库保存 |
| 机械整理 | 做 | 按时间窗切片、说话人片段切片、排序、取 top-k、统一字段名 |
| 规则线索 | 做 | 从模型分数中整理原子线索和复合线索，输出 `level`、`levelName` 和 `reviewSegments[]` |
| 风险融合 | 暂不做 | 不输出高/中/低风险等级，不给处置建议 |

当前允许的处理只有：

- 时间窗切片；
- 说话人分段；
- top-k 排序；
- 字段名统一；
- 配置化规则线索判断；
- 保存模型名、版本、原始标签和原始分数用于排查。

说明：对外 `score` 使用模型输出经 sigmoid / softmax 后的概率或维度值，范围为 `0` 到 `1`。模型未归一化 logits 可以保存到数据库内部 `evidence` 或 `internal_payload`，默认不对外返回。

当前不做：

- `confidenceLevel` 阈值分档；
- `riskLevel` 风险等级判断；
- `keySegments` 重点片段推荐；
- 处置建议、警情定级、责任主体身份判断；
- 报警人 / 接警员身份推断。

## 2. 模型真实输出

### 2.1 pyannote 说话人分段

pyannote 直接输出：

```text
若干个 start/end/speakerLabel 时间段
```

API 只返回中性字段：

```json
{
  "speakerLabel": "SPEAKER_00",
  "speakerRole": "未知",
  "roleSource": "diarization_only"
}
```

说明：

- `speakerLabel` 是模型聚类标签，不代表真实身份；
- 当前没有 ASR 时间戳、声道、固定开场白或声纹库时，不判断报警人或接警员；
- `speakerRole = 未知` 表示已分出说话人，但未完成业务身份映射。

### 2.2 BEATs

BEATs 直接输出：

```text
527 个 AudioSet 标签的概率
```

数据库内部保存每个输出项时，应保留以下原始语义：

```json
{
  "index": 14,
  "mid": "/m/03qc9zr",
  "eventNameEn": "Screaming",
  "eventNameZh": "尖叫声",
  "score": 0.72
}
```

说明：

- `index` 是 AudioSet 输出向量下标；
- `mid` 是 AudioSet 原始 MID；
- `eventNameEn` 是 AudioSet 原始英文标签；
- `eventNameZh` 是中文显示名，只做展示翻译，不代表业务结论；
- `score` 是模型原始分数；
- 第一版只做中英文显示名映射，不做标签归并、不判断风险。
- 对外 API 的 `audioEventScores[]` 只返回 `eventNameEn`、`eventNameZh`、`score`；`index` 和 `mid` 只保存在 `postcall_timeline_segments.internal_payload`。

### 2.3 WavLM

WavLM 直接输出：

```text
9 类情绪分数
17 类细粒度分数
arousal / valence / dominance 三个连续维度
```

本地已确认的 9 类情绪：

```text
Anger, Contempt, Disgust, Fear, Happiness, Neutral, Sadness, Surprise, Other
```

中英文显示名清单见 [postcall-wavlm-output-labels.csv](/Users/xumaowen/Workspace/Develop/PyCharm/echox-call/docs/postcall-wavlm-output-labels.csv:1)。

9 类情绪输出：

```json
{
  "index": 3,
  "emotionNameEn": "Fear",
  "emotionNameZh": "恐惧",
  "score": 0.68
}
```

17 类细粒度输出本地没有可靠标签名，第一版只能按 index 保存：

```json
{
  "index": 0,
  "score": 0.13
}
```

说明：17 类细粒度分数只建议入库保存，不建议第一版对外 API 返回。
对外 API 的 `voiceEmotionScores[]` 只返回 `emotionNameEn`、`emotionNameZh`、`score`；9 类情绪的 index 只保存在内部明细。

连续维度输出：

```json
{
  "arousal": {
    "dimensionNameEn": "Arousal",
    "dimensionNameZh": "唤醒度",
    "value": 0.74
  },
  "valence": {
    "dimensionNameEn": "Valence",
    "dimensionNameZh": "情绪效价",
    "value": 0.22
  },
  "dominance": {
    "dimensionNameEn": "Dominance",
    "dimensionNameZh": "控制感",
    "value": 0.31
  }
}
```

## 3. API 返回结构

任务完成后，对外查询接口实际返回 `postcall_analysis_results.api_result_payload` 中保存的 `data` 快照：

```json
{
  "jobId": "job_xxx",
  "jjdh": "JJD_20260408_0001",
  "state": "completed",
  "level": 1,
  "levelName": "需要关注",
  "reviewSegments": [
    {
      "startSec": 35.0,
      "endSec": 45.0,
      "result": "疑似尖叫"
    }
  ]
}
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `level` | 规则线索层输出的关注等级；`1=需要关注`、`2=建议复核`、`3=暂无明显线索`，不表示风险等级 |
| `levelName` | 关注等级中文名，必须与 `level` 一致 |
| `reviewSegments[]` | 对外复核片段，只包含 `startSec`、`endSec`、`result`；仅 `level=1/2` 返回 |

不建议返回：

```json
{
  "analysis": {},
  "audio": {},
  "beats": [],
  "wavlm": [],
  "keySegments": [],
  "riskLevel": "high",
  "confidenceLevel": "high"
}
```

原因：

- `analysis`、`audio` 属于内部任务元信息，不是模型结果，第一版对外不返回；
- `beats`、`wavlm` 暴露底层模型名称；
- `keySegments` 属于重点片段筛选结果，第一版不返回；
- `riskLevel` 属于风险融合结果，第一版不返回；
- `confidenceLevel` 是阈值分档，不是模型原始输出。

## 4. 数据库存储映射

数据库保存两类信息：

1. 与 API 完全一致的对外结果快照和时间线片段结构；
2. API 不返回的内部排查信息。

### 4.1 postcall_analysis_results.api_result_payload

保存对外 GET 查询接口返回的 `data` 部分快照，不包含统一响应包装里的 `code`、`message`、`timestamp`。

规则线索模式下，结果表字段如下：

| 数据库字段 | 当前取值 | 说明 |
| --- | --- | --- |
| `attention_level` | `1` / `2` / `3` | 规则线索层输出的关注等级 |
| `attention_level_name` | `需要关注` / `建议复核` / `暂无明显线索` | 关注等级中文名 |
| `rule_version` | 规则版本 | 例如 `postcall_attention_rules_v6` |
| `matched_rule_codes` | JSON array | 保存命中的规则编码 |
| `fusion_trace` | JSON object | 保存内部专家规则过程，包括 `attentionConclusion`、复合线索、被压制线索、模型冲突、不确定性和调试信息；不直接对外返回 |
| `api_result_payload` | API `data` object | 保存 `jobId`、`jjdh`、`state`、`level`、`levelName`；仅 `level=1/2` 时保存 `reviewSegments` |
| `api_result_version` | `postcall_job_result_v1` | API 结果快照结构版本 |

### 4.2 postcall_timeline_segments

该表保存内部模型时间线。当前外部 API 不返回 `timeline[]`，只在 `level=1/2` 时返回由规则层整理出的 `reviewSegments[]`。

| API 字段 | 数据库字段 | 说明 |
| --- | --- | --- |
| `timeline[].segmentId` | `segment_id` | 时间窗编号 |
| `timeline[].startSec` | `start_sec` | 开始秒 |
| `timeline[].endSec` | `end_sec` | 结束秒 |
| `timeline[].speakerLabel` | `speaker_label` | pyannote 说话人聚类标签；全局声音事件为空 |
| `timeline[].speakerRole` | `speaker_role` | 当前为 `未知`；后续有可靠映射依据后才可写 `报警人` 或 `接警员` |
| `timeline[].roleSource` | `role_source` | 当前主要为 `global_audio`、`diarization_only` 或 `energy_vad` |
| `timeline[].audioEventScores` | `audio_event_scores` | BEATs 对外 top-k 分数，单项包含 `eventNameEn`、`eventNameZh`、`score` |
| `timeline[].voiceEmotionScores` | `voice_emotion_scores` | WavLM 9 类情绪分数，单项包含 `emotionNameEn`、`emotionNameZh`、`score` |
| `timeline[].voiceEmotionDimensions` | `voice_emotion_dimensions` | WavLM 连续维度，字段内保存中英文维度名和 `value` |
| API 默认不返回 | `voice_detailed_scores` | WavLM 17 类细粒度分数，仅数据库内部保存 |
| API 默认不返回 | `internal_payload` | BEATs `index`、AudioSet `mid`、WavLM emotion index、模型版本、窗口参数、BEATs 527 类完整分数等内部排查信息 |
| 不对外返回 | `internal_payload` | 内部排查扩展，如完整向量、模型窗口参数 |

## 5. 规则线索层 v6

规则配置文件为 `config/postcall_attention_rules.yaml`，当前版本为 `postcall_attention_rules_v6`。规则层只读取 `timeline[]`，不会读取 `bjnr`、`asrResult`，也不会调用大模型生成原因。

规则处理顺序：

1. 从 `audioEventScores`、`voiceEmotionScores`、`voiceEmotionDimensions` 抽取原子线索。
2. 同类线索按时间合并。
3. 生成复合线索，例如尖叫伴随冲击、婴幼儿哭声伴随冲突、异常呼吸伴随语音异常、破裂损坏伴随高唤醒等。
4. 处理模型关系：背景声音异常但人声平稳、多说话人或切片碎片化等可进入 `level=2`；单纯人声异常但背景事件不足只进入内部追踪，不对外生成复核片段。
5. 按 30 秒窗口生成密度线索，例如 `dense_conflict_signals_30s`、`dense_distress_signals_30s`。
6. 抑制低价值单点线索，例如低分单次喊叫、普通敲门、低分单次撞击，以及单独出现的语音连续维度波动。
7. 若已生成复合线索，优先把更具体的复合线索写入 `reviewSegments[]`，减少同一时间段内原子线索重复展示。
8. 把外部简化结果写入 `api_result_payload`，把内部完整规则过程写入 `fusion_trace` 和 `postcall_review_segments.payload`。

连续维度的使用边界：

- `arousal >= 0.68` 可作为语音激动/紧张辅助线索；
- `valence <= 0.35` 可作为低情绪效价辅助线索；
- `dominance <= 0.35` 可作为低控制感辅助线索；
- 连续维度单独出现时不生成对外复核片段；
- 多个语音状态异常接近出现时，默认只写入内部 `fusion_trace`，不生成对外 `reviewSegments[]`；
- 语音状态只有与哭泣、尖叫、撞击、异常呼吸、破裂、门体冲击等明确声音事件组合时，才参与生成对外复核片段。

规则重算 CLI：

```bash
PYTHONPATH=src python -m echox_call.cli.attention_rules --job-id job_xxx
PYTHONPATH=src python -m echox_call.cli.attention_rules --all-completed
PYTHONPATH=src python -m echox_call.cli.attention_rules --job-id job_xxx --dry-run
```

重算只更新 `postcall_analysis_results` 的 `level`、`levelName`、`rule_version`、`matched_rule_codes`、`api_result_payload`，以及 `postcall_review_segments`，不会重新跑模型。

## 6. 后续风险层

后续如果要做报警场景优化，应在规则线索层之上新增明确的风险融合或业务分类模型。那一阶段才允许输出：

- 疑似冲突；
- 疑似痛苦；
- 疑似危险升级；
- 风险等级；
- 关键回听片段。

这些结果应优先扩展 `postcall_review_segments` 和 `postcall_analysis_results`；只有出现明确的跨任务检索或统计需求时，再新增派生表或物化视图。
