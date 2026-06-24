# 数据库初始化指南

> 适用范围：任何时候创建新库（开发 / 测试 / 部署 / 迁移到新机器），都走这一套。
>
> 单一真相源：[alembic/versions/](alembic/versions/) 下的迁移脚本。`baseline_v2` 在空库会走 `Base.metadata.create_all`，在旧 V1.5 库会走补列分支——单脚本同时支持两条路径。

## 1. 标准流程（三步走）

```powershell
# 1. 在 docker-compose 起的 postgres 里建库
docker exec tyagent-postgres psql -U postgres -c "CREATE DATABASE <库名>;"

# 2. 切环境变量指向新库（修改 .env 的 DATABASE_URL，或临时 $env:DATABASE_URL=...）
$env:DATABASE_URL="postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/<库名>"

# 3. 跑全量迁移（空库会自动走 create_all 分支建全部表）
alembic upgrade head
```

执行完会看到所有业务表 + `alembic_version` 表（记录最新 revision）。

## 2. 验证

```powershell
docker exec tyagent-postgres psql -U postgres -d <库名> -c "\dt"
docker exec tyagent-postgres psql -U postgres -d <库名> -c "SELECT version_num FROM alembic_version;"
```

应能看到所有表 + `alembic_version` 表里有一条最新 revision id。

## 3. 旧库（已经有数据但没 alembic_version）怎么纳入管理？

**绝大多数情况直接跑 `alembic upgrade head` 就行**——`baseline_v2` 会检测到 `kb_files` 已存在，走补列分支只加 V2.0 新增列，不动旧数据。

完成后 `alembic_version` 表自动出现并记录最新 revision。

只有当你**确认当前 schema 已经完全等于最新 baseline + 后续所有迁移**（很少见，比如手工同步过 schema 但没装 alembic）才用：

```powershell
alembic stamp head
```

只插入 `alembic_version` 表，不动 SQL。

## 4. 各场景对照表

| 场景 | 做法 |
|---|---|
| 新机器初始化 / 新人接手 | 拉代码 → `cp .env.example .env` → docker-compose 起服务 → `alembic upgrade head` |
| 新建测试库 / 新建开发库 | `CREATE DATABASE` → `alembic upgrade head` |
| 旧 V1.5 库升级到 V2.0 | 直接 `alembic upgrade head`（baseline_v2 会自动走补列分支） |
| 加新表 / 改列 | 改 `app/models/` → `alembic revision --autogenerate -m "<slug>"` → 人工 review → `alembic upgrade head` |
| 回退一步 | `alembic downgrade -1`（注意 baseline_v2 的 downgrade 不支持回到「空库」状态） |
| 切到指定版本 | `alembic upgrade <rev_id>` 或 `alembic downgrade <rev_id>` |
| 临时调试，看历史 | `alembic history --verbose` / `alembic current` |

## 5. 当前库现状（2026-06-24）

| 库名 | 用途 | 说明 |
|---|---|---|
| `tyagent` | 旧开发库（保留） | 上线前会切回它 |
| `tyagent_test` | 当前开发 + 集成测试库 | `.env` 的 `DATABASE_URL` 和 `TEST_DATABASE_URL` 都指向它 |

`tyagent_test` 在 2026-06-24 经过 DROP + CREATE 重建 + `alembic upgrade head` 后纳入 alembic 管理。

## 6. baseline_v2 的双分支设计

[alembic/versions/20260622_b74589b68f7d_baseline_v2.py](alembic/versions/20260622_b74589b68f7d_baseline_v2.py) 在 `upgrade()` 里先 `inspect()` 检查 `kb_files` 表是否存在：

- **不存在（空库）** → import 所有 ORM 模型，跑 `Base.metadata.create_all()` 一次性建全部表（含 V2.0 新增列）
- **存在（旧 V1.5 库）** → 走 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` 补列

后续迁移（如 `p1_9_kb_deletion_compensation_status`）所有 DDL 都加 `IF NOT EXISTS`，确保两条路径都幂等。

**为什么这么设计**：项目升级期没有引入 alembic，V1.5 库是靠 `app/main.py` 的 `create_all` 建的，无法溯源精确 V1.5 schema。如果硬要写「V1.5 baseline + V2.0 升级补丁」两条独立迁移，无法保证旧库迁移路径与「空库 → 跑两条迁移」完全一致。双分支单脚本是当前最干净的折中。

## 7. 常见踩坑

1. **`alembic upgrade head` 报 `relation xxx already exists`** —— 说明库里已有 V2.0 时代的表但没 `alembic_version`。直接 `alembic stamp head` 即可（schema 已经完整）。
2. **`alembic upgrade head` 报 `relation kb_files does not exist`** —— 旧版 baseline 脚本的 bug（已修），更新代码后重跑。
3. **`alembic.ini` 里中文注释报 UnicodeDecodeError** —— `alembic.ini` 必须纯 ASCII（Windows configparser 走 GBK locale），中文放 `alembic/env.py` 或 README。
4. **Windows 上连不上 PG** —— 用 `127.0.0.1` 不要用 `localhost`（IPv6 vpnkit 转发坑）。
5. **drop 时报"数据库正被使用"** —— 加 `WITH (FORCE)`，或先把 app / pgAdmin / DBeaver 全断开。
6. **`DATABASE_URL` 缺 `+asyncpg`** —— alembic env.py 走 async engine，必须带 `postgresql+asyncpg://`。

## 8. pytest fixture 的特殊情况

[tests/conftest.py](tests/conftest.py) 的 `pg_engine` / `pg_client` fixture 在 setup 阶段会执行 `Base.metadata.drop_all + create_all`，**绕过 alembic 直接重建表结构**。这是为了：

- 集成测试需要每次干净的状态
- pytest 跑得快（`create_all` 比 alembic 迁移快得多）

代价：fixture 建出来的表**没有 `alembic_version` 表**。这对测试 OK（测试结束就清），但**不要拿测试库当开发库长期用**——一旦想加新表 / 改列，alembic 看不出当前在哪个版本。

当前 `tyagent_test` 既做测试又做开发，所以我们手动 `alembic upgrade head` 把版本号 stamp 上了。pytest fixture 后续的 `drop_all + create_all` **不会删除 `alembic_version` 表**（它不在 `Base.metadata` 里），所以版本管理一直在线。
