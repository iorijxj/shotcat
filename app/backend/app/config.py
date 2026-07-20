"""应用配置，从环境变量加载。"""

import json
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = BACKEND_ROOT / ".env"


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

    # 登录防暴力破解（安全整改阶段三 3.1）：同一用户名/IP 在锁定窗口内连续失败
    # 达到阈值即锁定 login_lockout_seconds。IP 阈值故意更高：经 Caddy 反代时
    # client IP 退化为网关地址，IP 维度等效全局兜底，阈值太低会误锁所有人。
    login_max_failures_per_user: int = 5
    login_max_failures_per_ip: int = 30
    login_lockout_seconds: int = 900

    # 文件上传大小上限（安全整改阶段三 3.2），按类型区分，单位 MB。
    # 仅约束用户手动上传（/studio/files/upload）；AI 生成结果落库走内部下载链路，不受此限。
    upload_max_image_mb: int = 2
    upload_max_video_mb: int = 5

    # SSRF 防护开关（安全整改阶段三 3.3）：默认拦截指向内网/本机的下载地址。
    # 仅本地开发用 localhost mock 供应商时才可临时置 true，任何部署环境保持 false。
    ssrf_allow_private_targets: bool = False

    # AI 生成类接口限流（安全整改阶段三 3.4）：每登录用户每分钟允许的生成请求数，
    # 0 表示关闭。清单见 app/core/rate_limit.py。
    generation_rate_limit_per_minute: int = 10

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
    # 可选：对外访问基址（CDN 或自定义域名），为空则使用 S3 自带 URL 或预签名 URL
    s3_public_base_url: str | None = None

    def model_post_init(self, __context: object) -> None:
        if not self.celery_broker_url or not str(self.celery_broker_url).strip():
            password_part = f":{self.redis_password}@" if self.redis_password else ""
            self.celery_broker_url = f"redis://{password_part}{self.redis_host}:{self.redis_port}/{self.redis_db}"


settings = Settings()
