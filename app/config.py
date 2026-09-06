from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///./nwc.db"
    allowed_origins: str = "http://localhost:3000,http://localhost:3001,http://localhost:3002,https://crm.newworldcourtage.fr,https://www.newworldcourtage.fr"
    secret_key: str = "change-me-in-production"
    base_url: str = "http://localhost:8000"

    @property
    def origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",")]

    class Config:
        env_file = ".env"


settings = Settings()
