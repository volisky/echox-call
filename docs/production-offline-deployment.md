# EchoX Call 正式生产离线部署说明

本文档适用于实时音频正式版部署。离线包包含项目运行镜像、PostgreSQL 镜像、代码、配置、迁移脚本和模型文件。

## 1. 默认端口

```text
8000  API 服务，正式实时音频接收接口也在这个端口
8002  控制台对外端口
5432  PostgreSQL 对外端口
```

正式实时音频接收地址：

```text
POST http://服务器IP:8000/gateway/asr/push
```

控制台地址：

```text
http://服务器IP:8002/console/
```

## 2. 离线包内容

```text
images/echox-call-stream-cuda.tar.gz
images/postgres16.tar.gz
project/echox-call-project-with-models.tar.gz
deploy.sh
README-production-offline.md
MANIFEST.txt
SHA256SUMS
```

## 3. 一键部署

在离线服务器上解压外层包：

```bash
tar -xzf echox-call-stream-production-offline-*.tar.gz
cd echox-call-stream-production-offline-*
```

部署到默认目录 `/opt/echox-call`：

```bash
sudo ./deploy.sh
```

部署到指定目录：

```bash
sudo ./deploy.sh --install-dir /data/zhuxx/echox-call
```

只安装文件和镜像，不启动服务：

```bash
sudo ./deploy.sh --install-dir /data/zhuxx/echox-call --no-start
```

脚本会执行：

```text
1. 校验 SHA256SUMS
2. docker load 项目镜像和 postgres:16
3. 解压代码、配置和模型
4. 生成 .env.docker
5. 启动 postgres
6. 执行数据库迁移
7. 启动 api、worker、realtime-worker、console
```

默认不启动 `llm-worker`，因为生产环境通常需要先确认 LLM 地址和密钥。需要一起启动时：

```bash
sudo START_LLM_WORKER=1 ./deploy.sh --install-dir /data/zhuxx/echox-call
```

## 4. 关键配置

部署后配置文件在：

```text
/opt/echox-call/.env.docker
```

或你指定的安装目录：

```text
/data/zhuxx/echox-call/.env.docker
```

核心配置：

```env
ECHOX_CALL_PORT=8000
ECHOX_CALL_CONSOLE_PORT=8002
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_HOST_PORT=5432
POSTGRES_USER=echox
POSTGRES_PASSWORD=LZdx@2025
DATABASE_URL=postgresql://echox:LZdx%402025@postgres:5432/echox_call
POSTCALL_DEVICE=cuda
POSTCALL_ANALYSIS_PROFILE=fast
POSTCALL_WORKER_SKIP_CALL_ID_JOBS=1
POSTCALL_REALTIME_WINDOW_SEC=10
POSTCALL_REALTIME_TAIL_MIN_SEC=3
NVIDIA_VISIBLE_DEVICES=all
```

`POSTGRES_PASSWORD` 默认固定为 `LZdx@2025`。因为密码包含 `@`，所以 `DATABASE_URL` 中需要写成 URL 编码形式 `LZdx%402025`。

如果安装目录里已经存在 `.env.docker`，部署脚本默认会保留已有配置，不会覆盖 PG 密码。需要强制重新生成时再使用：

```bash
sudo ./deploy.sh --install-dir /data/zhuxx/echox-call --force-env
```

`POSTCALL_WORKER_SKIP_CALL_ID_JOBS=1` 表示带 `callId` 的实时音频任务由 `realtime-worker` 处理，普通 `worker` 不再重复下载占位 `audioUrl`。

## 5. 模块说明

`postgres`

保存任务、实时音频流、分片、窗口分析结果、最终结果和控制台相关数据。默认使用 Docker volume `postgres_data` 持久化。

`api`

对外 HTTP API 服务。包含：

```text
POST /gateway/asr/push
POST /api/v1/postcall/jobs
GET  /api/v1/postcall/jobs/{jobId}
GET  /health
```

正式流式音频入口就是 `api` 的 `/gateway/asr/push`。

`realtime-worker`

后台处理实时音频分片。每累计 10 秒音频分析一次，结束时汇总结果；如果最后剩余音频不足 3 秒，会忽略尾段并汇总已完成窗口。

`worker`

后台处理原始后处理音频任务。生产实时模式下建议保留，但配置为跳过带 `callId` 的任务，避免和 `realtime-worker` 重复处理同一通电话。

`llm-worker`

后台做 LLM 综合归纳。需要配置 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_WORKER_MODEL` 后再启动。

`console`

控制台服务，默认从宿主机 `8002` 访问。

## 6. 启动和维护命令

以下命令都在安装目录执行：

```bash
cd /opt/echox-call
```

查看状态：

```bash
docker compose --env-file .env.docker -f deploy/offline/docker-compose.production.yml ps
```

查看日志：

```bash
docker compose --env-file .env.docker -f deploy/offline/docker-compose.production.yml logs -f api
docker compose --env-file .env.docker -f deploy/offline/docker-compose.production.yml logs -f realtime-worker
docker compose --env-file .env.docker -f deploy/offline/docker-compose.production.yml logs -f worker
```

重新执行迁移：

```bash
docker compose --env-file .env.docker -f deploy/offline/docker-compose.production.yml --profile tools run --rm migrate
```

重启服务：

```bash
docker compose --env-file .env.docker -f deploy/offline/docker-compose.production.yml up -d api worker realtime-worker console
```

启动 LLM worker：

```bash
docker compose --env-file .env.docker -f deploy/offline/docker-compose.production.yml up -d llm-worker
```

## 7. 开启多个 worker

增加普通音频 worker：

```bash
docker compose --env-file .env.docker -f deploy/offline/docker-compose.production.yml up -d --scale worker=2 worker
```

增加实时音频 worker：

```bash
docker compose --env-file .env.docker -f deploy/offline/docker-compose.production.yml up -d --scale realtime-worker=2 realtime-worker
```

同时扩容：

```bash
docker compose --env-file .env.docker -f deploy/offline/docker-compose.production.yml up -d \
  --scale worker=2 \
  --scale realtime-worker=2 \
  worker realtime-worker
```

多卡服务器可以通过 `.env.docker` 控制可见 GPU：

```env
NVIDIA_VISIBLE_DEVICES=0,1,2,3
```

如果要让不同 worker 固定不同 GPU，建议新增一个 compose override 文件，把服务拆成 `worker-gpu0`、`worker-gpu1`、`realtime-worker-gpu0`、`realtime-worker-gpu1`，并分别设置 `NVIDIA_VISIBLE_DEVICES=0/1`。

## 8. 接入方请求格式

```text
POST /gateway/asr/push
Content-Type: multipart/form-data
```

字段：

```text
file
vendor_specific_param=agentid=001650887;usrdn=19299487507;callid=1781957692-917863;
voice_id
seq
final
end
voice_format=12
```

返回：

```json
{
  "code": 0,
  "message": "success"
}
```

curl 测试时 `vendor_specific_param` 建议用 `--form-string`，因为其中包含分号：

```bash
curl -X POST http://127.0.0.1:8000/gateway/asr/push \
  -H "Tracking-Id: test-001" \
  -F "file=@chunk0.wav;type=audio/wav" \
  --form-string "vendor_specific_param=agentid=001650887;usrdn=19299487507;callid=test-call-001;" \
  -F "voice_id=testvoice000001" \
  -F "seq=0" \
  -F "final=0" \
  -F "end=0" \
  -F "voice_format=12"
```

## 9. 验证

健康检查：

```bash
curl http://127.0.0.1:8000/health
```

CUDA 检查：

```bash
docker compose --env-file .env.docker -f deploy/offline/docker-compose.production.yml exec realtime-worker \
  python -c "import torch; print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0))"
```

端口未能外部访问时，优先检查云安全组是否放行：

```text
8000/tcp
8002/tcp
5432/tcp 如果需要外部访问数据库
```
