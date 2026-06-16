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
    cors_allow_origins: str = Field(
        default="http://localhost:8511,http://127.0.0.1:8511,http://localhost:8501,http://127.0.0.1:8501",
        alias="CORS_ALLOW_ORIGINS",
    )

    llm_base_url: str = Field(default="https://open.bigmodel.cn/api/paas/v4", alias="LLM_BASE_URL")
    llm_model_main: str = Field(default="glm-4.5-air", alias="LLM_MODEL_MAIN")
    llm_model_router: str = Field(default="glm-4.5-air", alias="LLM_MODEL_ROUTER")
    llm_api_key: str = Field(default="", alias="LLM_API_KEY")

    embedding_base_url: str = Field(default="https://open.bigmodel.cn/api/paas/v4", alias="EMBEDDING_BASE_URL")
    embedding_model: str = Field(default="BAAI/bge-m3", alias="EMBEDDING_MODEL")
    embedding_api_key: str = Field(default="", alias="EMBEDDING_API_KEY")
    embedding_device: str = Field(default="cpu", alias="EMBEDDING_DEVICE")
    embedding_local_dir: str = Field(default="/app/external-models/bge-m3", alias="EMBEDDING_LOCAL_DIR")
    enterprise_embedding_model: str = Field(default="BAAI/bge-m3", alias="ENTERPRISE_EMBEDDING_MODEL")
    enterprise_embedding_local_dir: str = Field(default="/app/external-models/bge-m3", alias="ENTERPRISE_EMBEDDING_LOCAL_DIR")
    enterprise_reranker_model: str = Field(default="BAAI/bge-reranker-v2-m3", alias="ENTERPRISE_RERANKER_MODEL")
    enterprise_reranker_local_dir: str = Field(default="/app/external-models/bge-reranker-v2-m3", alias="ENTERPRISE_RERANKER_LOCAL_DIR")
    enterprise_reranker_soft_budget_seconds: float = Field(default=15.0, alias="ENTERPRISE_RERANKER_SOFT_BUDGET_SECONDS")

    chroma_host: str = Field(default="localhost", alias="CHROMA_HOST")
    chroma_port: int = Field(default=8000, alias="CHROMA_PORT")
    chroma_collection: str = Field(default="leetcode_rag_bge_m3_v1", alias="CHROMA_COLLECTION")
    conversation_memory_chroma_collection: str = Field(
        default="conversation_memory_bge_m3_v1",
        alias="CONVERSATION_MEMORY_CHROMA_COLLECTION",
    )
    conversation_memory_collection_version: str = Field(
        default="bge-m3-1024-v1",
        alias="CONVERSATION_MEMORY_COLLECTION_VERSION",
    )
    conversation_memory_embedding_dimension: int = Field(
        default=1024,
        alias="CONVERSATION_MEMORY_EMBEDDING_DIMENSION",
    )
    enterprise_chroma_collection: str = Field(default="enterprise_rag_bench_v2", alias="ENTERPRISE_CHROMA_COLLECTION")
    workspace_memory_chroma_collection: str = Field(default="workspace_memory_v1", alias="WORKSPACE_MEMORY_CHROMA_COLLECTION")
    enterprise_collection_version: str = Field(default="enterprise_rag_bench_v2", alias="ENTERPRISE_COLLECTION_VERSION")

    langsmith_api_key: str = Field(default="", alias="LANGSMITH_API_KEY")
    langsmith_project: str = Field(default="leetcode-rag-agent", alias="LANGSMITH_PROJECT")
    langsmith_endpoint: str = Field(default="https://api.smith.langchain.com", alias="LANGSMITH_ENDPOINT")
    langsmith_tracing: bool = Field(default=False, alias="LANGSMITH_TRACING")

    model_input_cost_per_1m: float = Field(default=0.8, alias="MODEL_INPUT_COST_PER_1M")
    model_output_cost_per_1m: float = Field(default=2.0, alias="MODEL_OUTPUT_COST_PER_1M")

    streamlit_server_port: int = Field(default=8501, alias="STREAMLIT_SERVER_PORT")

    email_send_enabled: bool = Field(default=False, alias="EMAIL_SEND_ENABLED")
    smtp_host: str = Field(default="smtp.163.com", alias="SMTP_HOST")
    smtp_port: int = Field(default=465, alias="SMTP_PORT")
    smtp_username: str = Field(default="", alias="SMTP_USERNAME")
    smtp_password: str = Field(default="", alias="SMTP_PASSWORD")
    smtp_from: str = Field(default="", alias="SMTP_FROM")
    llm_timeout_seconds: float = Field(default=45.0, alias="LLM_TIMEOUT_SECONDS")
    llm_router_timeout_seconds: float = Field(default=12.0, alias="LLM_ROUTER_TIMEOUT_SECONDS")
    llm_router_max_retries: int = Field(default=0, alias="LLM_ROUTER_MAX_RETRIES")
    react_max_steps: int = Field(default=3, alias="REACT_MAX_STEPS")
    react_max_memory_reads: int = Field(default=2, alias="REACT_MAX_MEMORY_READS")
    react_max_tool_failures: int = Field(default=2, alias="REACT_MAX_TOOL_FAILURES")
    react_max_same_tool_retries: int = Field(default=1, alias="REACT_MAX_SAME_TOOL_RETRIES")
    enable_fast_path_router: bool = Field(default=True, alias="ENABLE_FAST_PATH_ROUTER")
    enable_legacy_orchestration_fallback: bool = Field(default=False, alias="ENABLE_LEGACY_ORCHESTRATION_FALLBACK")
    workspace_memory_root: str = Field(default=str(ROOT_DIR / "memory"), alias="WORKSPACE_MEMORY_ROOT")
    transcript_export_path: str = Field(default=str(DATA_DIR / "transcripts" / "session_transcript.jsonl"), alias="TRANSCRIPT_EXPORT_PATH")
    hermes_memory_db_path: str = Field(default=str(DATA_DIR / "hermes_memory.db"), alias="HERMES_MEMORY_DB_PATH")
    privacy_policy_db_path: str = Field(default=str(DATA_DIR / "privacy_policies.db"), alias="PRIVACY_POLICY_DB_PATH")
    hermes_compaction_turn_threshold: int = Field(default=80, alias="HERMES_COMPACTION_TURN_THRESHOLD")
    hermes_compaction_keep_turns: int = Field(default=16, alias="HERMES_COMPACTION_KEEP_TURNS")
    hermes_reflection_tool_call_interval: int = Field(default=8, alias="HERMES_REFLECTION_TOOL_CALL_INTERVAL")
    enterprise_sparse_db_path: str = Field(default=str(DATA_DIR / "enterprise_sparse.db"), alias="ENTERPRISE_SPARSE_DB_PATH")
    enterprise_canonical_dataset_root: str = Field(default="", alias="ENTERPRISE_CANONICAL_DATASET_ROOT")
    workspace_memory_sparse_db_path: str = Field(default=str(DATA_DIR / "workspace_memory_sparse.db"), alias="WORKSPACE_MEMORY_SPARSE_DB_PATH")
    user_model_provider_mode: str = Field(default="local", alias="USER_MODEL_PROVIDER_MODE")
    honcho_base_url: str = Field(default="", alias="HONCHO_BASE_URL")
    honcho_api_key: str = Field(default="", alias="HONCHO_API_KEY")
    imap_enabled: bool = Field(default=False, alias="IMAP_ENABLED")
    imap_host: str = Field(default="imap.163.com", alias="IMAP_HOST")
    imap_port: int = Field(default=993, alias="IMAP_PORT")
    imap_username: str = Field(default="", alias="IMAP_USERNAME")
    imap_password: str = Field(default="", alias="IMAP_PASSWORD")
    imap_mailbox: str = Field(default="INBOX", alias="IMAP_MAILBOX")
    calendar_provider_enabled: bool = Field(default=False, alias="CALENDAR_PROVIDER_ENABLED")
    calendar_provider: str = Field(default="tencent_exmail", alias="CALENDAR_PROVIDER")
    calendar_api_base_url: str = Field(default="", alias="CALENDAR_API_BASE_URL")
    calendar_api_key: str = Field(default="", alias="CALENDAR_API_KEY")
    tencent_meeting_enabled: bool = Field(default=False, alias="TENCENT_MEETING_ENABLED")
    tencent_meeting_provider: str = Field(default="mcp_skill", alias="TENCENT_MEETING_PROVIDER")
    tencent_meeting_mcp_url: str = Field(default="https://mcp.meeting.tencent.com/mcp/wemeet-open/v1", alias="TENCENT_MEETING_MCP_URL")
    tencent_meeting_token: str = Field(default="", alias="TENCENT_MEETING_TOKEN")
    tencent_meeting_skill_version: str = Field(default="1.0.8", alias="TENCENT_MEETING_SKILL_VERSION")
    tencent_meeting_api_base_url: str = Field(default="", alias="TENCENT_MEETING_API_BASE_URL")
    tencent_meeting_app_id: str = Field(default="", alias="TENCENT_MEETING_APP_ID")
    tencent_meeting_secret_id: str = Field(default="", alias="TENCENT_MEETING_SECRET_ID")
    tencent_meeting_secret_key: str = Field(default="", alias="TENCENT_MEETING_SECRET_KEY")

    postgres_host: str = Field(default="postgres", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")
    postgres_db: str = Field(default="dlp_agent", alias="POSTGRES_DB")
    postgres_user: str = Field(default="dlp_agent", alias="POSTGRES_USER")
    postgres_password: str = Field(default="dlp_agent", alias="POSTGRES_PASSWORD")

    redis_url: str = Field(default="redis://redis:6379/0", alias="REDIS_URL")
    celery_broker_url: str = Field(default="redis://redis:6379/0", alias="CELERY_BROKER_URL")
    celery_result_backend: str = Field(default="redis://redis:6379/1", alias="CELERY_RESULT_BACKEND")
    risk_worker_concurrency: int = Field(default=4, alias="RISK_WORKER_CONCURRENCY")
    email_worker_concurrency: int = Field(default=2, alias="EMAIL_WORKER_CONCURRENCY")

    @property
    def langsmith_enabled(self) -> bool:
        return bool(self.langsmith_api_key and self.langsmith_tracing)

    @property
    def postgres_dsn(self) -> str:
        return (
            f"dbname={self.postgres_db} "
            f"user={self.postgres_user} "
            f"password={self.postgres_password} "
            f"host={self.postgres_host} "
            f"port={self.postgres_port}"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
