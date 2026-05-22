# ARM64 离线部署指南

目标链路：

1. 当前机器打一个“ARM 构建输入包”，包含项目代码、配置、脚本和 `models/`。
2. 把输入包传到联网 ARM64 服务器，在 ARM64 服务器上构建镜像并生成最终离线包。
3. 把最终离线包传到离线 ARM64 服务器安装运行。

不要在 x86 机器上直接构建给 ARM 离线机使用的普通 Docker 镜像，除非你明确使用 `buildx --platform linux/arm64` 并验证镜像架构。最稳妥的方式是在联网 ARM64 服务器上原生构建。

## 1. 当前机器：生成 ARM 构建输入包

在项目根目录执行：

```bash
cd /home/ary/workspace/Projects/echox-call
mkdir -p dist
tar \
  --exclude='dist' \
  --exclude='data' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='._*' \
  --exclude='.DS_Store' \
  --exclude='.pytest_cache' \
  --exclude='.mypy_cache' \
  --exclude='.ruff_cache' \
  --exclude='.git' \
  -czf dist/echox-call-arm64-build-input-$(date +%Y%m%d%H%M%S).tar.gz \
  docker-compose.yml docker-compose.legacy.yml Dockerfile requirements.txt README.md offline_deploy.txt .dockerignore \
  .env.docker.example .env.docker \
  config docs migrations scripts src tests third_party models
```

这个包会包含 `models/`，不包含运行期 `data/`。如果 `.env.docker` 里有真实密钥，传输和保存时要按敏感文件处理。

## 2. 联网 ARM64 服务器：构建最终离线包

先确认服务器架构：

```bash
uname -m
```

应显示 `aarch64` 或 `arm64`。

安装并启动 Docker 后，把第一步生成的输入包上传到联网 ARM64 服务器，例如放到 `/opt/build/`：

```bash
mkdir -p /opt/build/echox-call
tar -xzf echox-call-arm64-build-input-*.tar.gz -C /opt/build/echox-call
cd /opt/build/echox-call
```

构建 ARM64 镜像并生成最终离线包：

```bash
./scripts/package_arm64_offline_bundle.sh
```

如果离线 ARM 服务器已经有可用 PostgreSQL，且不需要打包 `postgres:16` 镜像：

```bash
./scripts/package_arm64_offline_bundle.sh --skip-postgres
```

如果 PyTorch CPU 镜像源在 ARM 上下载失败，可以改用 PyPI 源构建：

```bash
docker build \
  --build-arg PYTORCH_CPU_INDEX_URL=https://pypi.org/simple \
  --build-arg PYTORCH_CPU_TRUSTED_HOST=pypi.org \
  -t echox-call:cpu .

./scripts/package_arm64_offline_bundle.sh --skip-build
```

如果 `apt-get update` 报类似下面的错误：

```text
Problem executing scripts APT::Update::Post-Invoke
Sub-process returned an error code
```

在 `Dockerfile` 的第一段 `RUN set -eux; \` 后面加入：

```dockerfile
    rm -f /etc/apt/apt.conf.d/docker-clean; \
    printf '%s\n' \
        'APT::Update::Post-Invoke "";' \
        'APT::Update::Post-Invoke-Success "";' \
        'DPkg::Post-Invoke "";' \
        > /etc/apt/apt.conf.d/99disable-post-invoke; \
```

然后重新执行打包脚本。

如果 `apt-get install` 报 `Processing was halted because there were too many errors` 或 `Sub-process /usr/bin/dpkg returned an error code (1)`，优先把基础镜像固定到 Debian bookworm：

```dockerfile
FROM python:3.11-slim-bookworm AS runtime
```

然后先拉取并打 tag：

```bash
docker pull docker.m.daocloud.io/library/python:3.11-slim-bookworm
docker tag docker.m.daocloud.io/library/python:3.11-slim-bookworm python:3.11-slim-bookworm
```

再重新执行打包脚本。

构建完成后会生成：

```text
dist/echox-call-arm64-cpu-offline-<时间>.tar.gz
```

这个最终离线包包含：

- `echox-call-arm64-cpu-image.tar.gz`
- 可选的 `postgres16-arm64-image.tar.gz`
- `echox-call-project-with-models.tar.gz`
- `install_offline.sh`
- `SHA256SUMS`

传输前建议校验镜像架构：

```bash
docker image inspect echox-call:cpu --format '{{.Architecture}}'
```

结果必须是：

```text
arm64
```

## 3. 离线 ARM64 服务器：安装

把最终离线包上传到离线 ARM64 服务器，例如 `/opt/packages/`：

```bash
mkdir -p /opt/packages
tar -xzf echox-call-arm64-cpu-offline-*.tar.gz -C /opt/packages
cd /opt/packages/echox-call-arm64-cpu-offline-*
sha256sum -c SHA256SUMS
```

安装到 `/opt/echox-call`：

```bash
sudo ./install_offline.sh /opt/echox-call
cd /opt/echox-call
```

编辑环境变量：

```bash
sudo vi .env.docker
sudo cp .env.docker .env
```

至少确认这些值：

```env
POSTGRES_HOST=<pg地址>
POSTGRES_PORT=5432
POSTGRES_USER=user
POSTGRES_PASSWORD=<pg密码>
POSTGRES_DB=echox_call
POSTGRES_ADMIN_DB=postgres

ECHOX_CALL_PORT=8000
ECHOX_CALL_CONSOLE_PORT=8002

LLM_API_KEY=<大模型key>
LLM_BASE_URL=<大模型OpenAI兼容地址，例如 http://x.x.x.x:8000/v1>
LLM_WORKER_MODEL=<模型名，例如 default>
LLM_WORKER_USE_TOOLS=0
```

如果使用本机 Docker PostgreSQL，并且 `host.docker.internal` 在旧 Docker 上不可解析，优先把 PG 容器加入同一个 compose 网络，或者把 `POSTGRES_HOST` 改成宿主机可访问的真实 IP。

## 4. 初始化数据库并启动服务

Docker Compose v2：

```bash
docker compose --env-file .env.docker run --rm migrate
docker compose --env-file .env.docker up -d api worker llm-worker console
```

旧版 `docker-compose`：

```bash
docker-compose -f docker-compose.legacy.yml run --rm migrate
docker-compose -f docker-compose.legacy.yml up -d api worker llm-worker console
```

如果旧版 `docker-compose` 报 `invalid IP address: 0.0.0.0` 或不识别 `env_file.path`，确认使用的是 `docker-compose.legacy.yml`。

## 5. 验证

查看容器：

```bash
docker ps
```

健康检查：

```bash
curl http://127.0.0.1:${ECHOX_CALL_PORT:-8000}/health
curl http://127.0.0.1:${ECHOX_CALL_CONSOLE_PORT:-8002}/health
```

查看日志：

```bash
docker compose --env-file .env.docker logs -f worker
docker compose --env-file .env.docker logs -f llm-worker
```

旧版：

```bash
docker-compose -f docker-compose.legacy.yml logs -f worker
docker-compose -f docker-compose.legacy.yml logs -f llm-worker
```

如果任务一直停在“分析中”，先看数据库中的最新任务和 LLM 任务：

```bash
docker exec -it postgres psql -U user -d echox_call -c "select job_id,jjdh,state,error_code,error_message,audio_completed_at,audio_analysis_data <> '{}'::jsonb as has_audio_data,created_at,updated_at from postcall_jobs order by created_at desc limit 5;"
docker exec -it postgres psql -U user -d echox_call -c "select job_id,state,error_code,error_message,llm_model,llm_output,created_at,updated_at from postcall_llm_jobs order by created_at desc limit 5;"
```

如果需要测试当前环境变量下的 PG 和 LLM 连通性：

```bash
docker compose --env-file .env.docker run --rm \
  -v "$PWD/scripts:/app/scripts:ro" \
  api python /app/scripts/check_runtime_connectivity.py
```
