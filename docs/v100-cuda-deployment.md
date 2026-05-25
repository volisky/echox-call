# V100 CUDA 部署指南

本文档用于在 NVIDIA Tesla V100 服务器上部署 EchoX Call，并让音频分析 `worker` 使用 GPU。

## 1. 前置检查

在目标服务器上先确认 NVIDIA 驱动和 Docker GPU 运行时可用：

```bash
nvidia-smi
docker --version
docker compose version || docker-compose --version
```

安装 NVIDIA Container Toolkit 后，执行：

```bash
docker run --rm --gpus all nvidia/cuda:12.6.3-base-ubuntu22.04 nvidia-smi
```

如果服务器是旧版 `docker-compose`，也可以用：

```bash
docker run --rm --runtime nvidia -e NVIDIA_VISIBLE_DEVICES=all nvidia/cuda:12.6.3-base-ubuntu22.04 nvidia-smi
```

能在容器内看到 V100 才继续部署。

## 2. 构建 CUDA 镜像

默认 CUDA 镜像使用 `Dockerfile.cuda`，安装 `torch==2.8.0` 和 `torchaudio==2.8.0` 的 CUDA 12.6 wheel：

```bash
cd /opt/echox-call
docker compose --env-file .env.docker -f docker-compose.yml -f docker-compose.cuda.yml build api
```

如果没有 Compose v2：

```bash
docker build -f Dockerfile.cuda -t echox-call:cuda .
```

> 注意：CUDA 12.6 wheel 需要服务器 NVIDIA 驱动版本足够新。若 `torch.cuda.is_available()` 为 `False`，优先升级驱动或确认 NVIDIA Container Toolkit 安装正确。

## 3. 环境变量

`.env.docker` 中数据库、端口、LLM 配置保持和 CPU 部署一致。GPU 设备选择由 `docker-compose.cuda.yml` 对 `worker` 注入：

```env
POSTCALL_DEVICE=cuda
```

建议 V100 初始参数：

```env
POSTCALL_WORKER_BATCH_SIZE=1
POSTCALL_TORCH_NUM_THREADS=4
POSTCALL_TORCH_INTEROP_THREADS=1
```

如果服务器有多张 GPU，可以通过环境变量限制 worker 使用哪张：

```bash
export NVIDIA_VISIBLE_DEVICES=0
```

## 4. 启动

Compose v2：

```bash
docker compose --env-file .env.docker -f docker-compose.yml -f docker-compose.cuda.yml run --rm migrate
docker compose --env-file .env.docker -f docker-compose.yml -f docker-compose.cuda.yml up -d api worker llm-worker console
```

旧版 `docker-compose`：

```bash
cp .env.docker .env
docker-compose -f docker-compose.legacy.yml -f docker-compose.cuda.legacy.yml run --rm migrate
docker-compose -f docker-compose.legacy.yml -f docker-compose.cuda.legacy.yml up -d api worker llm-worker console
```

## 5. 验证 GPU

```bash
docker compose --env-file .env.docker -f docker-compose.yml -f docker-compose.cuda.yml exec worker \
  python -c "import torch; print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0))"
```

期望输出类似：

```text
True
Tesla V100-SXM2-32GB
```

查看 worker 日志：

```bash
docker compose --env-file .env.docker -f docker-compose.yml -f docker-compose.cuda.yml logs -f worker
```

## 6. 离线打包

在联网 V100/x86_64 服务器上构建并打包：

```bash
./scripts/package_cuda_offline_bundle.sh --skip-postgres
```

生成：

```text
dist/echox-call-cuda-offline-<时间>.tar.gz
```

传到离线 V100 服务器后：

```bash
tar -xzf echox-call-cuda-offline-*.tar.gz -C /opt/packages
cd /opt/packages/echox-call-cuda-offline-*
sudo ./install_offline.sh /opt/echox-call
cd /opt/echox-call
vi .env.docker
docker compose --env-file .env.docker -f docker-compose.yml -f docker-compose.cuda.yml run --rm migrate
docker compose --env-file .env.docker -f docker-compose.yml -f docker-compose.cuda.yml up -d api worker llm-worker console
```
