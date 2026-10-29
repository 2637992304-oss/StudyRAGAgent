import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    database_url: str = (
        "postgresql+psycopg://studyrag:studyrag_dev@localhost:5432/studyrag"
    )
    redis_url: str = "redis://localhost:6379/0"
    storage_dir: Path = Path("./storage")
    backend_cors_origins: str = "http://127.0.0.1:5173,http://localhost:5173"
    auth_secret: str = "change-this-local-development-secret"
    max_upload_mb: int = 25
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    embedding_batch_size: int = Field(default=16, ge=1)
    embedding_threads: int = Field(default=2, ge=1)
    ingestion_pair_headings: bool = False
    ingestion_pdf_layout: bool = False
    ingestion_lease_seconds: int = Field(default=180, ge=3)
    ingestion_recovery_interval: int = Field(default=15, ge=1)
    ingestion_delivery_seconds: int = Field(default=60, ge=1)
    ingestion_max_attempts: int = Field(default=3, ge=1)
    ingestion_max_deliveries: int = Field(default=20, ge=1)
    model_cache_dir: Path = Path("./model_cache")
    demo_password: str = "study-demo-only"
    llm_provider: str = "openai_compatible"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_thinking_mode: Literal["default", "enabled", "disabled"] = "default"
    llm_timeout_seconds: int = 90
    llm_temperature: float = 0.1
    llm_max_output_tokens: int = 1200
    agent_plain_tokens: int = Field(default=2048, ge=1)
    agent_plain_timeout: float = Field(default=90, gt=0)
    agent_thinking_tokens: int = Field(default=8192, ge=1)
    agent_thinking_timeout: float = Field(default=180, gt=0)
    agent_thinking_effort: Literal["low", "high", "max"] = "high"
    transcription_tokens: int = Field(default=4096, ge=1)
    transcription_timeout: float = Field(default=90, gt=0)
    citation_check_tokens: int = Field(default=1024, ge=1)
    citation_check_timeout: float = Field(default=60, gt=0)
    retrieval_timeout: float = Field(default=45, gt=0)
    chat_deadline_seconds: float = Field(default=360, gt=0)
    llm_connect_timeout: float = Field(default=10, gt=0)
    llm_read_timeout: float = Field(default=30, gt=0)
    agent_max_rounds: int = Field(default=4, ge=1, le=8)
    agent_history_messages: int = Field(default=12, ge=2, le=30)
    agent_history_chars: int = Field(default=18000, ge=2000, le=50000)
    web_search_provider: Literal["tavily"] = "tavily"
    tavily_api_key: str = ""
    external_timeout: float = Field(default=20, gt=0, le=60)
    # Admin-only mapping: account UUID -> exact owner/repo -> read-only token.
    github_credentials: dict[str, dict[str, str]] = Field(default_factory=dict)
    retrieval_top_k: int = Field(default=5, ge=1)
    retrieval_max_distance: float = 0.65
    retrieval_max_chars: int = Field(default=12000, ge=1)
    retrieval_strategy: Literal["dense", "keyword", "hybrid", "rerank"] = "dense"
    retrieval_candidates: int = Field(default=30, ge=1)
    retrieval_rerank_candidates: int = Field(default=20, ge=1)
    retrieval_rrf_k: int = Field(default=60, ge=1)
    retrieval_keyword_min_score: float = Field(default=1.0, gt=0)
    retrieval_rerank_min_score: float = 0.0
    retrieval_lexical_max_chunks: int = Field(default=10000, ge=1)
    reranker_model: str = "BAAI/bge-reranker-v2-m3-int8"
    reranker_local_files_only: bool = True
    retrieval_rerank_gate: Literal["legacy", "score", "coverage", "adaptive"] = "legacy"
    retrieval_rerank_strong_score: float = 0.0
    retrieval_rerank_support_distance: float = Field(default=0.3, ge=0, le=2)
    retrieval_rerank_support_keyword: float = Field(default=3.0, gt=0)
    retrieval_deduplicate: bool = True
    reranker_threads: int = Field(default=2, ge=1)
    reranker_batch_size: int = Field(default=4, ge=1)
    reranker_length_bucketing: bool = True
    reranker_passage_chars: int = Field(default=0, ge=0)
    rerank_score_cache: bool = False
    rerank_cache_entries: int = Field(default=2048, ge=1, le=32768)
    rerank_cache_ttl_seconds: float = Field(default=600, gt=0)
    retrieval_pair_headings: bool = False
    retrieval_expand_terms: bool = False
    retrieval_balanced_candidates: bool = False
    retrieval_diversify: bool = False
    retrieval_section_limit: int = Field(default=2, ge=1)
    retrieval_query_normalization: bool = False
    retrieval_facet_coverage: bool = False
    retrieval_section_backfill: bool = False
    retrieval_grounded_answers: bool = False
    retrieval_table_reasoning: bool = False
    retrieval_structured_candidates: bool = False
    retrieval_facet_context: bool = False
    retrieval_verified_tables: bool = False
    retrieval_table_scan_chunks: int = Field(default=300, ge=1, le=2000)
    retrieval_table_scan_chars: int = Field(default=200000, ge=1000, le=1000000)
    retrieval_table_max_hops: int = Field(default=3, ge=1, le=3)
    retrieval_table_max_rows: int = Field(default=40, ge=1, le=100)
    retrieval_table_max_sources: int = Field(default=16, ge=1, le=32)
    retrieval_term_aliases: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "durability": ["持久性"],
            "atomicity": ["原子性"],
            "isolation": ["隔离性"],
            "encapsulation": ["封装"],
            "generalization": ["泛化"],
            "refactoring": ["重构"],
            "recursion": ["递归"],
            "immutable": ["不可变"],
            "mutable": ["可变"],
            "持久性": ["durability"],
            "封装": ["encapsulation"],
            "泛化": ["generalization"],
            "重构": ["refactoring"],
            "递归": ["recursion"],
        }
    )

    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    @property
    def cors_origins(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.backend_cors_origins.split(",")
            if origin.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    values = dotenv_values(ENV_FILE)
    overridden = [
        name
        for name in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY")
        if name in os.environ and name in values and os.environ[name] != values[name]
    ]
    if overridden:
        logging.getLogger(__name__).warning(
            "环境变量覆盖了项目 .env 的 %s；实际 LLM 模型：%s。"
            "如需使用 .env，请清除终端中的这些变量后重启后端。",
            ", ".join(overridden),
            settings.llm_model,
        )
    return settings
