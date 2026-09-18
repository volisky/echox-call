# EchoX Call 正式生产离线部署说明

本文档适用于实时音频正式版部署。离线包包含项目运行镜像、PostgreSQL 镜像、代码、配置、迁移脚本和模型文件。

## 1. 默认端口

```text
8000  API 服务，正式实时音频接收接口也在这个端口
8002  控制台对外端口
8010  舆情研判接口
8012  标注平台网关
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
update.sh
README-production-offline.md
MANIFEST.txt
SHA256SUMS
```

`deploy.sh` 用于全新安装，`update.sh` 用于已在运行的现场做增量更新（见第 6 节）。

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
7. 启动 api、worker、audio-cleanup、realtime-worker、console、opinion-risk-api、annotation-gateway
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
POSTGRES_PASSWORD=LZdx2025
DATABASE_URL=postgresql://echox:LZdx2025@postgres:5432/echox_call
POSTCALL_DEVICE=cuda
POSTCALL_ANALYSIS_PROFILE=fast
POSTCALL_WORKER_SKIP_CALL_ID_JOBS=1
POSTCALL_REALTIME_WINDOW_SEC=10
POSTCALL_REALTIME_TAIL_MIN_SEC=3
NVIDIA_VISIBLE_DEVICES=all
```

`POSTGRES_PASSWORD` 默认固定为 `LZdx2025`。

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

`opinion-risk-api`

舆情研判接口，默认端口 `8010`，配置项 `OPINION_RISK_PORT`。

`annotation-gateway`

标注平台网关，默认端口 `8012`，把 `ANNOTATION_GATEWAY_PREFIX`（默认 `/landa/web/biaozhu`）反代到控制台，依赖 `console` 健康后再启动。超时由 `ANNOTATION_GATEWAY_TIMEOUT_SECONDS` 控制，默认 120 秒。改过前缀或目标地址后需要 `up -d --force-recreate annotation-gateway` 才能生效。

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
docker compose --env-file .env.docker -f deploy/offline/docker-compose.production.yml restart api worker realtime-worker console
```

这里必须用 `restart`，不要用 `up -d`。业务代码是从宿主机 `src/` 挂载进去的，Python 不会热加载；而配置没有变化时 `up -d` 是空操作，容器不会被重建也不会重启，代码更新等于没生效。

反过来，`restart` 不会重新读取 `.env.docker`。改过 `.env.docker` 之后要重建容器：

```bash
docker compose --env-file .env.docker -f deploy/offline/docker-compose.production.yml up -d --force-recreate api worker realtime-worker console
```

启动 LLM worker：

```bash
docker compose --env-file .env.docker -f deploy/offline/docker-compose.production.yml up -d llm-worker
```

重启 worker 时正在处理的任务不会丢：任务行还在数据库里，等 `locked_until` 过期后会由 `recover_expired_jobs()` 回收重排。LLM worker 锁默认 120 秒，音频 worker 默认 600 秒，回收会消耗一次重试（最多 3 次），所以不要连续快速重启。

### 增量更新代码

镜像里只有 Python/CUDA 运行时和依赖（`Dockerfile.cuda` 只 `COPY requirements.txt`），业务代码是通过 compose 的 `../../src:/app/src:ro` 从宿主机挂载的。所以**只改代码时不需要重建镜像、也不需要往离线网内传镜像包**，同步 `src/` 再重启容器即可。

在外网打包增量代码：

```bash
git archive --format=tar HEAD src | gzip -9 > echox-src-<提交号>.tar.gz
sha256sum echox-src-<提交号>.tar.gz
```

把该 tar.gz 和 `update.sh` 带进离线网，然后在安装目录执行：

```bash
cd /opt/echox-call
./deploy/offline/update.sh --archive /root/echox-src-<提交号>.tar.gz --dry-run   # 先确认动作
./deploy/offline/update.sh --archive /root/echox-src-<提交号>.tar.gz             # 执行
```

脚本会备份 `src`（默认保留 3 份）→ 原地覆盖 → 重启服务，最后打印容器状态。

注意事项：

```text
1. 必须原地覆盖，不要删掉 src 目录再解压。目录被替换后 inode 变化，运行中的容器仍读到旧内容，
   直到重启才恢复——会出现「宿主机已更新、容器里还是旧代码」的假更新。
2. 只改代码用 restart；改了 .env.docker 用 --recreate。
3. 只有 requirements.txt 变化时才需要重建镜像并重新传输镜像包。
4. 回滚：ls -d src.bak.* 找到备份目录，拷回 src 后重启服务。
```

常用参数：`--source DIR`（直接用本地目录）、`--paths`（只更新指定子路径）、`--services all`、`--migrate`、`--keep N`、`--no-restart`。

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
8010/tcp 如果需要外部调用舆情研判接口
8012/tcp 如果需要外部访问标注平台
5432/tcp 如果需要外部访问数据库
```
