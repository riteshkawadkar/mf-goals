from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    database_url: str = "postgresql://localhost/goal_tracker"
    secret_key: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 10080  # 7 days
    anthropic_api_key: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    from_email: str = "noreply@goaltracker.app"
    frontend_origin: str = "http://localhost:3000"
    environment: str = "development"
    otp_valid_seconds: int = 600  # 10 minutes

    class Config:
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings()
