from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class RegisterRequest(BaseModel):
    username: str = Field(min_length=4, max_length=20)
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=20)
    password: str = Field(min_length=1, max_length=128)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(alias="currentPassword", min_length=1, max_length=128)
    new_password: str = Field(alias="newPassword", min_length=8, max_length=128)

    model_config = {"populate_by_name": True}


class PasswordRecoveryRequest(BaseModel):
    username: str = Field(min_length=1, max_length=20)
    recovery_code: str = Field(alias="recoveryCode", min_length=20, max_length=32)
    new_password: str = Field(alias="newPassword", min_length=8, max_length=128)

    model_config = {"populate_by_name": True}


class MemoryConsentRequest(BaseModel):
    enabled: bool


class LikoColorChoiceRequest(BaseModel):
    shell: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    skin: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")


class ChatRequest(BaseModel):
    request_id: str = Field(alias="requestId", min_length=8, max_length=100)
    conversation_id: str | None = Field(default=None, alias="conversationId", max_length=100)
    message: str = Field(min_length=1, max_length=1000)
    campus: Literal["粤海", "丽湖", "通用"] | None = None
    source: Literal["input", "recommendation"] = "input"

    @field_validator("message")
    @classmethod
    def strip_message(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message cannot be blank")
        return value

    model_config = {"populate_by_name": True}


class FeedbackRequest(BaseModel):
    rating: Literal["helpful", "unhelpful", "none"]
    reason: Literal[
        "inaccurate",
        "outdated",
        "not_answered",
        "unclear",
        "source_problem",
    ] | None = None
    request_id: str | None = Field(default=None, alias="requestId", max_length=100)

    @field_validator("reason")
    @classmethod
    def reason_requires_negative_rating(cls, value: str | None, info):
        if value is not None and info.data.get("rating") != "unhelpful":
            raise ValueError("reason is only valid for an unhelpful rating")
        return value

    model_config = {"populate_by_name": True}


class SourceItem(BaseModel):
    title: str
    url: str
    department: str | None = None
    published_at: str | None = Field(default=None, alias="publishedAt")
    last_verified_at: str | None = Field(default=None, alias="lastVerifiedAt")

    model_config = {"populate_by_name": True}


class MemoryUpdateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)

    @field_validator("text")
    @classmethod
    def strip_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("text cannot be blank")
        return value


class MemoryItemResponse(BaseModel):
    id: str
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = Field(default=None, alias="createdAt")
    updated_at: str | None = Field(default=None, alias="updatedAt")

    model_config = {"populate_by_name": True}


class ConversationSummaryResponse(BaseModel):
    id: str
    title: str
    preview: str = ""
    message_count: int = Field(alias="messageCount")
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")

    model_config = {"populate_by_name": True}


class ConversationMessageResponse(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    content: str
    answer_id: str | None = Field(default=None, alias="answerId")
    sources: list[SourceItem] = Field(default_factory=list)
    created_at: str = Field(alias="createdAt")

    model_config = {"populate_by_name": True}


class RecommendationItemResponse(BaseModel):
    question: str
    label: str
    category: str
    score: float
