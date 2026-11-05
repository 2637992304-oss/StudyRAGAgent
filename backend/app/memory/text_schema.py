"""Validated text-memory inputs; semantic extraction never grants authorization."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.config import get_settings


class TextMemoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str
    course_id: str | None = None
    applies_when: str | None = Field(default=None, max_length=300)
    exceptions: str | None = Field(default=None, max_length=300)
    tags: list[str] = Field(default_factory=list, max_length=10)
    use_mode: Literal["always", "relevant"] = "relevant"
    valid_from: datetime | None = None
    expires_at: datetime | None = None

    @field_validator("content")
    @classmethod
    def validate_content(cls, value):
        # Python len counts Unicode code points. Never normalize stored user text.
        if not value.strip():
            raise ValueError("记忆正文不能为空")
        if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise ValueError("记忆正文包含无效 Unicode 字符")
        if len(value) > get_settings().memory_content_max_chars:
            raise ValueError("记忆正文超出长度限制，请整理为多条后确认保存")
        return value

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, values):
        if any(not value.strip() or len(value) > 32 for value in values):
            raise ValueError("标签须为1至32个字符")
        return list(dict.fromkeys(values))

    @field_validator("valid_from", "expires_at")
    @classmethod
    def aware_time(cls, value):
        if value is not None and value.utcoffset() is None:
            raise ValueError("记忆时间必须包含时区")
        return value

    @model_validator(mode="after")
    def validate_window(self):
        if self.valid_from and self.expires_at and self.valid_from >= self.expires_at:
            raise ValueError("到期时间必须晚于生效时间")
        return self


class TextMemoryMutation(TextMemoryInput):
    version: int = Field(default=0, ge=0)
    request_key: str = Field(min_length=1, max_length=100)


class TextMemoryBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[TextMemoryInput] = Field(min_length=1, max_length=8)
