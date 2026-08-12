"""Run a destructive-to-test-data-only Mem0 smoke test, then clean up its temporary users."""

from __future__ import annotations

import argparse
import asyncio
from uuid import uuid4

from app.config import get_settings
from app.memory import Mem0MemoryStore, MemoryServiceError


async def verify() -> None:
    settings = get_settings()
    if not settings.memory_configured:
        raise RuntimeError("长期记忆环境变量尚未配置完整")

    store = Mem0MemoryStore(settings)
    run_id = uuid4().hex
    owner_id = f"verify_owner_{run_id}"
    other_id = f"verify_other_{run_id}"
    fact = f"记忆验收标记 {run_id}：用户明确喜欢简短直接的回答。"

    try:
        await store.remember_interaction(
            owner_id,
            fact,
            "好的，我会尽量简短直接。",
            f"verify_{run_id}",
        )
        owner_results = await store.search(owner_id, "回答表达偏好", 10)
        if not owner_results:
            raise RuntimeError("已写入测试对话，但未能检索到测试用户的记忆")

        other_results = await store.search(other_id, "回答表达偏好", 10)
        if other_results:
            raise RuntimeError("用户隔离失败：另一测试用户读到了不属于自己的记忆")

        owner_memories = await store.list_memories(owner_id, 100)
        if not owner_memories:
            raise RuntimeError("记忆列表未返回刚写入的测试记忆")

        first = owner_memories[0]
        updated = await store.update_memory(owner_id, first.id, f"验收标记 {run_id}：偏好简短回答。")
        if run_id not in updated.text:
            raise RuntimeError("记忆修改接口没有返回预期内容")

        await store.delete_memory(owner_id, first.id)
        remaining = await store.list_memories(owner_id, 100)
        if any(item.id == first.id for item in remaining):
            raise RuntimeError("单条记忆删除后仍可读取")
    finally:
        for user_id in (owner_id, other_id):
            try:
                await store.delete_all(user_id)
            except MemoryServiceError:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(description="验证 Mem0 与独立 PgVector 的真实读写链路")
    parser.parse_args()
    try:
        asyncio.run(verify())
    except (MemoryServiceError, RuntimeError) as error:
        print(f"长期记忆验收失败：{error}")
        return 1
    print("长期记忆验收通过：写入、检索、用户隔离、修改和删除均正常")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
