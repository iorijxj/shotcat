"""应用配置，从环境变量加载。"""

import json
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = BACKEND_ROOT / ".env"

# 随仓库下发的默认值 + 常见弱口令（精确匹配，大小写不敏感）。含 "change-me" 词根的
# 占位符（如 change-me-to-a-random-secret）由子串规则统一命中，不必逐一列举。
_WEAK_SECRET_EXACT = frozenset(
    {"rustfsadmin", "changeme", "password", "admin", "root", "secret", "123456", "test"}
)


def _is_weak_secret(value: str | None) -> bool:
    if not value:
        return False
    lowered = value.strip().lower()
    return "change-me" in lowered or lowered in _WEAK_SECRET_EXACT


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # App
    app_name: str = "Jellyfish API"
    debug: bool = False

    # API
    api_v1_prefix: str = "/api/v1"

    # Database
    database_url: str = "sqlite+aiosqlite:///./jellyfish.db"

    # Redis / Celery Broker
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str | None = None
    celery_broker_url: str | None = None

    # CORS：环境变量中建议使用逗号分隔（更贴近 docker-compose 用法）
    # 也兼容 JSON 数组：'["http://a","http://b"]'
    cors_origins: str = "http://localhost:7788,http://127.0.0.1:7788"

    # 鉴权：JWT 签名密钥，必须由环境变量提供，缺失时启动即报错（不给默认值，避免弱密钥）
    auth_jwt_secret: str
    auth_jwt_expire_minutes: int = 43200  # 30 天，内部工具场景无需频繁重登

    # LLM Provider api_key/api_secret 静态加密密钥（Fernet），必须由环境变量提供
    provider_secret_enc_key: str

    # 弱口令启动校验（公众化 M1）：安全默认为 False，检测到随仓库下发的默认/弱口令
    # （DB 口令 / S3 键 / Redis 口令 / JWT 密钥 / Provider 加密密钥）即拒绝启动。
    # 本机防火墙内开发对接本地 docker 基础设施时用 ALLOW_WEAK_SECRETS=true 显式豁免。
    allow_weak_secrets: bool = False

    # 登录防暴力破解（安全整改阶段三 3.1）：同一用户名/IP 在锁定窗口内连续失败
    # 达到阈值即锁定 login_lockout_seconds。IP 阈值故意更高：经 Caddy 反代时
    # client IP 退化为网关地址，IP 维度等效全局兜底，阈值太低会误锁所有人。
    # 公网重审（M1）：username 维度是精确的（不受反代退化影响），5 次/15 分足以
    # 挡定向撞库、又不至于误锁正常用户，维持不变；IP 维度因反代退化保持 30 的宽松
    # 兜底，收紧只会连坐同网关下的所有人，故不动。请求洪泛另由 login_rate_limit 挡。
    login_max_failures_per_user: int = 5
    login_max_failures_per_ip: int = 30
    login_lockout_seconds: int = 900

    # 文件上传大小上限（安全整改阶段三 3.2），按类型区分，单位 MB。
    # 仅约束用户手动上传（/studio/files/upload）；AI 生成结果落库走内部下载链路，不受此限。
    # 公网重审（M1）：原 2M/5M 面向局域网内部素材，真实短剧的参考图/样片明显偏小，
    # 上调到 10M 图 / 200M 视频以覆盖常见制作素材；仍保留硬上限防单请求撑爆存储，
    # 面向公众的总量滥用由后续 M3 的按租户配额兜底。
    upload_max_image_mb: int = 10
    upload_max_video_mb: int = 200

    # SSRF 防护开关（安全整改阶段三 3.3）：默认拦截指向内网/本机的下载地址。
    # 仅本地开发用 localhost mock 供应商时才可临时置 true，任何部署环境保持 false。
    # 公网重审（M1）：ssrf_guard 采用「只放行 is_global 公网 IP」的默认拒绝策略，
    # 已覆盖私有网段/回环/链路本地(含云元数据 169.254)/保留/CGNAT，公网强度足够，
    # 无需再维护易漏的黑名单；默认 False 维持不变。
    ssrf_allow_private_targets: bool = False

    # AI 生成类接口限流（安全整改阶段三 3.4）：每登录用户每分钟允许的生成请求数，
    # 0 表示关闭。清单见 app/core/rate_limit.py。
    generation_rate_limit_per_minute: int = 10

    # 登录接口限流（公众化 M1）：每来源 IP 每分钟允许的 /auth/login 请求数，0 关闭。
    # 与 login_max_failures_*（按失败次数锁定、含 username 维度）互补——此项挡请求洪泛，
    # 那项挡定向撞库。注意经 Caddy 反代时 client IP 退化为网关地址，此限流近似全局
    # 洪泛上限而非严格 per-IP，故默认给得较宽，按真实并发登录量调整。
    login_rate_limit_per_minute: int = 60

    @property
    def cors_origins_list(self) -> list[str]:
        s = (self.cors_origins or "").strip()
        if not s:
            return []
        if s.startswith("["):
            loaded = json.loads(s)
            if isinstance(loaded, list):
                return [str(x).strip() for x in loaded if str(x).strip()]
            return []
        return [x.strip() for x in s.split(",") if x.strip()]

    # S3 / 对象存储（用于素材文件）
    s3_endpoint_url: str | None = None
    s3_region_name: str | None = None
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    s3_bucket_name: str | None = None
    # 可选：统一前缀，方便按环境/项目隔离，如 "jellyfish/dev"
    s3_base_path: str = ""
    # 收窄为 dev-only：生产 S3 bucket 已私有化（M4 对象存储访问加固），不应再配置本项
    # 对外暴露 bucket URL——资产一律走后端代理 /api/v1/studio/files/{id}/download。
    s3_public_base_url: str | None = None

    def _assert_cors_safe(self) -> None:
        """CORS 启动期 fail-fast：main.py 固定 allow_credentials=True，此时 origins
        含通配 * 既被浏览器拒发凭证、又等同放开全网，必须直接拒绝而非静默降级。
        真实域名请逐个列入 CORS_ORIGINS（逗号分隔或 JSON 数组）。"""
        if "*" in self.cors_origins_list:
            raise ValueError(
                "CORS_ORIGINS 不允许通配 '*'（凭证模式下无效且等同放开全网）；"
                "请逐个列出真实前端域名，如 https://app.example.com"
            )

    def _assert_no_weak_secrets(self) -> None:
        """弱口令启动期 fail-fast：检测后端实际拿到的敏感凭证是否仍是仓库默认/弱口令。
        安全默认为 False；本机防火墙内开发用 ALLOW_WEAK_SECRETS=true 显式豁免。"""
        if self.allow_weak_secrets:
            return
        from urllib.parse import urlparse

        db_password = urlparse(self.database_url).password
        candidates = {
            "DATABASE_URL（数据库口令）": db_password,
            "S3_ACCESS_KEY_ID": self.s3_access_key_id,
            "S3_SECRET_ACCESS_KEY": self.s3_secret_access_key,
            "REDIS_PASSWORD": self.redis_password,
            "AUTH_JWT_SECRET（JWT 密钥）": self.auth_jwt_secret,
            "PROVIDER_SECRET_ENC_KEY": self.provider_secret_enc_key,
        }
        weak = [name for name, value in candidates.items() if _is_weak_secret(value)]
        if weak:
            raise ValueError(
                "检测到默认/弱口令，拒绝启动（公众化 M1）: " + "、".join(weak) + "。"
                "请改为随机强口令；仅本机防火墙内开发对接本地基础设施时"
                "可设 ALLOW_WEAK_SECRETS=true 显式豁免。"
            )

    def model_post_init(self, __context: object) -> None:
        if not self.celery_broker_url or not str(self.celery_broker_url).strip():
            password_part = f":{self.redis_password}@" if self.redis_password else ""
            self.celery_broker_url = f"redis://{password_part}{self.redis_host}:{self.redis_port}/{self.redis_db}"
        self._assert_cors_safe()
        self._assert_no_weak_secrets()


settings = Settings()
