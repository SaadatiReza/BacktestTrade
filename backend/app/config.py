from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    twelve_data_api_key: str = ""
    database_url: str = "sqlite:///./sp2l.db"


settings = Settings()
