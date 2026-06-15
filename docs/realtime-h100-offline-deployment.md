# 实时音频流 H100 离线部署指南

本文档用于部署实时音频流版本。该版本新增 `/gateway/asr/push` 音频流式接收接口，按固定 10 秒窗口分析音频，并继续沿用原有后处理查询接口返回结果。

## 1. 版本说明

镜像名：

```text
echox-call:stream-cuda
```

该镜像只包含 Python/CUDA 运行环境和依赖，业务代码通过 Docker Compose 从宿主机目录挂载：

```text
src/          -> /app/src
migrations/   -> /app/migrations
docs/         -> /app/docs
third_party/  -> /app/third_party
models/       -> /app/models
data/         -> /app/data
config/       -> /app/config
```

这样离线服务器上修改代码、配置或模型后，只需要重启容器，不需要重新构建镜像。

## 2. 离线包内容

建议离线包至少包含：

```text
echox-call-stream-cuda-YYYYMMDD.tar.gz
docker-compose.yml
docker-compose.cuda.yml
.env.docker
config/
data/
docs/
migrations/
models/
src/
third_party/
```

如果离线服务器没有 PostgreSQL 镜像，还需要额外准备：

```bash
docker save postgres:16 -o postgres-16.tar
```

## 3. H100 前置检查

在目标服务器上确认 NVIDIA 驱动和 Docker GPU 运行时可用：

```bash
nvidia-smi
docker --version
docker compose version
docker run --rm --gpus all nvidia/cuda:12.6.3-base-ubuntu22.04 nvidia-smi
```

容器内能看到 H100 后再启动服务。

## 4. 导入镜像

```bash
gzip -dc echox-call-stream-cuda-YYYYMMDD.tar.gz | docker load
docker image ls echox-call:stream-cuda
```

如果镜像包未压缩：

```bash
docker load -i echox-call-stream-cuda-YYYYMMDD.tar
```

## 5. 环境变量

从示例文件生成配置：

```bash
cp .env.docker.example .env.docker
```

实时流相关配置：

```env
POSTCALL_REALTIME_STORAGE_DIR=/app/data/realtime
POSTCALL_REALTIME_WINDOW_SEC=10
POSTCALL_REALTIME_TAIL_MIN_SEC=3
POSTCALL_REALTIME_WORKER_BATCH_SIZE=1
POSTCALL_REALTIME_WORKER_SLEEP_SECONDS=2.0
```

H100 部署建议：

```env
POSTCALL_DEVICE=cuda
POSTCALL_ANALYSIS_PROFILE=fast
POSTCALL_WORKER_BATCH_SIZE=1
POSTCALL_WORKER_SKIP_CALL_ID_JOBS=1
POSTCALL_TORCH_NUM_THREADS=4
POSTCALL_TORCH_INTEROP_THREADS=1
```

`POSTCALL_WORKER_SKIP_CALL_ID_JOBS=1` 表示原音频 worker 不处理带 `callId` 的实时流任务，实时音频由 `realtime-worker` 处理。

## 6. 初始化和启动

执行数据库初始化和迁移：

```bash
docker compose --env-file .env.docker -f docker-compose.yml -f docker-compose.cuda.yml run --rm db-init
docker compose --env-file .env.docker -f docker-compose.yml -f docker-compose.cuda.yml run --rm migrate
```

启动服务：

```bash
docker compose --env-file .env.docker -f docker-compose.yml -f docker-compose.cuda.yml up -d api worker llm-worker realtime-worker console
```

查看状态：

```bash
docker compose --env-file .env.docker -f docker-compose.yml -f docker-compose.cuda.yml ps
docker compose --env-file .env.docker -f docker-compose.yml -f docker-compose.cuda.yml logs -f realtime-worker
```

验证 CUDA：

```bash
docker compose --env-file .env.docker -f docker-compose.yml -f docker-compose.cuda.yml exec realtime-worker \
  python -c "import torch; print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0))"
```

## 7. 实时音频接口

接口：

```text
POST /gateway/asr/push
Content-Type: multipart/form-data
```

表单字段：

```text
file                    音频片段，默认 wav
vendor_specific_param   例如 agentid=001650887;usrdn=19299487507;callid=1781957692-917863;
voice_id                音频唯一标识
seq                     分片序号，从 0 开始
final                   是否最后一个识别包
end                     是否最后一片音频，最后一片为 1
voice_format            默认 12，表示 wav
```

返回：

```json
{
  "code": 0,
  "message": "success"
}
```

实时 worker 会在累计每 10 秒音频后分析一次；结束时如果剩余尾段小于 3 秒，会忽略尾段并汇总已完成窗口。

## 8. 原数据请求和结果查询

原后处理任务创建接口继续使用原路径，新增 `callId` 字段：

```json
{
  "jjdh": "案件编号",
  "callId": "1781957692-917863"
}
```

如果音频先到，系统会先保存并分析实时音频；等原数据请求带同一个 `callId` 到达后，再合并到该任务结果。

查询仍使用原接口：

```text
GET /api/v1/postcall/jobs/{jobId}
```

返回结果中会包含 `callId`。音频情绪分段由每个 10 秒窗口内的模型分段加上窗口起始时间换算成整段音频时间。

## 9. 告警回调

当前版本先不调用外部服务。若实时或最终汇总结果命中原有规则中的 1 级“需要关注”或 2 级“建议复核/建议关注”，系统会在 `postcall_realtime_alert_notifications` 表中保存待发送载荷，状态为 `skipped`。

预留回调载荷字段：

```json
{
  "jjdh": "案件编号",
  "callId": "1781957692-917863",
  "level": 1,
  "levelName": "需要关注",
  "emotionTypes": ["愤怒", "悲伤"]
}
```

待外部接口确定后，再把 `skipped` 改成实际发送逻辑。

## 10. CPU 调试

同一个镜像可以在 CPU 环境运行。CPU 调试时不要使用需要 GPU 的 compose 覆盖，或删除 `docker-compose.cuda.yml` 中服务的 `gpus: all` 配置，并设置：

```env
POSTCALL_DEVICE=cpu
POSTCALL_ANALYSIS_PROFILE=fast
```

单容器导入验证示例：

```bash
docker run --rm -v "$PWD/src:/app/src:ro" echox-call:stream-cuda \
  python -c "import torch; from echox_call.api.app import app; print(torch.cuda.is_available()); print(len(app.routes))"
```
