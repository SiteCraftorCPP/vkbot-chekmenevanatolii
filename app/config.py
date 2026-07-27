from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    vk_bot_token: str
    vk_group_id: int
    admin_ids_raw: str = Field(default="", validation_alias="ADMIN_IDS")
    database_url: str
    deepseek_api_key: str
    deepseek_model: str = "deepseek-chat"
    deepseek_base_url: str = "https://api.deepseek.com"
    ocr_lang: str = "ru"

    @property
    def admin_ids(self) -> frozenset[int]:
        return frozenset(
            int(item.strip()) for item in self.admin_ids_raw.split(",") if item.strip()
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
