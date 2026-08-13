import asyncio

import pytest

from app.config import Settings
from app.memory import Mem0MemoryStore, MemoryNotFoundError


def configured_memory_settings() -> Settings:
    return Settings(
        _env_file=None,
        memory_enabled=True,
        mem0_llm_api_key="llm-test-key",
        mem0_embedder_api_key="embed-test-key",
        mem0_postgres_url="postgresql://test:test@127.0.0.1:5432/liko_memory",
    )


def test_memory_configuration_requires_explicit_enable_and_credentials() -> None:
    assert Settings(_env_file=None).memory_configured is False
    assert configured_memory_settings().memory_configured is True


def test_mem0_configuration_uses_existing_models_and_pgvector() -> None:
    store = Mem0MemoryStore(configured_memory_settings(), client=object())
    config = store._config()

    assert config["llm"]["config"]["model"] == "deepseek-v4-flash"
    assert config["embedder"]["config"]["model"] == "BAAI/bge-m3"
    assert "embedding_dims" not in config["embedder"]["config"]
    assert config["vector_store"]["provider"] == "pgvector"
    assert config["vector_store"]["config"]["embedding_model_dims"] == 1024
    assert config["vector_store"]["config"]["collection_name"] == "liko_memories"
    assert "健康、家庭和财务" in config["custom_instructions"]


def test_memory_update_checks_user_ownership_before_mutation() -> None:
    class FakeClient:
        def get(self, memory_id):
            assert memory_id == "mem_other"
            return {
                "id": "mem_other",
                "memory": "另一位用户的记忆",
                "user_id": "user_other",
            }

        def update(self, memory_id, text):
            raise AssertionError("an unowned memory must never be updated")

    store = Mem0MemoryStore(configured_memory_settings(), client=FakeClient())

    with pytest.raises(MemoryNotFoundError):
        asyncio.run(store.update_memory("user_123", "mem_other", "恶意修改"))
