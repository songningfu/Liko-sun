import asyncio

from app.memory import MemoryRecord
from scripts import verify_memory


def test_memory_verification_covers_isolation_update_and_cleanup(monkeypatch) -> None:
    class FakeSettings:
        memory_configured = True

    class FakeStore:
        records: dict[str, list[MemoryRecord]] = {}

        def __init__(self, settings):
            self.settings = settings

        async def remember_interaction(self, user_id, user_message, assistant_message, conversation_id):
            self.records[user_id] = [MemoryRecord(id="mem_1", text=user_message)]

        async def search(self, user_id, query, limit):
            return self.records.get(user_id, [])[:limit]

        async def list_memories(self, user_id, limit=100):
            return self.records.get(user_id, [])[:limit]

        async def update_memory(self, user_id, memory_id, text):
            self.records[user_id] = [MemoryRecord(id=memory_id, text=text)]
            return self.records[user_id][0]

        async def delete_memory(self, user_id, memory_id):
            self.records[user_id] = [item for item in self.records.get(user_id, []) if item.id != memory_id]

        async def delete_all(self, user_id):
            self.records.pop(user_id, None)

    monkeypatch.setattr(verify_memory, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(verify_memory, "Mem0MemoryStore", FakeStore)

    asyncio.run(verify_memory.verify())
    assert FakeStore.records == {}
