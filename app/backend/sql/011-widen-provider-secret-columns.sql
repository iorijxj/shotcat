-- 011 providers.api_key/api_secret 从 VARCHAR(4096) 改 TEXT，给 Fernet 加密后的密文腾出空间
-- （加密后的 token 比明文长，VARCHAR(4096) 在极端情况下可能不够用）。
-- 只改列类型，不改列语义；配合 app/cli/encrypt_existing_provider_secrets.py 把存量明文转成密文。
-- 幂等：可重复执行（mysql-init-sql 每次启动全量重跑）。

SET @dtype := (SELECT DATA_TYPE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='providers' AND COLUMN_NAME='api_key');
SET @s := IF(@dtype IS NOT NULL AND @dtype <> 'text', "ALTER TABLE providers MODIFY COLUMN api_key TEXT NOT NULL COMMENT 'API Key（敏感，加密存储）'", 'SELECT 1');
PREPARE st FROM @s; EXECUTE st; DEALLOCATE PREPARE st;

SET @dtype2 := (SELECT DATA_TYPE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='providers' AND COLUMN_NAME='api_secret');
SET @s := IF(@dtype2 IS NOT NULL AND @dtype2 <> 'text', "ALTER TABLE providers MODIFY COLUMN api_secret TEXT NOT NULL COMMENT 'API Secret（敏感，加密存储）'", 'SELECT 1');
PREPARE st FROM @s; EXECUTE st; DEALLOCATE PREPARE st;
