# Postcall 原始模型输出验证

本文档记录本地模型、API 合同和 PostgreSQL 表结构的核对结论。

模型真实输出字段清单见 [postcall-model-raw-output-checklist.md](/Users/xumaowen/Workspace/Develop/PyCharm/echox-call/docs/postcall-model-raw-output-checklist.md:1)。

## 1. 当前结论

第一版保留模型原始输出，并在 worker 成功路径执行确定性规则线索层。

当前合理结构是：

- `postcall_timeline_segments` 保存内部模型时间线片段，并用结构化列保存同一份时间、说话人和模型分数字段；
- `postcall_analysis_results.api_result_payload` 保存对外 GET 接口返回的 `data` 部分快照，包括 `level`、`levelName`；仅 `level=1/2` 时包含 `reviewSegments`；
- `postcall_review_segments` 逐条保存对外 `reviewSegments[]` 复核片段；
- `postcall_model_runs` 保存模型运行记录。

当前仍不输出：

- 风险等级；
- 疑似冲突；
- 疑似痛苦；
- 疑似危险升级；
- 关键证据片段推荐。

## 2. BEATs 验证

本地 checkpoint：

```text
models/BEATs_iter3_plus_AS2M_finetuned_on_AS2M_cpt2/BEATs_iter3_plus_AS2M_cpt2.pt
```

验证结果：

- checkpoint 包含 `cfg`、`model`、`label_dict`；
- `cfg.finetuned_model = true`；
- `cfg.predictor_class = 527`；
- `label_dict` 长度为 `527`；
- predictor 权重形状为 `[527, 768]`。

结论：BEATs 是 AudioSet 527 类多标签声音事件模型。

数据库内部明细应保留原始输出语义：

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

- `index` 来自 AudioSet 输出向量下标，只保存在内部明细；
- `mid` 来自 AudioSet，只保存在内部明细；
- `eventNameEn` 来自 AudioSet；
- `eventNameZh` 来自本地中文显示名映射；
- `score` 来自模型；
- 对外 API 只返回 `eventNameEn`、`eventNameZh`、`score`；
- 不在模型原始输出层翻译成“疑似尖叫”；
- 不在第一版判断“冲突”“危险”“高风险”。

## 3. WavLM 验证

本地模型：

```text
models/wavlm-large-categorical-emotion/model.safetensors
models/wavlm-large-categorical-emotion/config.json
models/wavlm-large/config.json
third_party/WavLM/emotion/wavlm_emotion.py
```

`config.json` 关键字段：

```json
{
  "output_class_num": 9,
  "detailed_class_num": 17,
  "pretrain_model": "wavlm_large"
}
```

本地 README 已确认 9 类情绪：

```text
Anger, Contempt, Disgust, Fear, Happiness, Neutral, Sadness, Surprise, Other
```

权重和 wrapper 代码验证结果：

- `emotion_layer.2.weight` 形状为 `[9, 256]`；
- `detailed_out_layer.2.weight` 形状为 `[17, 256]`；
- `arousal_layer.2.weight` 形状为 `[1, 256]`；
- `valence_layer.2.weight` 形状为 `[1, 256]`；
- `dominance_layer.2.weight` 形状为 `[1, 256]`；
- `WavLMWrapper.forward()` 返回 `predicted, detailed_predicted, arousal, valence, dominance`。

API 应保留 9 类情绪和 3 个连续维度的原始输出语义。17 类细粒度分数只入库保存，默认不对外返回。

```json
{
  "voiceEmotionScores": [
    {"emotionNameEn": "Fear", "emotionNameZh": "恐惧", "score": 0.68}
  ],
  "voiceEmotionDimensions": {
    "arousal": {"dimensionNameEn": "Arousal", "dimensionNameZh": "唤醒度", "value": 0.74},
    "valence": {"dimensionNameEn": "Valence", "dimensionNameZh": "情绪效价", "value": 0.22},
    "dominance": {"dimensionNameEn": "Dominance", "dimensionNameZh": "控制感", "value": 0.31}
  }
}
```

说明：

- 9 类情绪可以返回英文情绪名、中文显示名和 score；
- 17 类细粒度输出本地没有可靠标签名，只入库保存 index 和 score，默认不对外返回；
- arousal、valence、dominance 是模型直接输出，可以返回英文维度名、中文显示名和原始数值；
- 中文显示名只做展示，不在第一版翻译成“痛苦”“惊恐”“激动”“平稳”等业务结论。

## 4. API 结构判断

当前第一版实际返回：

```json
{
  "jobId": "job_xxx",
  "jjdh": "JJD_20260408_0001",
  "state": "completed",
  "level": 3,
  "levelName": "暂无明显线索"
}
```

这个结构是合理的，因为 API 只表达关注等级和复核片段，不返回内部任务元信息、完整模型时间线和风险等级。

## 5. 数据库验证

已验证 PostgreSQL 当前结构：

- migration `001`、`002`、`003`、`004` 已应用；
- migration `005` 用于把结果结构收紧为原始模型输出合同；
- migration `006` 曾用于增加 raw 模式约束，并把模型分数字段调整为 `double precision`；
- migration `007` 用于统一声音事件、人声情绪和时间线明细字段注释，明确 `event_name` 是中文显示名，`event_name_en` 是原始英文标签；
- migration `008` 用于把 WavLM 情绪和连续维度的 API/数据库注释统一为中英文显示名结构；
- migration `009` 用于明确 WavLM 17 类细粒度分数只入库保存，第一版默认不对外返回；
- migration `010` 用于明确第一版 API 不返回 `analysis`、`audio`、`keySegments`，且对外隐藏 BEATs `index` / `mid` 和 WavLM emotion index；
- migration `011` 用于补充 `speakerLabel`、`speakerRole`、`roleSource` 说话人分段字段，并允许 `speaker_diarization` 运行记录；
- migration `012` 用于允许快模式 `roleSource = energy_vad`，表示仅按能量人声切片、不区分说话人；
- migration `015` 用于补充规则线索层结果和证据片段字段的中文注释；
- migration `016` 用于保存对外 API `data` 快照；
- migration `017` 用于删除旧 `result_payload`，统一使用 `api_result_payload`；
- migration `018` 曾用于把时间字段改为 `double precision`，并约束时间线结构；
- migration `026` 用于收敛当前目标结构，删除重复表和重复字段，保留 6 张业务表；
- 所有 `postcall_%` 业务表和字段都有中文注释；
- `postcall_timeline_segments` 保存完整内部模型时间线，规则重算从结构化列重建输入；
- 当前 worker 成功结果写入 `attention_level`、`attention_level_name`、`rule_version`、`matched_rule_codes` 和 `api_result_payload`；
- 插入任务、最终结果、时间线片段和复核片段的验证数据成功后回滚。

## 6. 后续优化边界

后续要做规则或融合时，必须新加一层，不能把它混进原始输出层。

当前规则线索层已经基于原始输出生成 `level`、`levelName` 和 `reviewSegments`，但仍不改写模型原始输出层。后续更高阶风险层如需继续扩展，可以生成：

- 疑似冲突；
- 疑似痛苦；
- 疑似危险升级；
- 需要人工重点关注；
- 风险等级；
- 关键回听片段。

这些结果应明确写成 `derived_risk` 或证据片段，不应伪装成模型原始输出。
