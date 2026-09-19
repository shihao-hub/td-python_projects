-- ============================================================
-- 01_create_db.sql —— 建库（先杀掉旧连接，删库重建，可重复执行）
-- 由 setup.ps1 调用：psql -d postgres -f 01_create_db.sql
-- ============================================================

-- 若有残留连接，先断开，否则 DROP DATABASE 会报 "database is being accessed"
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname = 'learn_pg' AND pid <> pg_backend_pid();

DROP DATABASE IF EXISTS learn_pg;

-- UTF8 编码保证中文正常存储
CREATE DATABASE learn_pg ENCODING 'UTF8';
