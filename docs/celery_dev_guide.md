# Celery 开发与部署指南（V1.5）

> 起步时间：2026-06-11（V1.5 S0 阶段）
> 配套代码：[app/tasks/celery_app.py](../app/tasks/celery_app.py) · [app/tasks/ping.py](../app/tasks/ping.py)
> 配套需求：[V1.5 PRD §3.4](TyAgent%20V1.5%20%C2%B7%20%E9%9C%80%E6%B1%82%E8%A7%84%E6%A0%BC%E8%AF%B4%E6%98%8E%E4%B9%A6.md) TASK-01 ~ TASK-05

---

## 1. 角色与组件

```
┌───────────────┐     enqueue     ┌──────────────┐    consume    ┌──────────────┐
│ FastAPI app   │ ──────────────▶ │   Redis 7    │ ─────────────▶│ Celery Worker │
│ (HTTP 接入)    │                 │ broker+result │               │  (Python 进程) │
└───────────────┘                 └──────────────┘               └──────────────┘
                                          ▲                              │
                                          │     poll result              │
                                          └──────────────────────────────┘
```

- **生产者**：FastAPI 进程，调 `task.delay()` 把任务写进 Redis
- **broker / backend**：同一个 Redis 实例（broker = 任务队列；backend = 结果存储）
- **消费者**：独立的 Python 进程，由 `celery worker` 命令拉起；专门跑 `@celery_app.task` 标注的函数

> 三者**完全解耦**：worker 挂了不影响 FastAPI 收请求，只是入库任务会堆积在 Redis 里；FastAPI 挂了 worker 也能继续消化在途任务。

---

## 2. 环境准备

### 2.1 Redis 启动

仓库已在 [docker-compose/docker-compose.yml](../docker-compose/docker-compose.yml) 加好 `redis:7-alpine` 服务，跟 Milvus / Neo4j 一起：

```bash
cd docker-compose
docker compose up -d redis      # 仅起 redis
# 或 docker compose up -d        # Milvus + Neo4j + Redis 全起
```

健康检查：

```bash
docker exec -it tyagent-redis redis-cli ping
# → PONG
```

数据持久化挂到 `d:/dockerVolumes/redis/data`（AOF 模式，断电恢复 ≤1s 数据丢失）。

### 2.2 配置项（.env）

```ini
REDIS_URL=redis://127.0.0.1:6379/0
# 可选覆盖：broker / backend 缺省都复用 REDIS_URL
# CELERY_BROKER_URL=redis://127.0.0.1:6379/0
# CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/1
```

> ⚠️ **Windows 必须用 `127.0.0.1` 不要用 `localhost`**。Windows 解析 `localhost` 优先返回 IPv6 `::1`，
> Docker Desktop 的 vpnkit 对 IPv6→容器 的转发常丢应用层包，表现为：TCP 握手成功 →
> 客户端发 PING → 永远等不到 PONG → "Timeout reading from socket"。worker 启动卡在
> `[tasks]` 段下不动也是同一原因。项目默认值已强制 `127.0.0.1`，不要改回 `localhost`。

---

## 3. 启动 Worker

### 3.1 Windows 开发态（`--pool=solo`）

```bash
conda activate geo_agent
celery -A app.tasks.celery_app worker --pool=solo -l info
```

**为什么必须 `--pool=solo`**：

Celery 默认 prefork 池在 Windows 上有兼容性问题（依赖 fork()，Windows 没有真正的 fork），常见症状：
- `ValueError: not enough values to unpack`
- worker 起来后第一个任务就崩溃

`--pool=solo` 让单进程串行处理任务，**开发自测够用**（不并发），无 fork 依赖。

### 3.2 备选：Windows 多并发用 `--pool=threads`

```bash
celery -A app.tasks.celery_app worker --pool=threads -c 4 -l info
```

适用：任务以 I/O 为主（embedding / DB 读写），不受 GIL 限制时。V1.5 的入库任务 80% 时间都是远程 embedding 调用，threads 池 OK；但解析 / 切片是 CPU 密集 → 多线程加速有限。**生产建议直接走 Linux。**

### 3.3 Linux 生产部署（`--pool=prefork`，推荐）

```bash
celery -A app.tasks.celery_app worker --pool=prefork -c 4 -l info
```

- `-c 4`：4 个 worker 子进程，按 CPU 核数定
- 配合 systemd / supervisor 守护进程
- 真生产建议加 `--max-tasks-per-child=200` 防内存泄漏累积

---

## 4. 验证链路（S0 验收 smoke）

启动 worker 后，开另一个 shell 跑：

```bash
conda activate geo_agent
python -c "
from app.tasks import ping_task
res = ping_task.delay('hello from S0')
print('result =', res.get(timeout=5))
"
```

期望输出：

```
result = pong: hello from S0 @ <你的主机名>
```

worker 终端会看到：

```
[INFO/MainProcess] Task app.tasks.ping.ping_task[xxxx-xxxx] received
[INFO/...] ping_task 收到 message='hello from S0' 回 reply='pong: hello from S0 @ ...'
[INFO/MainProcess] Task app.tasks.ping.ping_task[...] succeeded in 0.001s: 'pong: ...'
```

---

## 5. 常见问题排查

### 5.1 worker 起不来 / 报 `kombu.exceptions.OperationalError: [Errno 111] Connection refused`

→ Redis 没启动。`docker ps | grep redis` 看容器在不在；或 `REDIS_URL` 配错。

### 5.1b worker 日志只打到 `[tasks]` 段就卡死 / 客户端 `Timeout reading from socket`

→ 99% 是 **Windows + Docker Desktop 的 IPv6 转发坑**。`Test-NetConnection localhost -Port 6379`
显示 `RemoteAddress : ::1` + `TcpTestSucceeded : True` 但 `redis-cli` 永远没响应 = 命中此坑。
→ **修法**：`.env` 里 `REDIS_URL` 把 `localhost` 改成 `127.0.0.1`。项目默认值已是 `127.0.0.1`。

### 5.2 worker 起来了，但任务一直 PENDING

→ 通常是任务模块没被 worker import。检查：
1. 任务文件是否在 `app/tasks/celery_app.py` 的 `_TASK_MODULES` 列表里
2. 启动命令的 `-A app.tasks.celery_app` 路径正确
3. worker 启动日志里有没有打印出 `Loading task module 'app.tasks.xxx'`

### 5.3 Windows 下 worker 启动后立刻崩溃

→ 几乎肯定是 pool 没改 solo。重启加上 `--pool=solo`。

### 5.4 任务执行成功但 `result.get()` 拿不到

→ result backend 没配或不可达。检查 `CELERY_RESULT_BACKEND` / `REDIS_URL`。
→ 单测里：通过 `celery_app.conf.task_always_eager = True` 让任务同步在 caller 里跑（[tests/test_celery_app.py](../tests/test_celery_app.py) 用的就是这招）。

### 5.5 任务超时被强杀

→ 默认 `task_time_limit=1800` 秒（30 分钟）。文件入库超过这个时间是异常，先看日志。
→ 临时调大：在任务装饰器加 `@celery_app.task(time_limit=3600)` 覆盖。

---

## 6. 后续阶段路线

| 阶段 | 新增任务模块 | 触发场景 |
|---|---|---|
| S3 | `app/tasks/ingest_task.py::parse_and_ingest_task` | 文件上传后异步解析入库 |
| S4 | `app/tasks/session_task.py::generate_session_title_task` | 首轮 AI 回复后自动取标题 |
| S4 | `app/tasks/session_task.py::generate_session_summary_task` | `/summarize` 接口主动触发 |

新任务模块**必须在 `_TASK_MODULES` 注册**，否则 worker 不会发现它。

---

## 7. 测试自动化（CI 友好）

CI 环境**不起真 Redis**，所有 Celery 单测走 `task_always_eager` 同步执行：

```python
@pytest.fixture
def eager_celery():
    from app.tasks import celery_app
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    yield celery_app
    # cleanup ...
```

参考实现：[tests/test_celery_app.py](../tests/test_celery_app.py)。

需要真 Redis / Worker 的端到端验收脚本，按 CLAUDE.md 约定**由用户手动跑**，Claude 只给命令。
