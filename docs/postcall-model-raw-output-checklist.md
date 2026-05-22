# Postcall 模型真实输出清单

本文档只描述当前两个分析模型和一个说话人分段模型的真实输出，以及这些输出在 API 和 PostgreSQL 中的对应关系。

模型原始输出本身不做规则判断、不做风险融合、不直接输出“冲突/痛苦/高风险”等结论。当前 API 只返回规则线索层生成的 `level`、`levelName` 和 `reviewSegments[]`；完整 `timeline[]` 原始输出保存在数据库内部，用于排查和规则重算。

## 1. 输出总览

| 输出组 | 来源模型 | 模型真实输出 | API 字段 | 数据库片段字段 | 数据库明细类型 | 中文说明 |
| --- | --- | --- | --- | --- | --- | --- |
| 声音事件分数 | BEATs AudioSet fine-tuned | 527 个 AudioSet 多标签概率 | `timeline[].audioEventScores[]` | `postcall_timeline_segments.audio_event_scores` | `audio_event_score` | 每个分数表示一个 AudioSet 声音标签在该时间窗内出现的概率 |
| 说话人分段 | pyannote speaker diarization | `SPEAKER_00` / `SPEAKER_01` 等时间段聚类标签 | `timeline[].speakerLabel` / `timeline[].speakerRole` / `timeline[].roleSource` | `postcall_timeline_segments.speaker_label` / `speaker_role` / `role_source` | `speaker_diarization` | 只表示“谁在这个时间段说话”的聚类标签，不判断报警人或接警员身份 |
| 能量人声切片 | librosa energy split | 若干人声能量时间段 | `timeline[].roleSource = energy_vad` | `postcall_timeline_segments.role_source` | `voice_emotion_score` / `voice_emotion_dimension` | 快模式使用；不输出 `SPEAKER_00` / `SPEAKER_01`，也不区分报警人或接警员 |
| 人声情绪分数 | WavLM categorical emotion | 9 类情绪 logits，经 softmax 后形成概率 | `timeline[].voiceEmotionScores[]` | `postcall_timeline_segments.voice_emotion_scores` | `voice_emotion_score` | 每个分数表示该时间窗人声更接近某一情绪类别的概率 |
| 人声细粒度分数 | WavLM detailed head | 17 类细粒度 logits，经 softmax 后形成概率 | 第一版 API 默认不返回 | `postcall_timeline_segments.voice_detailed_scores` | `internal_payload` | 本地未确认 17 类标签名，第一版只入库保存 index 和 score |
| 人声连续维度 | WavLM dimension heads | `arousal`、`valence`、`dominance` 三个 0 到 1 标量 | 内部 timeline 输入 | `postcall_timeline_segments.voice_emotion_dimensions` | `internal_payload` | 三个连续情绪维度，保存中英文维度名和数值，不是业务结论 |

## 2. 公共时间窗字段

| API 字段 | 数据库字段 | 类型 | 中文说明 |
| --- | --- | --- | --- |
| `timeline[].segmentId` | `postcall_timeline_segments.segment_id` | `string` | 时间窗编号，同一任务内唯一 |
| `timeline[].startSec` | `start_sec` | `number` | 时间窗开始秒数 |
| `timeline[].endSec` | `end_sec` | `number` | 时间窗结束秒数 |
| `timeline[].speakerLabel` | `speaker_label` | `string \| null` | 说话人分段标签，例如 `SPEAKER_00`；BEATs 全局声音事件片段为空 |
| `timeline[].speakerRole` | `speaker_role` | `string \| null` | 业务身份，当前无可靠映射时为 `未知`，全局声音事件片段为空 |
| `timeline[].roleSource` | `role_source` | `string \| null` | `global_audio` 表示全局声音事件，`diarization_only` 表示完整模式说话人分段，`energy_vad` 表示快模式能量人声切片 |

2 分钟音频应拆成多个时间窗。BEATs 和 WavLM 使用各自原生时间窗，不强行融合：BEATs 片段通常没有说话人身份；完整模式下 WavLM 片段来自 pyannote 说话人分段，会带 `SPEAKER_00` / `SPEAKER_01`；快模式下 WavLM 片段来自能量人声切片，`speakerLabel` 和 `speakerRole` 为空。17 类细粒度分数第一版只入库保存，不对外返回。

## 3. 声音事件输出

来源：BEATs。

本地模型：

```text
models/BEATs_iter3_plus_AS2M_finetuned_on_AS2M_cpt2/BEATs_iter3_plus_AS2M_finetuned_on_AS2M_cpt2.pt
```

本地标签表：

```text
models/audioset/class_labels_indices.csv
docs/postcall-beats-audioset-labels.csv
```

真实输出：

```text
[527] sigmoid probability
```

API 单项结构：

```json
{
  "eventNameEn": "Screaming",
  "eventNameZh": "尖叫声",
  "score": 0.72
}
```

字段对照：

| 字段 | API 是否返回 | 数据库字段 | 中文说明 |
| --- | --- | --- | --- |
| `index` | 否 | `postcall_timeline_segments.internal_payload.audioEventScoresFull[].index` | AudioSet 输出向量下标，用于内部排查 |
| `mid` | 否 | `postcall_timeline_segments.internal_payload.audioEventScoresFull[].mid` | AudioSet MID，声音标签的稳定编码，用于内部排查 |
| `eventNameEn` | 是 | `postcall_timeline_segments.audio_event_scores[].eventNameEn` | AudioSet 原始英文标签 |
| `eventNameZh` | 是 | `postcall_timeline_segments.audio_event_scores[].eventNameZh` | 中文显示名；只是标签翻译，不是报警业务结论 |
| `score` | 是 | `postcall_timeline_segments.audio_event_scores[].score` | BEATs `extract_features()` 输出的 sigmoid 概率，范围 `0` 到 `1` |

完整 527 类清单见 [postcall-beats-audioset-labels.csv](/Users/xumaowen/Workspace/Develop/PyCharm/echox-call/docs/postcall-beats-audioset-labels.csv:1)。其中 `label_en` 是 AudioSet 原始名称，`label_zh` 是中文显示名。API 应同时返回 `eventNameEn` 和 `eventNameZh`。

报警音频优先关注的 BEATs 标签：

| index | MID | eventNameEn | eventNameZh | 中文注释 |
| --- | --- | --- | --- | --- |
| 8 | `/m/07p6fty` | `Shout` | 喊叫声 | 可作为争执、求助或强烈情绪线索 |
| 11 | `/m/07sr1lc` | `Yell` | 叫喊声 | 可作为争执、呼救或强烈情绪线索 |
| 14 | `/m/03qc9zr` | `Screaming` | 尖叫声 | 报警音频重点关注声音事件 |
| 15 | `/m/02rtxlg` | `Whispering` | 低声/耳语 | 可能对应压低声音，但模型只判断声学耳语 |
| 22 | `/m/0463cq4` | `Crying, sobbing` | 哭泣/抽泣声 | 报警音频重点关注声音事件 |
| 24 | `/m/07qz6j3` | `Whimper` | 呜咽声 | 可作为痛苦、害怕或弱势状态线索，但不是结论 |
| 25 | `/m/07qw_06` | `Wail, moan` | 哀号/呻吟声 | 可作为痛苦或求助线索，但不是医学判断 |
| 41 | `/m/0lyf6` | `Breathing` | 呼吸声 | 只表示呼吸类声音 |
| 42 | `/m/07mzm6` | `Wheeze` | 喘鸣声 | 可作为呼吸异常线索，不作医疗诊断 |
| 44 | `/m/07s0dtb` | `Gasp` | 急促吸气声 | 可作为惊恐、窒息感或呼吸异常线索 |
| 45 | `/m/07pyy8b` | `Pant` | 喘气声 | 可作为紧张、奔跑或呼吸异常线索 |
| 53 | `/m/07pbtc8` | `Walk, footsteps` | 脚步声 | 可作为人员移动线索 |
| 354 | `/m/02dgv` | `Door` | 门相关声音 | 门相关声音 |
| 358 | `/m/07rjzl8` | `Slam` | 猛烈关门/撞击声 | 报警音频重点关注门体冲击线索 |
| 359 | `/m/07r4wb8` | `Knock` | 敲门/敲击声 | 敲门/敲击声 |
| 388 | `/m/07pp_mv` | `Alarm` | 报警器/警报声 | 报警器/警报类声音 |
| 396 | `/m/03kmc9` | `Siren` | 警报/警笛声 | 警报/警笛声 |
| 400 | `/m/0c3f7m` | `Fire alarm` | 火警报警声 | 火警报警声 |
| 426 | `/m/014zdl` | `Explosion` | 爆炸声 | 报警音频重点关注声音事件 |
| 427 | `/m/032s66` | `Gunshot, gunfire` | 枪声/射击声 | 报警音频重点关注声音事件 |
| 441 | `/m/039jq` | `Glass` | 玻璃相关声音 | 玻璃相关声音 |
| 443 | `/m/07rn7sz` | `Shatter` | 破碎声 | 可作为玻璃破碎或物体破裂线索 |
| 460 | `/m/07qnq_y` | `Thump, thud` | 沉闷撞击声 | 沉闷撞击声 |
| 466 | `/m/07pws3f` | `Bang` | 砰响/撞击声 | 报警音频重点关注冲击线索 |
| 467 | `/m/07ryjzk` | `Slap, smack` | 拍打/掌击声 | 可作为肢体接触线索，但不是打人结论 |
| 469 | `/m/07pjjrj` | `Smash, crash` | 撞碎/碰撞破裂声 | 可作为冲突或破坏线索 |
| 470 | `/m/07pc8lb` | `Breaking` | 破裂/损坏声 | 破裂/损坏声 |

## 4. 人声情绪输出

来源：WavLM categorical emotion。

本地模型：

```text
models/wavlm-large-categorical-emotion/model.safetensors
models/wavlm-large-categorical-emotion/config.json
third_party/WavLM/emotion/wavlm_emotion.py
```

真实输出：

```text
predicted: [9] logits
```

API 使用 `softmax(predicted)` 后的概率。

API 单项结构：

```json
{
  "emotionNameEn": "Fear",
  "emotionNameZh": "恐惧",
  "score": 0.68
}
```

9 类情绪清单：

| index | emotionNameEn | emotionNameZh | 中文注释 | API 字段 | 数据库明细映射 |
| --- | --- | --- | --- | --- | --- |
| 0 | `Anger` | 愤怒 | 愤怒/恼怒倾向；可作为强烈负向情绪线索，不等于冲突结论 | 内部 timeline 输入 | `postcall_timeline_segments.voice_emotion_scores[]` |
| 1 | `Contempt` | 轻蔑 | 轻蔑/不满倾向；通常只作为情绪上下文 | 内部 timeline 输入 | `postcall_timeline_segments.voice_emotion_scores[]` |
| 2 | `Disgust` | 厌恶 | 厌恶倾向；通常只作为情绪上下文 | 内部 timeline 输入 | `postcall_timeline_segments.voice_emotion_scores[]` |
| 3 | `Fear` | 恐惧 | 恐惧/害怕倾向；可作为报警音频重点关注的人声状态线索 | 内部 timeline 输入 | `postcall_timeline_segments.voice_emotion_scores[]` |
| 4 | `Happiness` | 高兴 | 开心/愉悦倾向；通常用于排除强负向情绪 | 内部 timeline 输入 | `postcall_timeline_segments.voice_emotion_scores[]` |
| 5 | `Neutral` | 中性 | 中性情绪倾向；不等于“现场平稳”结论 | 内部 timeline 输入 | `postcall_timeline_segments.voice_emotion_scores[]` |
| 6 | `Sadness` | 悲伤 | 悲伤/低落倾向；可与哭泣声音事件一起作为求助或痛苦线索 | 内部 timeline 输入 | `postcall_timeline_segments.voice_emotion_scores[]` |
| 7 | `Surprise` | 惊讶 | 惊讶倾向；需要结合上下文解释 | 内部 timeline 输入 | `postcall_timeline_segments.voice_emotion_scores[]` |
| 8 | `Other` | 其他 | 其他情绪或无法归入前 8 类；不应解释成具体状态 | 内部 timeline 输入 | `postcall_timeline_segments.voice_emotion_scores[]` |

字段对照：

| 字段 | API 是否返回 | 数据库字段 | 中文说明 |
| --- | --- | --- | --- |
| `index` | 否 | 当前不单独保存；顺序由标签表和数组顺序确定 | 9 类情绪输出向量下标，用于内部排查 |
| `emotionNameEn` | 是 | `postcall_timeline_segments.voice_emotion_scores[].emotionNameEn` | WavLM README 确认的英文情绪标签 |
| `emotionNameZh` | 是 | `postcall_timeline_segments.voice_emotion_scores[].emotionNameZh` | 中文显示名，只做展示翻译，不是业务状态结论 |
| `score` | 是 | `postcall_timeline_segments.voice_emotion_scores[].score` | `softmax(predicted)` 后的概率，范围 `0` 到 `1` |

WavLM 输出清单见 [postcall-wavlm-output-labels.csv](/Users/xumaowen/Workspace/Develop/PyCharm/echox-call/docs/postcall-wavlm-output-labels.csv:1)。

## 5. 人声细粒度输出

来源：WavLM detailed head。

真实输出：

```text
detailed_predicted: [17] logits
```

数据库内部使用 `softmax(detailed_predicted)` 后的概率。

当前限制：本地模型只确认有 17 类输出，未确认 17 类标签名。第一版不能编造标签，也不建议对外 API 返回。

数据库内部单项结构：

```json
{
  "index": 0,
  "score": 0.13
}
```

17 类 index 清单：

| index | label | 中文注释 | API 字段 | 数据库明细映射 |
| --- | --- | --- | --- | --- |
| 0 | 未确认 | 细粒度类别 0，标签名未确认，只保存 index 和 score | 第一版默认不返回 | `event_type=voice_detailed_score`, `event_code=detailed_index_0` |
| 1 | 未确认 | 细粒度类别 1，标签名未确认，只保存 index 和 score | 第一版默认不返回 | `event_type=voice_detailed_score`, `event_code=detailed_index_1` |
| 2 | 未确认 | 细粒度类别 2，标签名未确认，只保存 index 和 score | 第一版默认不返回 | `event_type=voice_detailed_score`, `event_code=detailed_index_2` |
| 3 | 未确认 | 细粒度类别 3，标签名未确认，只保存 index 和 score | 第一版默认不返回 | `event_type=voice_detailed_score`, `event_code=detailed_index_3` |
| 4 | 未确认 | 细粒度类别 4，标签名未确认，只保存 index 和 score | 第一版默认不返回 | `event_type=voice_detailed_score`, `event_code=detailed_index_4` |
| 5 | 未确认 | 细粒度类别 5，标签名未确认，只保存 index 和 score | 第一版默认不返回 | `event_type=voice_detailed_score`, `event_code=detailed_index_5` |
| 6 | 未确认 | 细粒度类别 6，标签名未确认，只保存 index 和 score | 第一版默认不返回 | `event_type=voice_detailed_score`, `event_code=detailed_index_6` |
| 7 | 未确认 | 细粒度类别 7，标签名未确认，只保存 index 和 score | 第一版默认不返回 | `event_type=voice_detailed_score`, `event_code=detailed_index_7` |
| 8 | 未确认 | 细粒度类别 8，标签名未确认，只保存 index 和 score | 第一版默认不返回 | `event_type=voice_detailed_score`, `event_code=detailed_index_8` |
| 9 | 未确认 | 细粒度类别 9，标签名未确认，只保存 index 和 score | 第一版默认不返回 | `event_type=voice_detailed_score`, `event_code=detailed_index_9` |
| 10 | 未确认 | 细粒度类别 10，标签名未确认，只保存 index 和 score | 第一版默认不返回 | `event_type=voice_detailed_score`, `event_code=detailed_index_10` |
| 11 | 未确认 | 细粒度类别 11，标签名未确认，只保存 index 和 score | 第一版默认不返回 | `event_type=voice_detailed_score`, `event_code=detailed_index_11` |
| 12 | 未确认 | 细粒度类别 12，标签名未确认，只保存 index 和 score | 第一版默认不返回 | `event_type=voice_detailed_score`, `event_code=detailed_index_12` |
| 13 | 未确认 | 细粒度类别 13，标签名未确认，只保存 index 和 score | 第一版默认不返回 | `event_type=voice_detailed_score`, `event_code=detailed_index_13` |
| 14 | 未确认 | 细粒度类别 14，标签名未确认，只保存 index 和 score | 第一版默认不返回 | `event_type=voice_detailed_score`, `event_code=detailed_index_14` |
| 15 | 未确认 | 细粒度类别 15，标签名未确认，只保存 index 和 score | 第一版默认不返回 | `postcall_timeline_segments.voice_detailed_scores[]` |
| 16 | 未确认 | 细粒度类别 16，标签名未确认，只保存 index 和 score | 第一版默认不返回 | `postcall_timeline_segments.voice_detailed_scores[]` |

字段对照：

| API 字段 | 数据库字段 | 中文说明 |
| --- | --- | --- |
| `index` | `postcall_timeline_segments.voice_detailed_scores[].index` | 17 类细粒度输出向量下标 |
| `score` | `postcall_timeline_segments.voice_detailed_scores[].score` | `softmax(detailed_predicted)` 后的概率，范围 `0` 到 `1` |

## 6. 人声连续维度输出

来源：WavLM dimension heads。

真实输出：

```text
arousal: [1] sigmoid value
valence: [1] sigmoid value
dominance: [1] sigmoid value
```

API 结构：

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

字段清单：

| API 字段 | 数据库 JSON 字段 | 数据库明细映射 | 中文注释 |
| --- | --- | --- | --- |
| `timeline[].voiceEmotionDimensions.arousal.value` | `voice_emotion_dimensions.arousal.value` | `postcall_timeline_segments.voice_emotion_dimensions.arousal` | 情绪唤醒度/激活水平；数值高表示声音更激烈或更兴奋，不直接等于“激动”结论 |
| `timeline[].voiceEmotionDimensions.valence.value` | `voice_emotion_dimensions.valence.value` | `postcall_timeline_segments.voice_emotion_dimensions.valence` | 情绪效价；数值低更偏负向，数值高更偏正向，不直接等于“痛苦”结论 |
| `timeline[].voiceEmotionDimensions.dominance.value` | `voice_emotion_dimensions.dominance.value` | `postcall_timeline_segments.voice_emotion_dimensions.dominance` | 控制感/支配感；数值低可能表示弱势或失控倾向，但第一版只保存数值 |

## 7. API 片段示例

```json
[
  {
    "segmentId": "seg_0001",
    "startSec": 35.0,
    "endSec": 45.0,
    "speakerLabel": null,
    "speakerRole": null,
    "roleSource": "global_audio",
    "audioEventScores": [
      {
        "eventNameEn": "Screaming",
        "eventNameZh": "尖叫声",
        "score": 0.72
      }
    ],
    "voiceEmotionScores": [],
    "voiceEmotionDimensions": {}
  },
  {
    "segmentId": "seg_0002",
    "startSec": 36.2,
    "endSec": 42.8,
    "speakerLabel": "SPEAKER_00",
    "speakerRole": "未知",
    "roleSource": "diarization_only",
    "audioEventScores": [],
    "voiceEmotionScores": [
      {
        "emotionNameEn": "Fear",
        "emotionNameZh": "恐惧",
        "score": 0.68
      }
    ],
    "voiceEmotionDimensions": {
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
  }
]
```

## 8. 不允许作为模型原始输出的字段

| 字段或表达 | 处理方式 |
| --- | --- |
| `riskLevel` | 不返回；第一版不输出风险等级 |
| `level` / `levelName` | 只允许作为规则线索层字段返回，不能写入 `timeline[]` 或模型原始明细 |
| `confidenceLevel` | 不返回；模型原始明细事件中必须为 `NULL` |
| `severity` | 不返回；原始明细事件中必须为 `NULL` |
| `keySegments` | 不返回重点片段 |
| `疑似冲突` | 不作为模型原始输出返回；后续如需要应由规则或业务模型产生 |
| `疑似痛苦` | 不作为模型原始输出返回；后续如需要应由规则或业务模型产生 |
| `疑似危险升级` | 不返回；这是风险融合判断 |
| `平稳` | 不返回；`Neutral` 只是模型情绪类别，不等于业务平稳结论 |
