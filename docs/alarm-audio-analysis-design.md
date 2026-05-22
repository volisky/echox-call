# 报警音频分析模型能力与开发方案

> 当前开发口径：第一版落地“模型原始输出 + 确定性规则线索层”，对外输出 `level`、`levelName` 和 `reviewSegments`，但不输出风险等级、不做大模型融合、不做处置建议。当前模型真实输出字段以 `docs/postcall-model-raw-output-checklist.md` 为准。

## 1. 目标定位

本方案面向 110/接警类报警音频的辅助分析，不把模型输出直接当作警情事实或证据结论。系统应输出可复核的风险线索：什么时间段出现了什么声音、人声情绪是否异常、为什么建议民警/接警员关注，并保留原始音频、时间戳、模型版本、阈值和置信度。

成熟系统的共同思路不是“整段音频给一个最终结论”，而是：

- 先把录音按时间切成可解释片段；
- 分别做声音事件、人声情绪/状态、语音内容和能量变化分析；
- 最后做规则或模型融合，生成风险等级和证据片段；
- 所有结论都能回放到原始音频时间点，由人工复核。

参考依据：

- AudioSet 是 10 秒级人标注声音事件数据集，包含 527 个常用音频事件标签，适合做通用声音事件基线。
- BEATs 是通用音频预训练/AudioSet fine-tuned 模型，适合声音事件识别。
- WavLM 是语音表征模型，适合人声相关任务。
- DCASE 这类成熟音频任务通常区分 audio tagging、sound event detection、source separation，不把所有问题塞进一个模型。
- APCO/NENA 的应急通信 QA/QI 思路强调录音回放、质检、事件过程复核和人员处置评估，系统输出应服务于接警/处置，而不是替代接警员或民警判断。

## 2. 当前两个模型的职责边界

### 2.1 BEATs_iter3+ AS2M fine-tuned

本地模型：

```text
models/BEATs_iter3_plus_AS2M_finetuned_on_AS2M_cpt2/BEATs_iter3_plus_AS2M_finetuned_on_AS2M_cpt2.pt
```

已验证结果：

- checkpoint 可正常加载；
- `cfg / model / label_dict` 齐全；
- `predictor_class = 527`；
- `label_dict = 527`；
- `load_state_dict` 严格匹配，`missing_count = 0`，`unexpected_count = 0`；
- 零音频前向输出维度为 `(1, 527)`。

它适合做的功能：

- 通用声音事件识别；
- 多标签 audio tagging；
- 报警音频中的异常声音线索发现；
- 输出每个时间窗内的事件概率。

对报警业务有直接价值的 AudioSet 相关标签包括：

- `Screaming`：尖叫；
- `Crying, sobbing` / `Baby cry, infant cry`：哭泣、婴儿哭声；
- `Shout` / `Yell` / `Children shouting`：喊叫；
- `Breathing` / `Gasp`：呼吸、喘息；
- `Door` / `Slam` / `Knock` / `Bang`：门体、撞击、敲击、砰响；
- `Speech` / `Hubbub, speech noise, speech babble`：人声、人群或多人说话噪声。

它不适合直接做的功能：

- 不能直接判断“发生冲突”这个警务语义结论；
- 不能单独判断“家暴”“斗殴”“胁迫”等案事件类型；
- 不能稳定判断“呼吸异常”的医学或急救含义；
- 不能识别语义内容，例如“救命”“别打了”“有人闯入”。

工程定位：

```text
BEATs = audio event tagging / sound event classification model
```

它输出的是声音事件证据，不是最终警情判断。

### 2.2 WavLM-Large Categorical Emotion

本地模型：

```text
models/wavlm-large-categorical-emotion/model.safetensors
```

已验证结果：

- 依赖安装后可加载；
- 需要使用 WavLM-Large 的真实架构配置加载，不能直接依赖本地 `config.json`；
- 真实权重结构是 24 层、1024 hidden size；
- 前向输出包括：
  - 9 类情绪 logits：`(1, 9)`；
  - 17 类细粒度情绪 logits：`(1, 17)`；
  - arousal：`(1, 1)`；
  - valence：`(1, 1)`；
  - dominance：`(1, 1)`。

README 中明确的 9 类情绪标签：

- `Anger`
- `Contempt`
- `Disgust`
- `Fear`
- `Happiness`
- `Neutral`
- `Sadness`
- `Surprise`
- `Other`

它适合做的功能：

- 人声情绪识别；
- 判断语音片段是否偏向恐惧、愤怒、中性、悲伤、惊讶等；
- 输出 arousal/valence/dominance 作为激动程度、情绪正负性和控制感等辅助特征；
- 给 BEATs 的声音事件结果补充“人声状态线索”。

它不适合直接做的功能：

- 不能直接识别“压低声音”；
- 不能直接识别“痛苦”这个业务标签，除非映射到 `Sadness/Fear/Other` 或重新训练；
- 不能单独判断“惊恐、痛苦、压低声音、激动、平稳”这组业务状态；
- 不能分析非人声事件，例如门体冲击、撞击、环境噪声。

工程定位：

```text
WavLM emotion = speech emotion classification / paralinguistic feature model
```

它输出的是人声情绪特征，不是最终风险等级。

### 2.3 检查内容中英文对照

这一节只列当前两个模型可以直接输出或较可靠映射的检查项。`原生标签` 表示模型或 AudioSet 标签体系中存在的英文标签；`业务检查项` 是面向报警音频系统的中文展示名称。业务检查项必须保留“疑似/线索”表达，不应写成确认结论。

#### 2.3.1 BEATs 声音事件检查项

BEATs 输出 527 类 AudioSet 多标签概率，适合检查声音事件。下表只列报警音频中优先关注的标签。

| 业务检查项 | English check item | 原生标签 / Native labels | 建议输出 | 说明 |
| --- | --- | --- | --- | --- |
| 人声/说话 | Speech / speaking voice | `Speech`, `Male speech, man speaking`, `Female speech, woman speaking`, `Child speech, kid speaking` | `speech_detected` / `检测到人声` | 用于判断片段是否有人声，不代表风险。 |
| 多人嘈杂/语音混杂 | Hubbub / speech babble | `Hubbub, speech noise, speech babble`, `Crowd` | `speech_babble` / `疑似多人嘈杂` | 可辅助判断争执或现场混乱，但不能单独判定冲突。 |
| 喊叫 | Shout / yell | `Shout`, `Yell`, `Children shouting`, `Battle cry` | `shout_or_yell` / `疑似喊叫` | 与 WavLM 的高唤醒、愤怒或恐惧结果结合后更有价值。 |
| 尖叫 | Screaming | `Screaming` | `screaming` / `疑似尖叫` | 报警音频中的高优先级风险线索，建议保留原始回放片段。 |
| 哭泣/抽泣 | Crying / sobbing | `Crying, sobbing` | `crying_sobbing` / `疑似哭泣或抽泣` | 成人哭泣、抽泣线索；需要结合人声情绪和语义复核。 |
| 婴儿哭声 | Baby cry / infant cry | `Baby cry, infant cry` | `baby_cry` / `疑似婴儿哭声` | 儿童/婴幼儿相关警情可重点提示，但不要单独推断伤害。 |
| 呼吸声 | Breathing | `Breathing` | `breathing` / `检测到明显呼吸声` | 容易受近讲麦克风、跑动、设备摩擦影响，通常只做弱提示。 |
| 喘息/倒吸气 | Gasp | `Gasp` | `gasp` / `疑似喘息或倒吸气` | 比普通 breathing 更值得关注，可作为呼吸困难或惊吓线索。 |
| 门体声音 | Door sound | `Door`, `Doorbell`, `Sliding door` | `door_sound` / `疑似门体相关声音` | 单独出现不应升高风险，主要作为现场动作线索。 |
| 摔门/猛关门 | Slam | `Slam` | `slam` / `疑似摔门或猛关门` | 与喊叫、尖叫、撞击连续出现时可提升关注等级。 |
| 敲击 | Knock | `Knock` | `knock` / `疑似敲击声` | 可用于判断敲门、敲击物体等，需结合上下文。 |
| 砰响/撞击 | Bang / impact-like sound | `Bang`, `Thump, thud`, `Smash, crash` | `impact_sound` / `疑似撞击或砰响` | 报警场景中重点关注，但要防止烟花、装修、车门等误报。 |
| 玻璃相关声音 | Glass-related sound | `Glass` | `glass_sound` / `疑似玻璃相关声音` | AudioSet 标签较粗，不能直接写成“玻璃破碎”。 |
| 儿童环境声 | Child-related sound | `Children playing`, `Baby laughter`, `Child singing` | `child_context` / `儿童相关背景声` | 主要用于上下文和误报分析，不是风险标签。 |
| 静音/无明显声音 | Silence | `Silence` | `silence` / `静音或低声段` | 可用于发现异常静默、通话中断或片段无效。 |

BEATs 不直接支持但可以通过融合推断的业务项：

| 业务检查项 | English derived item | 推荐融合条件 | 输出建议 |
| --- | --- | --- | --- |
| 疑似冲突 | Possible conflict | `Shout/Yell` + `Bang/Slam/Knock` + WavLM `Anger/Fear` 或高 arousal | `possible_conflict` / `疑似冲突线索` |
| 疑似人身安全风险 | Possible personal safety risk | `Screaming` 或 `Crying` + WavLM `Fear/Sadness` + 多个事件在短时间聚集 | `possible_personal_safety_risk` / `疑似人身安全风险线索` |
| 疑似呼吸异常 | Possible breathing distress | `Gasp/Breathing` 持续或峰值明显 + 低语/静音/恐惧情绪 | `possible_breathing_distress` / `疑似呼吸异常线索` |
| 疑似破门/打砸 | Possible forced entry / smashing | `Door/Slam/Bang/Smash` 连续出现 + 喊叫或恐惧人声 | `possible_forced_entry_or_smashing` / `疑似破门或打砸线索` |

#### 2.3.2 WavLM 人声情绪检查项

WavLM emotion 模型适合检查人声片段的情绪和副语言特征。它不检查门声、撞击、环境声，也不直接输出“家暴”“冲突”“受伤”等警务结论。

| 业务检查项 | English check item | 原生标签 / Native output | 建议输出 | 说明 |
| --- | --- | --- | --- | --- |
| 愤怒/激动 | Anger / agitation | `Anger` | `anger` / `疑似愤怒或激动` | 可作为争执或强烈情绪线索，不能单独判定冲突。 |
| 轻蔑/不满 | Contempt | `Contempt` | `contempt` / `疑似轻蔑或不满` | 警务价值低，通常只作为情绪上下文。 |
| 厌恶 | Disgust | `Disgust` | `disgust` / `疑似厌恶情绪` | 警务价值低，通常不单独触发风险。 |
| 恐惧/惊恐 | Fear / fearfulness | `Fear` | `fear` / `疑似恐惧或惊恐` | 重点关注项，可与尖叫、哭泣、撞击融合。 |
| 高兴/积极情绪 | Happiness | `Happiness` | `happiness` / `疑似积极情绪` | 多用于降低风险或识别非警情背景。 |
| 平稳/中性 | Neutral | `Neutral` | `neutral` / `疑似平稳或中性` | 可作为低风险证据，但不能排除风险。 |
| 悲伤/低落 | Sadness | `Sadness` | `sadness` / `疑似悲伤或低落` | 可作为哭泣、求助、痛苦线索的辅助证据。 |
| 惊讶/突发反应 | Surprise | `Surprise` | `surprise` / `疑似惊讶或突发反应` | 与突发撞击、尖叫结合时更有意义。 |
| 其他情绪 | Other emotion | `Other` | `other_emotion` / `其他情绪` | 不应过度解释。 |
| 高唤醒/强烈情绪 | High arousal | `arousal` scalar | `high_arousal` / `疑似高唤醒或强烈情绪` | 不是分类标签，是连续维度；可辅助判断激动程度。 |
| 负向情绪 | Negative valence | `valence` scalar | `negative_valence` / `疑似负向情绪` | 需要结合阈值解释；不能直接等同痛苦。 |
| 控制感/支配感降低 | Low dominance | `dominance` scalar | `low_dominance` / `疑似弱势或失控状态` | 只是辅助特征，需谨慎展示。 |

WavLM 不直接支持但可以映射或后续微调的业务项：

| 业务检查项 | English business state | 当前支持程度 | 推荐处理 |
| --- | --- | --- | --- |
| 惊恐 | Panic / frightened state | 可由 `Fear + high_arousal` 近似映射 | 第一版输出“疑似恐惧/高唤醒”，不要直接写“确认惊恐”。 |
| 痛苦 | Distress / pain-like state | 当前没有原生 `distress` 标签 | 可用 `Sadness/Fear + Crying/Gasp` 作为弱线索，后续需标注微调。 |
| 压低声音 | Suppressed voice / hushed voice | 当前不支持 | 需要专门训练 speech-state head，不能用现有 WavLM emotion 直接判断。 |
| 激动 | Agitated state | 可由 `Anger + high_arousal` 近似映射 | 与 `Shout/Yell` 融合后输出“疑似激动”。 |
| 平稳 | Calm / stable state | 可由 `Neutral + low_arousal + 无高风险事件` 近似映射 | 只能作为辅助低风险证据。 |

## 3. 2 分钟报警音频如何处理

2 分钟音频不建议整段一次性送入模型。原因：

- BEATs/AudioSet 体系天然偏 10 秒级声音事件识别；
- WavLM emotion 模型 README 建议输入 3 到 15 秒的 16kHz 单声道音频；
- 报警音频中的关键风险往往只出现在几秒钟内，整段平均会稀释尖叫、撞击、喘息等短事件；
- 民警/接警员需要的是“第几秒发生了什么”，而不是整段一个标签。

推荐处理方式：

### 3.1 原始音频保全

导入音频后先做证据保全：

- 保存原始文件；
- 计算 `sha256`；
- 记录来源、上传人、接警编号、通话开始时间；
- 后续分析只产生派生结果，不覆盖原文件；
- 所有片段时间戳都映射回原始音频时间轴。

### 3.2 标准化音频

分析用音频统一转换为：

```text
sample_rate = 16000
channel = mono
format = float32 waveform
```

同时保留：

- 原始采样率；
- 原始时长；
- 转换参数；
- 是否降噪、是否音量归一化。

不建议默认做强降噪。报警音频里的撞击、喘息、远处喊叫可能会被降噪算法当噪声消掉。可以提供“原始分析”和“增强分析”两路结果，但最终证据回放应指向原始音频。

### 3.3 BEATs 声音事件窗口

对 2 分钟音频使用滑动窗口：

```text
window_size = 10s
hop_size = 5s
```

2 分钟音频大约产生：

```text
(120 - 10) / 5 + 1 = 23 个窗口
```

每个窗口输出 527 类概率，只保留报警相关标签和 top-k 标签。

窗口结果需要合并：

- 连续窗口都出现 `Screaming`，合并成一个尖叫事件段；
- 相邻 `Bang/Slam/Knock` 可以合并成撞击事件簇；
- 短促高置信事件保留峰值窗口；
- 输出事件起止时间、峰值时间、最大置信度、平均置信度、出现次数。

建议报警相关事件阈值先按保守策略启动：

```text
Screaming >= 0.35
Crying/Sobbing >= 0.30
Baby cry >= 0.30
Shout/Yell >= 0.30
Gasp >= 0.25
Breathing >= 0.35
Bang/Slam/Knock >= 0.30
Door >= 0.30
```

这些阈值不能直接定为生产标准，必须用本地报警音频样本校准。启动阶段可以同时保留低阈值候选和高阈值强证据，避免漏掉低质量录音里的关键风险。

### 3.4 WavLM 人声情绪窗口

WavLM 不应跑整段 2 分钟音频。应先做 VAD：

```text
2 分钟音频 -> VAD 人声段 -> 合并短语音 -> 切成 3s 到 15s 片段
```

建议规则：

- 人声段短于 1 秒：先不单独判断情绪，只作为上下文；
- 相邻人声间隔小于 0.5 秒：合并；
- 合并后短于 3 秒：可向前后扩展上下文到 3 秒；
- 长于 15 秒：按 10 秒窗口、5 秒步长切分；
- 对接警员和报警人混合说话的场景，当前使用 pyannote 先做说话人分段，只得到 `SPEAKER_00` / `SPEAKER_01`；在没有 ASR 时间戳、声道、固定开场白或声纹库前，不判断谁是报警人或接警员。

WavLM 输出建议映射为业务特征：

```text
Fear -> 恐惧/惊恐线索
Anger -> 激动/争执线索
Sadness -> 哭泣、痛苦、低落线索
Neutral -> 平稳线索
Surprise -> 突发惊讶线索
Arousal 高 -> 高唤醒/激动线索
Valence 低 -> 负向情绪线索
Dominance 低 -> 失控/弱势线索
```

注意：这只是业务映射，不是模型原生标签。对外展示时应写“疑似恐惧/高唤醒”，不要写成“确认惊恐”。

### 3.5 融合策略

从警务处置角度，最终关注的不是单个标签，而是风险组合：

```text
风险 = 声音事件 + 人声情绪 + 能量变化 + 时间连续性 + 可回放证据
```

建议第一版用规则融合，不要一上来训练黑盒风险模型。

高优先级规则示例：

- `Screaming` 高置信 + `Fear` 高置信：疑似紧急人身安全风险；
- `Crying/Sobbing` 连续出现 + `Fear/Sadness`：疑似求助、伤害或儿童/弱势人员风险；
- `Bang/Slam/Knock` 多次出现 + `Shout/Yell`：疑似冲突、破门、打砸或强烈争执；
- `Gasp/Breathing` 异常突出 + 人声少或语速异常：疑似呼吸困难、受伤或医疗风险；
- `Door/Slam/Bang` 后接静默或低声：疑似环境突变，需要人工重点复核；
- 多个风险事件在 30 秒内聚集：提升风险等级。

低优先级或需要复核的情况：

- 单独 `Door` 不应触发高风险；
- 单独 `Speech` 没有风险意义；
- 单独 `Anger` 只能说明情绪，不代表违法犯罪；
- 单独 `Breathing` 可能是近讲麦克风、跑步、噪声或设备摩擦。

## 4. 当前阶段对外能够提供的结果

### 4.1 面向业务系统的结果边界

第一版对外结果不返回 `analysis`、`audio`、`riskEvaluated`、`analysisMode` 等内部元信息，只返回任务标识、状态和时间线模型输出。

```json
{
  "jobId": "job_20260508_0001",
  "jjdh": "JJD_20260408_0001",
  "state": "completed",
  "timeline": []
}
```

### 4.2 时间线结果

时间线是最重要的结果，民警和接警员可以直接跳转回放：

```json
{
  "timeline": [
    {
      "segmentId": "seg_0001",
      "startSec": 34.8,
      "endSec": 48.5,
      "audioEventScores": [
        {
          "eventNameEn": "Screaming",
          "eventNameZh": "尖叫声",
          "score": 0.82
        },
        {
          "eventNameEn": "Bang",
          "eventNameZh": "砰响/撞击声",
          "score": 0.69
        }
      ],
      "voiceEmotionScores": [
        {
          "emotionNameEn": "Fear",
          "emotionNameZh": "恐惧",
          "score": 0.74
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
}
```

对外时间线不直接暴露底层模型名称，也不在 `timeline[]` 内输出 `confidenceLevel`、`riskLevel`、`level`、`levelName`。

### 4.3 证据片段

第一版不返回 `keySegments` 字段；`level=1/2` 的规则线索会写入 `postcall_review_segments` 供内部复核。API 当前只返回 `startSec`、`endSec`、`result`。

风险等级和处置建议属于后续风险融合层。

### 4.4 模型与可追溯信息

内部必须保存；对外 API 默认不返回，除非后续提供受控 debug 接口：

```json
{
  "modelVersions": {
    "soundEvent": "BEATs_iter3_plus_AS2M_finetuned_on_AS2M_cpt2",
    "voiceEmotion": "wavlm-large-categorical-emotion"
  },
  "audioProcessing": {
    "analysisSampleRate": 16000,
    "channels": "mono",
    "beatsWindowSec": 10,
    "beatsHopSec": 5,
    "wavlmMinSec": 3,
    "wavlmMaxSec": 15
  },
  "sourceTrace": {
    "originalSha256": "..."
  }
}
```

## 5. 推荐开发架构

### 5.1 模块划分

建议拆成以下模块：

```text
audio_io/
  load_audio.py              # 读取、转码、采样率转换
  preserve.py                # 原始文件 hash、元数据

segmentation/
  vad.py                     # 人声检测
  windows.py                 # 10s/5s BEATs 窗口、3-15s WavLM 窗口

models/
  beats_event_model.py       # BEATs 加载和推理
  wavlm_emotion_model.py     # WavLM wrapper 加载和推理

fusion/
  event_mapping.py           # AudioSet MID 到业务事件映射
  risk_rules.py              # 风险规则
  timeline_merge.py          # 连续窗口合并

api/
  schemas.py                 # 请求/响应结构
  routes.py                  # 对外接口

storage/
  analysis_repo.py           # 分析结果持久化
```

### 5.2 模型加载注意事项

BEATs：

- 需要引入官方 `BEATs.py`、`backbone.py`、`modules.py`；
- 使用 checkpoint 内的 `cfg` 初始化；
- 使用 checkpoint 内的 `label_dict` 做 MID 映射；
- 输出是 527 类多标签概率；
- 需要额外维护 MID 到中文业务事件的映射表。

WavLM：

- 需要引入 Vox-Profile 的 `WavLMWrapper`，当前路径是 `third_party/WavLM/emotion/wavlm_emotion.py`；
- `models/wavlm-large-categorical-emotion/config.json` 是 emotion 模型的构造参数；
- `models/wavlm-large/config.json` 才是 WavLM-Large 主干架构配置；
- 加载时应使用 WavLM-Large 架构参数，再加载 `models/wavlm-large-categorical-emotion/model.safetensors`；
- 本地 README 只提供 9 类情绪标签，17 类细粒度标签需要从上游项目或模型作者处确认后再对外展示；
- 未确认的 17 类标签不要编造展示。

### 5.3 下载来源

BEATs 官方代码来自 Microsoft UniLM 仓库：

```text
https://github.com/microsoft/unilm/tree/master/beats
```

需要下载这三个文件：

```bash
mkdir -p third_party/beats

curl -L https://raw.githubusercontent.com/microsoft/unilm/master/beats/BEATs.py \
  -o third_party/beats/BEATs.py

curl -L https://raw.githubusercontent.com/microsoft/unilm/master/beats/backbone.py \
  -o third_party/beats/backbone.py

curl -L https://raw.githubusercontent.com/microsoft/unilm/master/beats/modules.py \
  -o third_party/beats/modules.py
```

BEATs checkpoint 来源也是 Microsoft UniLM 的 BEATs README：

```text
https://github.com/microsoft/unilm/blob/master/beats/README.md
```

当前本地使用的是：

```text
Fine-tuned BEATs_iter3+ (AS2M) (cpt2)
```

WavLM emotion wrapper 来自 Vox-Profile 官方仓库：

```text
https://github.com/tiantiaf0627/vox-profile-release
```

当前项目使用的核心文件是：

```text
third_party/WavLM/emotion/wavlm_emotion.py
```

如果后续要重新下载，可以从上游仓库复制：

```bash
mkdir -p third_party/WavLM/emotion

curl -L https://raw.githubusercontent.com/tiantiaf0627/vox-profile-release/main/src/model/emotion/wavlm_emotion.py \
  -o third_party/WavLM/emotion/wavlm_emotion.py
```

WavLM emotion 权重来源：

```text
https://huggingface.co/tiantiaf/wavlm-large-categorical-emotion
```

当前项目需要保留：

```text
models/wavlm-large-categorical-emotion/model.safetensors
models/wavlm-large-categorical-emotion/config.json
```

WavLM-Large 架构配置来源：

```text
https://huggingface.co/microsoft/wavlm-large
```

当前项目需要保留：

```text
models/wavlm-large/config.json
models/wavlm-large/preprocessor_config.json
```

代码里不要用 `models/wavlm-large-categorical-emotion/config.json` 初始化 WavLM 主干；它只是 emotion wrapper 的任务配置。应使用 `models/wavlm-large/config.json` 初始化 WavLM-Large 主干，再加载 `model.safetensors`。

AudioSet 标签表可从 Google 官方地址下载：

```text
http://storage.googleapis.com/us_audioset/youtube_corpus/v1/csv/class_labels_indices.csv
```

它用于把 BEATs checkpoint 里的 MID 映射成可读标签，例如 `/m/03qc9zr -> Screaming`、`/m/0463cq4 -> Crying, sobbing`。业务系统还需要再维护一层中文事件映射，例如 `Screaming -> 疑似尖叫`。

当前项目保存路径：

```text
models/audioset/class_labels_indices.csv
```

### 5.4 第一版接口

建议先提供异步分析接口：

```http
POST /api/v1/postcall/jobs
GET  /api/v1/postcall/jobs/{jobId}
GET  /api/v1/postcall/jobs/{jobId}/timeline
GET  /api/v1/postcall/jobs/{jobId}/evidence-clips
```

提交任务返回：

```json
{
  "jobId": "job_20260508_0001",
  "state": "processing_queued"
}
```

查询任务返回：

```json
{
  "jobId": "job_20260508_0001",
  "state": "completed",
  "timeline": []
}
```

## 6. 后续警务视角下的风险等级设计

建议风险等级不直接等同警情类别，而是表示“需要关注程度”：

```text
critical：疑似正在发生人身伤害、严重呼吸困难、连续尖叫/撞击/求救组合
high：强烈冲突、尖叫、哭泣、撞击集中出现
medium：单类异常声音或异常情绪明显，但证据不足
low：仅普通说话、环境声或低置信异常
unknown：音质太差、静音、模型不可判定
```

从民警角度，系统应优先回答三个问题：

1. 有没有需要立即关注的人身安全风险？
2. 风险出现在哪几个时间点，可以快速回放吗？
3. 系统为什么这么判断，有哪些模型证据和不确定性？

不要让系统输出如下结论：

- “确认家暴”；
- “确认有人被殴打”；
- “确认嫌疑人破门”；
- “确认报警人说谎”。

更合适的表达：

- “疑似尖叫，建议复核”；
- “疑似撞击/摔门声，建议结合通话内容判断”；
- “疑似恐惧或高唤醒人声，不作为单独处置依据”；
- “模型置信度低，建议人工听辨”。

## 7. 评估与上线要求

上线前至少要做以下验证：

- 收集本地真实报警音频样本，按事件片段标注；
- 每类至少统计 precision、recall、false positive、false negative；
- 单独评估低码率、强噪声、远场、多人说话、手机摩擦、车内环境；
- 对关键事件设置高召回策略，例如尖叫、哭泣、喘息、撞击；
- 对容易误报事件设置复核策略，例如门声、普通喊叫、背景电视声；
- 每次模型或阈值变更都记录版本；
- 不满足置信度要求时输出 `unknown`，不要强行给结论。

建议第一阶段指标目标：

```text
尖叫/哭泣/撞击：优先召回，允许人工复核消除误报
呼吸异常：只做弱提示，不做强结论
冲突：只做融合风险，不做单模型标签
人声情绪：只做辅助证据，不单独触发最高风险
```

## 8. 参考资料

- [AudioSet 官方说明](https://research.google.com/audioset/index.html)：AudioSet 使用 10 秒声音片段和大规模人工标注，适合作为通用声音事件识别标签体系参考。
- [AudioSet Ontology](https://research.google.com/audioset/ontology/index.html)：用于确认 `Screaming`、`Crying`、`Breathing`、`Door`、`Slam`、`Bang` 等声音事件标签。
- [BEATs: Audio Pre-Training with Acoustic Tokenizers](https://www.microsoft.com/en-us/research/?p=945876)：BEATs 的模型定位和 AudioSet/ESC-50 效果依据。
- [Microsoft UniLM BEATs README](https://github.com/microsoft/unilm/blob/master/beats/README.md)：BEATs checkpoint 命名和 AudioSet fine-tuned 模型来源。
- [WavLM - Microsoft Research](https://www.microsoft.com/en-us/research/?p=815242)：WavLM 作为语音表征模型的定位依据。
- [DCASE Sound Event Detection](https://dcase.community/challenge2017/task-sound-event-detection-in-real-life-audio)：成熟音频任务中 audio tagging、sound event detection、重叠事件检测等任务拆分参考。
- [APCO/NENA QA/QI Standard](https://www.apcointl.org/standards/1107x-apco-nena-standard-for-the-establishment-of-a-quality-assurance-and-quality-improvement-program-for-emergency-communications-centers/)：应急通信中心质检/复核工作流参考。
- [NIJ Forensic Handling of User Generated Audio Recordings](https://nij.ojp.gov/library/publications/forensic-handling-user-generated-audio-recordings)：音频证据处理、真实性和时间线同步思路参考。

## 9. 结论

当前两个模型可以组成报警音频分析的第一版基线：

- BEATs 负责声音事件：尖叫、哭泣、喊叫、喘息、门体/撞击等；
- WavLM 负责人声情绪：恐惧、愤怒、中性、悲伤、惊讶，以及 arousal/valence/dominance；
- 2 分钟音频必须切片处理，不能整段一次性判断；
- 最终输出应是时间线、风险片段、风险等级、摘要和可回放证据；
- “冲突”“呼吸异常”“痛苦”“压低声音”等业务标签需要融合规则或后续微调，不能直接宣称当前模型已经原生支持。

第一版建议按“规则融合 + 人工复核”上线，等积累足够本地标注数据后，再训练报警场景专用分类头或融合模型。
