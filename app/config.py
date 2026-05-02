from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    api_host: str = Field(default="0.0.0.0", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")
    api_base_url: str = Field(default="http://localhost:8000", alias="API_BASE_URL")

    llm_base_url: str = Field(default="https://open.bigmodel.cn/api/paas/v4", alias="LLM_BASE_URL")
    llm_model_main: str = Field(default="glm-4.5-air", alias="LLM_MODEL_MAIN")
    llm_api_key: str = Field(default="", alias="LLM_API_KEY")

    embedding_base_url: str = Field(default="https://open.bigmodel.cn/api/paas/v4", alias="EMBEDDING_BASE_URL")
    embedding_model: str = Field(default="BAAI/bge-small-zh-v1.5", alias="EMBEDDING_MODEL")
    embedding_api_key: str = Field(default="", alias="EMBEDDING_API_KEY")
    embedding_device: str = Field(default="cpu", alias="EMBEDDING_DEVICE")
    embedding_local_dir: str = Field(default=str(DATA_DIR / "models" / "bge-small-zh-v1.5"), alias="EMBEDDING_LOCAL_DIR")

    chroma_host: str = Field(default="localhost", alias="CHROMA_HOST")
    chroma_port: int = Field(default=8000, alias="CHROMA_PORT")
    chroma_collection: str = Field(default="leetcode_rag_v1", alias="CHROMA_COLLECTION")

    langsmith_api_key: str = Field(default="", alias="LANGSMITH_API_KEY")
    langsmith_project: str = Field(default="leetcode-rag-agent", alias="LANGSMITH_PROJECT")
    langsmith_endpoint: str = Field(default="https://api.smith.langchain.com", alias="LANGSMITH_ENDPOINT")
    langsmith_tracing: bool = Field(default=True, alias="LANGSMITH_TRACING")

    model_input_cost_per_1m: float = Field(default=0.8, alias="MODEL_INPUT_COST_PER_1M")
    model_output_cost_per_1m: float = Field(default=2.0, alias="MODEL_OUTPUT_COST_PER_1M")

    streamlit_server_port: int = Field(default=8501, alias="STREAMLIT_SERVER_PORT")

    email_send_enabled: bool = Field(default=False, alias="EMAIL_SEND_ENABLED")
    smtp_host: str = Field(default="smtp.163.com", alias="SMTP_HOST")
    smtp_port: int = Field(default=465, alias="SMTP_PORT")
    smtp_username: str = Field(default="", alias="SMTP_USERNAME")
    smtp_password: str = Field(default="", alias="SMTP_PASSWORD")
    smtp_from: str = Field(default="", alias="SMTP_FROM")

    @property
    def langsmith_enabled(self) -> bool:
        return bool(self.langsmith_api_key and self.langsmith_tracing)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
