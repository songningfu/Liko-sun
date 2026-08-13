import asyncio
import json

import httpx
import pytest

from app.attachments import AttachmentNotFoundError, AttachmentProcessor
from app.config import Settings


def attachment_settings(tmp_path, **overrides) -> Settings:
    return Settings(
        _env_file=None,
        attachment_dir=str(tmp_path / "attachments"),
        session_secret="test-session-secret-at-least-thirty-two-characters",
        **overrides,
    )


def test_text_attachment_is_short_lived_and_owner_isolated(tmp_path) -> None:
    processor = AttachmentProcessor(attachment_settings(tmp_path))
    record = asyncio.run(
        processor.save(
            owner_hash="owner-a",
            filename="学习计划.md",
            content_type="text/markdown",
            data="本周完成高等数学第三章。".encode(),
        )
    )

    assert record.kind == "document"
    assert "高等数学" in processor.get(record.id, "owner-a").extracted_text
    assert "<Attachment" in processor.build_context([record])
    assert not any(path.suffix != ".json" for path in processor.directory.iterdir())
    with pytest.raises(AttachmentNotFoundError):
        processor.get(record.id, "owner-b")

    processor.delete(record.id, "owner-a")
    with pytest.raises(AttachmentNotFoundError):
        processor.get(record.id, "owner-a")


def test_image_attachment_uses_configured_vision_model(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["model"] == "Qwen/Qwen3.6-35B-A3B"
        assert payload["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "图片是一张课程通知，日期为 8 月 20 日。"}}]},
        )

    settings = attachment_settings(
        tmp_path,
        vision_enabled=True,
        vision_api_key="test-vision-key",
    )
    processor = AttachmentProcessor(settings, transport=httpx.MockTransport(handler))
    record = asyncio.run(
        processor.save(
            owner_hash="owner-a",
            filename="通知.png",
            content_type="image/png",
            data=b"not-a-real-image-but-the-upstream-is-mocked",
        )
    )

    assert record.kind == "image"
    assert "8 月 20 日" in record.extracted_text
