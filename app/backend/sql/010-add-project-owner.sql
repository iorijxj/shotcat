-- 010 项目所有权：给 projects 加 owner_id（关联 users.id），用于跨用户越权隔离。
-- users 表本身是新表，由 init_db.py 的 create_all 自动建出，这里只处理
-- 对已存在的 projects 表做 ALTER（create_all 不会给已有表加列）。
-- owner_id 先建为可空列，避免对存量行报错；上线后需手动回填存量项目的归属，
-- 见 docs/99_归档/应急上线安全方案.md 或部署记录。
-- 幂等：可重复执行（mysql-init-sql 每次启动全量重跑）。

SET @has := (SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='projects' AND COLUMN_NAME='owner_id');
SET @s := IF(@has=0, "ALTER TABLE projects ADD COLUMN owner_id VARCHAR(64) NULL COMMENT '所属用户ID', ADD INDEX ix_projects_owner_id (owner_id)", 'SELECT 1');
PREPARE st FROM @s; EXECUTE st; DEALLOCATE PREPARE st;
