import asyncio
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Any, Protocol

from .config import Settings, get_settings


DEFAULT_MEMORY_INSTRUCTIONS = """
从对话中提取对未来陪伴确实有帮助、且由用户明确表达的信息。可以包括用户的校区、专业、目标、兴趣、表达偏好、健康、家庭和财务相关情况；不要因为内容类别而自动排除。不要猜测、诊断或给用户贴未经表达的人格标签。信息变化时更新旧记忆，不要保留互相冲突的当前事实。
""".strip()


class MemoryServiceError(RuntimeError):
    """A safe memory-layer failure without provider payloads or credentials."""


class MemoryNotFoundError(MemoryServiceError):
    pass


@dataclass(slots=True)
class MemoryRecord:
    id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str | None = None
    updated_at: str | None = None


class LongTermMemoryStore(Protocol):
    configured: bool

    async def search(self, user_id: str, query: str, limit: int) -> list[MemoryRecord]: ...

    async def remember_interaction(
        self,
        user_id: str,
        user_message: str,
        assistant_message: str,
        conversation_id: str,
    ) -> None: ...

    async def remember_fact(self, user_id: str, fact: str, conversation_id: str) -> None: ...

    async def list_memories(self, user_id: str, limit: int = 100) -> list[MemoryRecord]: ...

    async def update_memory(self, user_id: str, memory_id: str, text: str) -> MemoryRecord: ...

    async def delete_memory(self, user_id: str, memory_id: str) -> None: ...

    async def delete_all(self, user_id: str) -> None: ...


class DisabledMemoryStore:
    configured = False

    async def search(self, user_id: str, query: str, limit: int) -> list[MemoryRecord]:
        return []

    async def remember_interaction(
        self,
        user_id: str,
        user_message: str,
        assistant_message: str,
        conversation_id: str,
    ) -> None:
        return None

    async def remember_fact(self, user_id: str, fact: str, conversation_id: str) -> None:
        return None

    async def list_memories(self, user_id: str, limit: int = 100) -> list[MemoryRecord]:
        raise MemoryServiceError("长期记忆服务尚未配置。")

    async def update_memory(self, user_id: str, memory_id: str, text: str) -> MemoryRecord:
        raise MemoryServiceError("长期记忆服务尚未配置。")

    async def delete_memory(self, user_id: str, memory_id: str) -> None:
        raise MemoryServiceError("长期记忆服务尚未配置。")

    async def delete_all(self, user_id: str) -> None:
        raise MemoryServiceError("长期记忆服务尚未配置。")


class Mem0MemoryStore:
    configured = True

    def __init__(self, settings: Settings, client: Any | None = None):
        if not settings.memory_configured and client is None:
            raise MemoryServiceError("长期记忆服务配置不完整。")
        self.settings = settings
        self._client = client
        self._client_lock = Lock()

    def _config(self) -> dict[str, Any]:
        llm_base_key = (
            "deepseek_base_url"
            if self.settings.mem0_llm_provider == "deepseek"
            else "openai_base_url"
        )
        return {
            "llm": {
                "provider": self.settings.mem0_llm_provider,
                "config": {
                    "model": self.settings.mem0_llm_model,
                    "api_key": self.settings.mem0_llm_api_key,
                    llm_base_key: self.settings.mem0_llm_base_url,
                    "temperature": 0.1,
                    "max_tokens": 1200,
                },
            },
            "embedder": {
                "provider": self.settings.mem0_embedder_provider,
                "config": {
                    "model": self.settings.mem0_embedder_model,
                    "api_key": self.settings.mem0_embedder_api_key,
                    "openai_base_url": self.settings.mem0_embedder_base_url,
                },
            },
            "vector_store": {
                "provider": "pgvector",
                "config": {
                    "connection_string": self.settings.mem0_postgres_url,
                    "collection_name": self.settings.mem0_collection_name,
                    "embedding_model_dims": self.settings.mem0_embedding_dims,
                    "diskann": False,
                    "hnsw": True,
                },
            },
            "history_db_path": self.settings.mem0_history_db_path,
            "custom_instructions": DEFAULT_MEMORY_INSTRUCTIONS,
        }

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        with self._client_lock:
            if self._client is not None:
                return self._client
            try:
                history_parent = Path(self.settings.mem0_history_db_path).expanduser().parent
                history_parent.mkdir(parents=True, exist_ok=True)
                from mem0 import Memory

                self._client = Memory.from_config(self._config())
            except Exception as error:
                raise MemoryServiceError("长期记忆服务初始化失败。") from error
        return self._client

    def _reset_client(self) -> None:
        with self._client_lock:
            self._client = None

    async def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                client = self._get_client()
                return await asyncio.to_thread(getattr(client, method), *args, **kwargs)
            except TypeError:
                raise
            except Exception as error:
                last_error = error
                if attempt == 0:
                    self._reset_client()
        assert last_error is not None
        raise last_error

    @staticmethod
    def _records(payload: Any) -> list[MemoryRecord]:
        items = payload.get("results", []) if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            return []
        records: list[MemoryRecord] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            memory_id = item.get("id") or item.get("memory_id")
            text = item.get("memory") or item.get("text") or item.get("content")
            if not isinstance(memory_id, str) or not isinstance(text, str):
                continue
            records.append(
                MemoryRecord(
                    id=memory_id,
                    text=text,
                    metadata=item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
                    created_at=item.get("created_at") if isinstance(item.get("created_at"), str) else None,
                    updated_at=item.get("updated_at") if isinstance(item.get("updated_at"), str) else None,
                )
            )
        return records

    async def search(self, user_id: str, query: str, limit: int) -> list[MemoryRecord]:
        try:
            payload = await self._call(
                "search",
                query=query,
                filters={"user_id": user_id},
                top_k=limit,
            )
        except Exception as error:
            raise MemoryServiceError("长期记忆检索失败。") from error
        return self._records(payload)

    async def remember_interaction(
        self,
        user_id: str,
        user_message: str,
        assistant_message: str,
        conversation_id: str,
    ) -> None:
        messages = [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": assistant_message},
        ]
        try:
            await self._call(
                "add",
                messages,
                user_id=user_id,
                metadata={"conversation_id": conversation_id, "source": "liko_chat"},
            )
        except Exception as error:
            raise MemoryServiceError("长期记忆写入失败。") from error

    async def remember_fact(self, user_id: str, fact: str, conversation_id: str) -> None:
        try:
            await self._call(
                "add",
                fact,
                user_id=user_id,
                metadata={"conversation_id": conversation_id, "source": "liko_explicit_profile"},
                infer=False,
            )
        except Exception as error:
            raise MemoryServiceError("长期记忆写入失败。") from error

    async def list_memories(self, user_id: str, limit: int = 100) -> list[MemoryRecord]:
        try:
            try:
                payload = await self._call(
                    "get_all",
                    filters={"user_id": user_id},
                    top_k=limit,
                )
            except TypeError:
                payload = await self._call("get_all", user_id=user_id)
        except Exception as error:
            raise MemoryServiceError("长期记忆读取失败。") from error
        return self._records(payload)

    async def _owned_memory(self, user_id: str, memory_id: str) -> MemoryRecord:
        try:
            payload = await self._call("get", memory_id)
        except Exception as error:
            raise MemoryServiceError("长期记忆读取失败。") from error
        if not isinstance(payload, dict) or payload.get("user_id") != user_id:
            raise MemoryNotFoundError("没有找到这条记忆。")
        records = self._records([payload])
        if not records:
            raise MemoryNotFoundError("没有找到这条记忆。")
        return records[0]

    async def update_memory(self, user_id: str, memory_id: str, text: str) -> MemoryRecord:
        await self._owned_memory(user_id, memory_id)
        try:
            await self._call("update", memory_id, text)
        except Exception as error:
            raise MemoryServiceError("长期记忆更新失败。") from error
        return MemoryRecord(id=memory_id, text=text)

    async def delete_memory(self, user_id: str, memory_id: str) -> None:
        await self._owned_memory(user_id, memory_id)
        try:
            await self._call("delete", memory_id)
        except Exception as error:
            raise MemoryServiceError("长期记忆删除失败。") from error

    async def delete_all(self, user_id: str) -> None:
        try:
            await self._call("delete_all", user_id=user_id)
        except Exception as error:
            raise MemoryServiceError("长期记忆清空失败。") from error


@lru_cache
def get_memory_store() -> LongTermMemoryStore:
    settings = get_settings()
    if not settings.memory_configured:
        return DisabledMemoryStore()
    return Mem0MemoryStore(settings)
