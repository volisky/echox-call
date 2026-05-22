# EchoX Call

报警通话音频分析服务。本地启动入口统一使用根目录的 `start.sh`。

## 启动前准备

首次启动或依赖变更后安装依赖：

```bash
./start.sh install
```

确认 `.env` 中的数据库连接可用：

```env
DATABASE_URL=postgresql://用户名:密码@localhost:5432/echox_call
```

检查数据库连接：

```bash
./start.sh ping
```

## 本地启动

先执行数据库迁移：

```bash
./start.sh migrate
```

启动 API 服务：

```bash
./start.sh api
```

另开一个终端，启动音频分析 worker：

```bash
./start.sh worker
```

worker 是常驻轮询进程。没有待处理任务时会继续等待，并定时输出 `worker idle: no queued jobs`。
worker 数量由 `.env` 控制，启动命令不变：

```env
ECHOX_CALL_WORKER_COUNT=1
ECHOX_CALL_WORKER_ID_PREFIX=postcall-worker
```

部署时如需两个 worker，把 `ECHOX_CALL_WORKER_COUNT` 改成 `2` 后仍执行：

```bash
./start.sh worker
```

控制台是独立服务，不启动控制台不影响 API。需要查看管理控制台时，另开终端启动：

```bash
./start.sh console
```

## 访问地址

- API：http://127.0.0.1:8000
- API 健康检查：http://127.0.0.1:8000/health
- API 文档：http://127.0.0.1:8000/docs
- 控制台：http://127.0.0.1:8001/console/

控制台需要登录后访问，账号配置在 `config/console_users.yaml`。默认账号：

```text
用户名：admin
密码：EchoxCall@2026
```

## 端口配置

端口和监听地址在 `.env` 中配置，由 `start.sh` 读取：

```env
ECHOX_CALL_HOST=127.0.0.1
ECHOX_CALL_PORT=8000
ECHOX_CALL_CONSOLE_HOST=127.0.0.1
ECHOX_CALL_CONSOLE_PORT=8001
ECHOX_CALL_KILL_PORTS=1
ECHOX_CALL_WORKER_COUNT=1
ECHOX_CALL_WORKER_ID_PREFIX=postcall-worker
CONSOLE_USERS_CONFIG_PATH=config/console_users.yaml
```

说明：

- `ECHOX_CALL_HOST`：API 监听地址，`127.0.0.1` 表示只允许本机访问。
- `ECHOX_CALL_PORT`：API 监听端口，默认 `8000`。
- `ECHOX_CALL_CONSOLE_HOST`：控制台监听地址。
- `ECHOX_CALL_CONSOLE_PORT`：控制台监听端口，默认 `8001`。
- `ECHOX_CALL_KILL_PORTS=1`：启动 API / 控制台前自动清理对应端口上的监听进程。
- `ECHOX_CALL_KILL_PORTS=0`：不自动清理端口，端口被占用时直接报错。
- `ECHOX_CALL_WORKER_COUNT`：`./start.sh worker` 启动的 worker 数量，默认 `1`。
- `ECHOX_CALL_WORKER_ID_PREFIX`：多个 worker 自动生成 `worker-id` 时使用的前缀。
- `CONSOLE_USERS_CONFIG_PATH`：控制台登录用户配置文件路径，默认 `config/console_users.yaml`。

临时改端口也可以直接在命令前传环境变量：

```bash
ECHOX_CALL_PORT=9000 ./start.sh api
ECHOX_CALL_CONSOLE_PORT=9001 ./start.sh console
```

## 常用命令

```bash
./start.sh --help
./start.sh ping
./start.sh migrate
./start.sh api
./start.sh worker
./start.sh console
```

## Docker CPU 部署

项目提供 CPU 版 Docker Compose 部署文件：

```bash
cp .env.docker.example .env.docker
docker compose --env-file .env.docker build api
docker compose --env-file .env.docker run --rm db-init
docker compose --env-file .env.docker run --rm migrate
docker compose --env-file .env.docker up -d api worker console
```

详细步骤见 `docs/docker-cpu-deployment.md`。
