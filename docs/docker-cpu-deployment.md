# Docker CPU 部署

本文档说明如何用 Docker Compose 启动 CPU 版 EchoX Call。默认部署会连接已有 PostgreSQL 服务，并在该 PostgreSQL 中创建 EchoX Call 自己的数据库和表。该部署方式保留 GPU 扩展空间：应用配置通过环境变量控制，后续只需要替换镜像构建方式和把 `POSTCALL_DEVICE` 改为 `cuda`。

## 1. 准备目录

在项目根目录准备模型和数据目录：

```bash
mkdir -p models data
cp .env.docker.example .env.docker
```

把模型文件放到 `models/`，并确认 `.env.docker` 中的路径与实际文件一致：

```text
models/
  BEATs_iter3_plus_AS2M_finetuned_on_AS2M_cpt2/
    BEATs_iter3_plus_AS2M_finetuned_on_AS2M_cpt2.pt
  wavlm-large-categorical-emotion/
  wavlm-large/
  speaker-diarization-community-1/
```

如果先用 `POSTCALL_ANALYSIS_PROFILE=fast`，worker 不会跑说话人分离，但建议仍按最终目录结构准备模型，减少后续切换成本。

## 2. 修改配置

编辑 `.env.docker`：

```env
POSTGRES_HOST=host.docker.internal
POSTGRES_PORT=5432
POSTGRES_USER=user
POSTGRES_PASSWORD=123456
POSTGRES_DB=echox_call
POSTGRES_ADMIN_DB=postgres
DATABASE_URL=postgresql://user:123456@host.docker.internal:5432/echox_call
POSTCALL_DEVICE=cpu
POSTCALL_ANALYSIS_PROFILE=fast
```

说明：

- `POSTGRES_HOST` 指已有 PostgreSQL 的地址。Linux 下 `docker-compose.yml` 已把 `host.docker.internal` 映射到宿主机，所以如果 PG 容器把 5432 映射到了宿主机端口，可以直接使用该值。
- `POSTGRES_ADMIN_DB` 是初始化连接用的维护库，一般是 `postgres`。
- `POSTGRES_DB` 是 EchoX Call 自己要创建和使用的数据库，建议不要复用其他业务库。
- `DATABASE_URL` 必须指向 `POSTGRES_DB`。

部署前还需要替换：

- `config/clients.yaml` 中的 API Key。
- `config/console_users.yaml` 中的控制台密码和 `session.secret`。

## 3. 构建镜像

```bash
docker compose --env-file .env.docker build api
```

镜像名固定为 `echox-call:cpu`，API、worker、console 共用该镜像。Dockerfile 已默认使用清华源：

- apt：`https://mirrors.tuna.tsinghua.edu.cn/debian`
- pip：`https://pypi.tuna.tsinghua.edu.cn/simple`
- PyTorch CPU wheels：默认使用 `https://download.pytorch.org/whl/cpu`，因为清华 PyTorch wheel 镜像可能缺少 `torch==2.8.0`。

如需尝试清华 PyTorch CPU wheel 源：

```bash
docker compose --env-file .env.docker build api \
  --build-arg PYTORCH_CPU_INDEX_URL=https://mirrors.tuna.tsinghua.edu.cn/pytorch-wheels/cpu \
  --build-arg PYTORCH_CPU_TRUSTED_HOST=mirrors.tuna.tsinghua.edu.cn
```

如果出现 `No matching distribution found for torch==2.8.0`，请使用默认构建命令，让 PyTorch 单独从官方 CPU wheel 源下载，其余 Python 依赖仍走清华 PyPI。

当前 Docker 镜像不使用 conda。如果后续增加 conda 环境，建议在构建前配置清华 conda 源：

```bash
conda config --add channels https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/main
conda config --add channels https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/r
conda config --add channels https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/msys2
conda config --add channels https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/pytorch
conda config --set show_channel_urls yes
```

## 4. 初始化数据库和表

在已有 PostgreSQL 中创建 EchoX Call 数据库：

```bash
docker compose --env-file .env.docker run --rm db-init
```

执行迁移创建业务表：

```bash
docker compose --env-file .env.docker run --rm migrate
```

## 5. 启动服务

```bash
docker compose --env-file .env.docker up -d api worker console
```

访问地址：

```text
API: http://服务器IP:8000
API 健康检查: http://服务器IP:8000/health
API 文档: http://服务器IP:8000/docs
控制台: http://服务器IP:8001/console/
```

查看日志：

```bash
docker compose --env-file .env.docker logs -f api
docker compose --env-file .env.docker logs -f worker
docker compose --env-file .env.docker logs -f console
```

## 6. CPU 模式建议

CPU 首次部署建议使用：

```env
POSTCALL_ANALYSIS_PROFILE=fast
POSTCALL_TORCH_NUM_THREADS=4
POSTCALL_TORCH_INTEROP_THREADS=1
```

`fast` 模式跳过说话人分离，资源压力较小。需要完整分析时可改成：

```env
POSTCALL_ANALYSIS_PROFILE=full
```

修改 `.env.docker` 后重启 worker：

```bash
docker compose --env-file .env.docker up -d --force-recreate worker
```

## 7. 停止服务

```bash
docker compose --env-file .env.docker down
```

如需连数据库数据一起删除：

```bash
docker compose --env-file .env.docker down -v
```

注意：默认使用外部 PostgreSQL，`docker compose down -v` 只会删除本 compose 创建的卷，不会删除已有 PG 容器中的数据库。

## 8. 后续离线部署准备

当前 CPU 版 Dockerfile 会在线安装 Python 依赖。后续完全离线部署时，建议在有网络的构建机完成：

```bash
docker compose --env-file .env.docker build api
docker save echox-call:cpu postgres:16 -o echox-call-cpu-images.tar
```

离线包至少包含：

```text
echox-call-cpu-images.tar
docker-compose.yml
.env.docker
config/
models/
data/
```

离线机器导入镜像后启动：

```bash
docker load -i echox-call-cpu-images.tar
docker compose --env-file .env.docker run --rm db-init
docker compose --env-file .env.docker run --rm migrate
docker compose --env-file .env.docker up -d api worker console
```
