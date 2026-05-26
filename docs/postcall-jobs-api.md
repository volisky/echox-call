# EchoX Call 报警音频分析接口文档

本文档按当前代码实现整理，对应 FastAPI 应用 `src/echox_call/api/app.py` 和 postcall 路由 `src/echox_call/api/v1/postcall.py`。

当前对外接口只有：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/health` | 服务健康检查 |
| `POST` | `/api/v1/postcall/jobs` | 创建或重新排队一条报警音频分析任务 |
| `GET` | `/api/v1/postcall/jobs/{job_id}` | 查询任务状态和已保存的分析结果 |

在线 OpenAPI 文档路径：

```text
GET /docs
GET /redoc
GET /openapi.json
```

## 1. 基本约定

### 1.1 基础地址

本地默认地址：

```text
http://127.0.0.1:8000
```

实际地址以部署环境为准。

### 1.2 鉴权

除 `/health` 外，postcall 接口必须携带请求头：

```http
X-API-Key: <client-api-key>
Content-Type: application/json
```

服务端从 `config/clients.yaml` 读取客户端配置。鉴权通过后，任务会记录当前客户端的 `client_id` 和 `source_system`。

鉴权失败规则：

| 场景 | HTTP 状态码 | `message` |
| --- | --- | --- |
| 缺少 `X-API-Key` | `401` | `missing X-API-Key` |
| API Key 不匹配 | `401` | `invalid X-API-Key` |
| 客户端被禁用 | `403` | `API client is disabled: <client_id>` |
| 客户端配置错误 | `500` | `API client config error: ...` |

### 1.3 统一响应结构

所有 API 响应都使用统一包裹结构：

```json
{
  "code": 0,
  "message": "success",
  "data": {},
  "timestamp": "2026-05-09T10:00:00+00:00"
}
```

字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `code` | `integer` | 成功固定为 `0`；失败时为 HTTP 状态码 |
| `message` | `string` | 成功为 `success`；失败为错误摘要 |
| `data` | `object` | 业务数据；无业务数据时为 `{}` |
| `timestamp` | `string` | 服务端 UTC 时间，ISO 8601 格式 |

参数校验失败返回 `422`，并在 `data.errors` 中返回 Pydantic / FastAPI 校验明细：

```json
{
  "code": 422,
  "message": "request validation failed",
  "data": {
    "errors": [
      {
        "type": "missing",
        "loc": ["body", "jjdh"],
        "msg": "Field required",
        "input": {}
      }
    ]
  },
  "timestamp": "2026-05-09T10:00:00+00:00"
}
```

### 1.4 处理中结果返回

音频 worker 和 LLM worker 是两条独立处理链路。查询任务时，如果其中任一部分已经完成，但整体任务还没有最终完成，`GET /api/v1/postcall/jobs/{job_id}` 会继续使用 `data.overallResult` 返回已完成的部分。

客户端只需要通过 `state` 判断 `overallResult` 是否最终结果：

- `state = completed`：`overallResult` 是最终综合结果。
- `state != completed`：`overallResult` 是当前已完成部分的临时结果，后续可能变化。

示例：

```json
{
  "jobId": "job_xxx",
  "jjdh": "JJD_20260509_0001",
  "state": "processing_analyzing",
  "overallResult": {
    "level": 3,
    "levelName": "暂无明显线索",
    "summary": [
      "分析总结：未发现明确二级以上警情。"
    ],
    "voiceResult": {},
    "inputSnapshot": {
      "alarmContent": "接警员填写的报警内容",
      "alarmAddress": "警情地址扩展字段",
      "isHighIncidentAddress": false
    }
  }
}
```

## 2. 健康检查

### 2.1 接口信息

```http
GET /health
```

该接口不需要 `X-API-Key`。

### 2.2 响应示例

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "status": "ok"
  },
  "timestamp": "2026-05-09T10:00:00+00:00"
}
```

## 3. 创建报警音频分析任务

### 3.1 接口信息

```http
POST /api/v1/postcall/jobs
Content-Type: application/json
X-API-Key: <client-api-key>
```

### 3.2 接口用途

提交一条报警音频分析任务。接口只负责入库和排队；音频下载、标准化、模型推理、规则判断由 worker 异步处理。

当前处理边界：

- `audioUrl` 会被 worker 下载并分析；
- `bjnr` 会保存，但当前不参与音频关注等级判断；
- `asrResult` 会保存，但当前不参与音频关注等级判断；
- `callbackUrl` 会保存，但当前没有回调投递逻辑；
- 对外结果只返回 `level`、`levelName` 和必要的 `reviewSegments`，不返回完整模型时间线。

### 3.3 HTTP 状态码

| 状态码 | 场景 |
| --- | --- |
| `201` | `jjdh` 首次提交，创建新任务 |
| `200` | 同一 `jjdh` 重复提交，覆盖旧任务字段并重新排队 |
| `401` | 鉴权失败 |
| `403` | 客户端被禁用 |
| `422` | 请求体字段缺失、类型错误、枚举值错误、URL 不合法、出现未定义字段等 |
| `500` | 数据库或服务端内部错误 |

### 3.4 重复提交规则

当前以 `jjdh` 作为同一任务的业务唯一键。

- 首次提交：新建任务，返回 `duplicate: false`；
- 重复提交：保留原 `jobId`，用新请求覆盖任务字段和 `audioUrl`，清理旧分析产物，重置为 `processing_queued`，返回 `duplicate: true`；
- 重复提交会让内部 `duplicate_count + 1`；
- 如果旧 worker 仍在处理上一版提交，旧版本结果写回会被拒绝，避免旧结果覆盖新请求。

### 3.5 请求参数

请求体不允许携带未定义字段。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `jjdh` | `string` | 是 | 接警单号；同一 `jjdh` 重复提交会重新排队 |
| `audioUrl` | `string` | 是 | 远程音频地址，必须是 `http` / `https` 地址；IP 字面量必须是公网地址 |
| `bjsj` | `string` | 是 | 报警时间，ISO 8601 日期时间 |
| `JCJXTJSDWMC` | `string` | 是 | 接处警系统警情接收单位名称 |
| `JJDWMC` | `string` | 是 | 接警单位名称 |
| `GXDWMC` | `string` | 是 | 管辖单位名称 |
| `bjdh` | `string` | 是 | 报警电话 |
| `bjrmc` | `string` | 是 | 报警人名称 |
| `bjrxbdm` | `integer` | 是 | 报警人性别代码，范围 `0` 到 `2` |
| `lxdh` | `string` | 是 | 联系电话 |
| `jqdz` | `string` | 是 | 警情地址 |
| `bjnr` | `string` | 是 | 报警内容，当前仅保存 |
| `jqlbdm` | `string` | 是 | 警情类别代码 |
| `jqlxdm` | `string` | 是 | 警情类型代码 |
| `jqxldm` | `string \| null` | 否 | 警情细类代码 |
| `jqzldm` | `string \| null` | 否 | 警情子类代码 |
| `jqdj` | `string` | 是 | 警情等级 |
| `callbackUrl` | `string \| null` | 否 | 回调地址；当前只校验并保存，不投递 |
| `asrResult` | `array<object> \| null` | 否 | 外部 ASR 文本；当前只校验并保存，不参与判断 |
| `alarmContent` | `string \| null` | 否 | 接警员填写的报警内容；当前只保存到原始请求快照 |
| `alarmAddress` | `string \| null` | 否 | 警情地址扩展字段；当前只保存到原始请求快照 |
| `isHighIncidentAddress` | `boolean \| null` | 否 | 是否高发案地址；当前只保存到原始请求快照 |
| `riskPerson` | `object \| null` | 否 | 风险人员信息；当前只保存到原始请求快照，不参与音频关注等级判断 |

必填字符串字段不能为空白字符串。可选字符串如果传入，也不能为空白字符串。

`audioUrl` 当前校验规则：

- 必须是 `http` 或 `https`；
- 必须包含 hostname；
- hostname 不能是 `localhost` 或 `*.localhost`；
- 如果 hostname 是 IP 字面量，不能是私有地址、回环地址、链路本地地址、多播地址、保留地址或未指定地址。

`callbackUrl` 当前校验规则：

- 如果传入，必须是合法的 `http` 或 `https` URL；
- 必须包含 hostname；
- 当前不限制公网地址；
- 当前不做回调投递。

`asrResult[]` 字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `speaker` | `string` | 是 | 只能是 `接警员` 或 `报警人` |
| `text` | `string` | 是 | ASR 文本，不能为空白字符串 |

`riskPerson` 字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `idcard` | `string \| null` | 否 | 身份证号或脱敏身份证号 |
| `tags` | `array<string>` | 否 | 风险标签，默认为空数组 |
| `report` | `string \| null` | 否 | 风险人员说明 |

`riskPerson` 不允许携带未定义字段。`idcard`、`report` 和 `tags[]` 如果传入，不能为空白字符串。

### 3.6 请求示例

```json
{
  "jjdh": "JJD_20260509_0001",
  "audioUrl": "https://example.com/audio/JJD_20260509_0001.wav",
  "bjsj": "2026-05-09T18:30:00+08:00",
  "JCJXTJSDWMC": "某市公安局接处警系统",
  "JJDWMC": "某市公安局指挥中心",
  "GXDWMC": "某市公安局某分局",
  "bjdh": "13800000000",
  "bjrmc": "张三",
  "bjrxbdm": 1,
  "lxdh": "13800000000",
  "jqdz": "某小区1栋2单元301",
  "bjnr": "报警人称现场有人争吵并听到撞击声",
  "jqlbdm": "01",
  "jqlxdm": "0101",
  "jqxldm": "010101",
  "jqzldm": "01010101",
  "jqdj": "2",
  "callbackUrl": "https://example.com/postcall/callback",
  "asrResult": [
    {
      "speaker": "接警员",
      "text": "你好，这里是110。"
    },
    {
      "speaker": "报警人",
      "text": "我要报警，现场有人争吵。"
    }
  ],
  "alarmContent": "接警员填写的报警内容",
  "alarmAddress": "警情地址扩展字段",
  "isHighIncidentAddress": true,
  "riskPerson": {
    "idcard": "321282************",
    "tags": ["暴力犯罪前科", "标签数据矛盾"],
    "report": "该人员曾因寻衅滋事罪获刑释放，存在暴力行为前科"
  }
}
```

### 3.7 响应示例

首次提交成功，HTTP 状态码为 `201`：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "jobId": "job_4bb27d3b984a4d5c9d5e5f5e43e7f04f",
    "jjdh": "JJD_20260509_0001",
    "state": "processing_queued",
    "duplicate": false
  },
  "timestamp": "2026-05-09T10:30:00+00:00"
}
```

重复提交成功，HTTP 状态码为 `200`：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "jobId": "job_4bb27d3b984a4d5c9d5e5f5e43e7f04f",
    "jjdh": "JJD_20260509_0001",
    "state": "processing_queued",
    "duplicate": true
  },
  "timestamp": "2026-05-09T10:31:00+00:00"
}
```

### 3.8 响应字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `data.jobId` | `string` | 服务端任务 ID |
| `data.jjdh` | `string` | 接警单号 |
| `data.state` | `string` | 创建后固定为 `processing_queued` |
| `data.duplicate` | `boolean` | 是否为同一 `jjdh` 的重复提交 |

## 4. 查询报警音频分析结果

### 4.1 接口信息

```http
GET /api/v1/postcall/jobs/{job_id}
X-API-Key: <client-api-key>
```

### 4.2 接口用途

查询当前客户端创建的任务状态和已保存结果。该接口只读数据库：

- 不触发音频下载；
- 不触发模型推理；
- 不实时重算规则；
- 不根据 `bjnr` 或 `asrResult` 生成结论。

路径参数 `job_id` 传服务端创建任务时返回的 `jobId` 值。

### 4.3 权限规则

- 只能查询当前 `X-API-Key` 对应客户端创建的任务；
- 任务不存在或不属于当前客户端时，统一返回 `404`；
- 这样可以避免调用方探测其他客户端任务是否存在。

### 4.4 HTTP 状态码

| 状态码 | 场景 |
| --- | --- |
| `200` | 查询成功 |
| `401` | 鉴权失败 |
| `403` | 客户端被禁用 |
| `404` | 任务不存在，或当前客户端无权访问 |
| `500` | 数据库错误，或已保存的结果快照不符合对外合同 |

### 4.5 响应字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `data.jobId` | `string` | 服务端任务 ID |
| `data.jjdh` | `string` | 接警单号 |
| `data.state` | `string` | 任务状态 |
| `data.level` | `integer \| null` | 关注等级；未完成或失败时为 `null` |
| `data.levelName` | `string \| null` | 关注等级中文名；未完成或失败时为 `null` |
| `data.reviewSegments` | `array<object>` | 复核片段；仅 `level=1` 或 `level=2` 时返回 |

任务状态 `state` 当前枚举：

| 枚举值 | 说明 |
| --- | --- |
| `processing_queued` | 已入队，等待 worker 处理 |
| `processing_downloading` | worker 已认领，正在下载音频 |
| `processing_analyzing` | 正在执行模型分析和规则判断 |
| `completed` | 已完成 |
| `failed` | 执行失败 |
| `failed_cancelled` | 已取消或终止 |

关注等级 `level` 当前枚举：

| `level` | `levelName` | 说明 |
| --- | --- | --- |
| `1` | `需要关注` | 规则线索层命中需要关注的复核线索 |
| `2` | `建议复核` | 存在较弱声音线索或多维语音状态波动，但未达到需要关注条件 |
| `3` | `暂无明显线索` | 未发现明显复核线索 |

注意：`level` 是音频规则线索层的关注等级，不是警情等级，也不是高/中/低风险等级。当前最高关注级别是 `1=需要关注`。

`reviewSegments[]` 当前只对外返回三个字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `startSec` | `number` | 复核片段开始时间，单位秒 |
| `endSec` | `number` | 复核片段结束时间，单位秒 |
| `result` | `string` | 规则整理后的复核结果，例如 `疑似哭泣` |

### 4.6 响应示例

任务未完成：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "jobId": "job_4bb27d3b984a4d5c9d5e5f5e43e7f04f",
    "jjdh": "JJD_20260509_0001",
    "state": "processing_analyzing",
    "level": null,
    "levelName": null
  },
  "timestamp": "2026-05-09T10:32:00+00:00"
}
```

任务完成且需要关注：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "jobId": "job_4bb27d3b984a4d5c9d5e5f5e43e7f04f",
    "jjdh": "JJD_20260509_0001",
    "state": "completed",
    "level": 1,
    "levelName": "需要关注",
    "reviewSegments": [
      {
        "startSec": 0.0,
        "endSec": 12.147,
        "result": "疑似哭泣"
      }
    ]
  },
  "timestamp": "2026-05-09T10:35:00+00:00"
}
```

任务完成且暂无明显线索：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "jobId": "job_4bb27d3b984a4d5c9d5e5f5e43e7f04f",
    "jjdh": "JJD_20260509_0001",
    "state": "completed",
    "level": 3,
    "levelName": "暂无明显线索"
  },
  "timestamp": "2026-05-09T10:35:00+00:00"
}
```

任务失败：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "jobId": "job_4bb27d3b984a4d5c9d5e5f5e43e7f04f",
    "jjdh": "JJD_20260509_0001",
    "state": "failed",
    "level": null,
    "levelName": null
  },
  "timestamp": "2026-05-09T10:35:00+00:00"
}
```

任务不存在或无权访问：

```json
{
  "code": 404,
  "message": "postcall job not found",
  "data": {},
  "timestamp": "2026-05-09T10:35:00+00:00"
}
```

### 4.7 当前明确不返回的字段

当前 GET 结果不对外返回以下内部字段：

```text
analysis
audio
timeline
insights
matchedRuleCodes
audioEventScores
voiceEmotionScores
voiceEmotionDimensions
voiceDetailedScores
riskLevel
riskTypes
riskEvaluated
confidence
reason
sourceSegments
modelVersions
audioProcessing
```

这些信息仍可能保存在内部数据库表中，用于排查、规则重算和模型输出核对。

## 5. cURL 示例

创建任务：

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/postcall/jobs" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <client-api-key>" \
  -d '{
    "jjdh": "JJD_20260509_0001",
    "audioUrl": "https://example.com/audio/JJD_20260509_0001.wav",
    "bjsj": "2026-05-09T18:30:00+08:00",
    "JCJXTJSDWMC": "某市公安局接处警系统",
    "JJDWMC": "某市公安局指挥中心",
    "GXDWMC": "某市公安局某分局",
    "bjdh": "13800000000",
    "bjrmc": "张三",
    "bjrxbdm": 1,
    "lxdh": "13800000000",
    "jqdz": "某小区1栋2单元301",
    "bjnr": "报警人称现场有人争吵并听到撞击声",
    "jqlbdm": "01",
    "jqlxdm": "0101",
    "jqdj": "2"
  }'
```

查询结果：

```bash
curl -X GET "http://127.0.0.1:8000/api/v1/postcall/jobs/job_4bb27d3b984a4d5c9d5e5f5e43e7f04f" \
  -H "X-API-Key: <client-api-key>"
```

## 6. 处理链路说明

当前完整链路是异步任务制：

```text
POST 创建任务
  -> postcall_jobs.state = processing_queued
  -> worker 认领任务
  -> 下载 audioUrl
  -> 标准化为 16kHz 单声道 WAV
  -> BEATs 声音事件分析
  -> WavLM 人声状态分析
  -> 规则线索层生成 level / levelName / reviewSegments
  -> 保存结果
  -> GET 查询已保存结果
```

当前实现限制：

- `GET /api/v1/postcall/jobs/{job_id}` 不会临时生成分析结果；
- `reviewSegments` 是确定性规则输出，不是大模型生成文案；
- 模型原始分数和完整时间线只入库，不直接暴露给外部调用方；
- 当前未实现 callback 投递；
- 当前未实现外部 ASR 文本融合；
- 当前未输出风险等级和处置建议。
