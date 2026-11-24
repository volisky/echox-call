# EchoX Call 正式生产离线部署说明

本文档适用于正式版离线部署。离线包包含项目运行镜像、PostgreSQL 镜像、代码、配置、迁移脚本和模型文件。

## 1. 默认端口

```text
8000  API 服务
8002  控制台对外端口
8010  舆情研判接口
8012  标注平台网关
5432  PostgreSQL 对外端口
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
check_update.sh
README-production-offline.md
MANIFEST.txt
SHA256SUMS
```

`deploy.sh` 用于全新安装，`update.sh` 用于已在运行的现场做增量更新，`check_update.sh` 用于更新前的只读预检（见第 6 节）。

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
7. 启动 api、worker、audio-cleanup、console、opinion-risk-api、annotation-gateway
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
NVIDIA_VISIBLE_DEVICES=all
```

`POSTGRES_PASSWORD` 默认固定为 `LZdx2025`。

如果安装目录里已经存在 `.env.docker`，部署脚本默认会保留已有配置，不会覆盖 PG 密码。需要强制重新生成时再使用：

```bash
sudo ./deploy.sh --install-dir /data/zhuxx/echox-call --force-env
```

## 5. 模块说明

`postgres`

保存任务、音频资产、分析结果和控制台相关数据。默认使用 Docker volume `postgres_data` 持久化。

`api`

对外 HTTP API 服务。包含：

```text
POST /api/v1/postcall/jobs
GET  /api/v1/postcall/jobs/{jobId}
GET  /health
```

`worker`

后台处理音频任务。话后接口 `POST /api/v1/postcall/jobs` 仍接受原有全部字段（含 `callId`），请求格式不变；区别是升级后不再有实时音频 worker，任务一律由 `worker` 下载 `audioUrl` 指向的完整录音做话后分析。旧接入如果为 `callId` 任务提交过占位 `audioUrl`（实时模式下不会被下载），升级后必须提供可下载的真实录音地址，否则该任务会因下载失败而重试直至失败。

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
docker compose --env-file .env.docker -f deploy/offline/docker-compose.production.yml logs -f worker
```

重新执行迁移：

```bash
docker compose --env-file .env.docker -f deploy/offline/docker-compose.production.yml --profile tools run --rm migrate
```

重启服务：

```bash
docker compose --env-file .env.docker -f deploy/offline/docker-compose.production.yml restart api worker console
```

这里必须用 `restart`，不要用 `up -d`。业务代码是从宿主机 `src/` 挂载进去的，Python 不会热加载；而配置没有变化时 `up -d` 是空操作，容器不会被重建也不会重启，代码更新等于没生效。

反过来，`restart` 不会重新读取 `.env.docker`。改过 `.env.docker` 之后要重建容器：

```bash
docker compose --env-file .env.docker -f deploy/offline/docker-compose.production.yml up -d --force-recreate api worker console
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

### 更新前预检

执行 `update.sh` 之前，先用 `check_update.sh` 确认现场能否替换为新版本。预检是只读的：不改动安装目录，不重启容器，镜像内检查使用 `docker run --rm --network none` 的一次性容器。

为了让预检能对比依赖、迁移、配置和服务定义，归档里除 `src` 外一并带上这些文件。`update.sh` 只同步 `--paths` 指定的目录，多带不影响更新：

```bash
tar --exclude='__pycache__' --exclude='*.pyc' -czf echox-update-<提交号>.tar.gz \
  src migrations config requirements.txt deploy/offline/docker-compose.production.yml
```

```bash
cd /opt/echox-call
./deploy/offline/check_update.sh --archive /root/echox-update-<提交号>.tar.gz
```

检查内容：

```text
1. 安装目录、compose 文件、.env.docker、docker / compose / rsync 是否就绪
2. 新旧文件差异（新增 / 修改 / 将被删除）
3. requirements.txt、migrations/、config/、compose 文件是否有变化
4. 磁盘空间是否足够备份和同步
5. 在现有镜像里检查新代码：语法、各服务入口模块能否导入、依赖版本是否满足
```

退出码 0 表示可以更新，并在结尾打印对应的 `update.sh` 命令（需要迁移时已带上 `--paths "src migrations" --migrate`）；退出码 1 表示存在阻断项，例如现有镜像不满足新依赖、新代码无法导入、磁盘空间不足。`[WARN]` 项不阻断更新，但需要先人工确认。

## 7. 开启多个 worker

增加普通音频 worker：

```bash
docker compose --env-file .env.docker -f deploy/offline/docker-compose.production.yml up -d --scale worker=2 worker
```

多卡服务器可以通过 `.env.docker` 控制可见 GPU：

```env
NVIDIA_VISIBLE_DEVICES=0,1,2,3
```

如果要让不同 worker 固定不同 GPU，建议新增一个 compose override 文件，把服务拆成 `worker-gpu0`、`worker-gpu1`，并分别设置 `NVIDIA_VISIBLE_DEVICES=0/1`。

## 8. 验证

健康检查：

```bash
curl http://127.0.0.1:8000/health
```

CUDA 检查：

```bash
docker compose --env-file .env.docker -f deploy/offline/docker-compose.production.yml exec worker \
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
