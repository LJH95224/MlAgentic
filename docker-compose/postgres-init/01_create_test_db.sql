-- ============================================================
-- PostgreSQL 容器首次启动时执行（postgres:17-alpine 官方约定：
-- /docker-entrypoint-initdb.d 下的 .sql / .sh 在 initdb 完成后按字典序执行一次）
--
-- 作用：建一个独立的 tyagent_test 库专供 pytest 集成测试用
--   - tyagent      → 开发态业务库（POSTGRES_DB 自动建好）
--   - tyagent_test → 集成测试库（每次跑 pytest 都 drop_all+create_all，
--                    数据会被清空，所以禁止往这里塞业务数据）
--
-- 重建容器（删了卷再起）才会重新执行；改这个文件后已有数据库不会被影响。
-- ============================================================

CREATE DATABASE tyagent_test;
GRANT ALL PRIVILEGES ON DATABASE tyagent_test TO postgres;
