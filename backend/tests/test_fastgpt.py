import asyncio
import json

import httpx
import pytest

from app.config import Settings
from app.fastgpt import FastGPTClient, FastGPTUpstreamError, extract_official_sources


def configured_settings(**overrides) -> Settings:
    values = {
        "fastgpt_base_url": "https://rag.example.com/api",
        "fastgpt_api_key": "test-only-key",
        "fastgpt_app_id": "app_test",
        "fastgpt_timeout_seconds": 10,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_chat_url_accepts_root_api_and_direct_endpoint() -> None:
    assert configured_settings().fastgpt_chat_url == "https://rag.example.com/api/v1/chat/completions"
    assert configured_settings(
        fastgpt_base_url="https://rag.example.com/api/v1/chat/completions"
    ).fastgpt_chat_url == "https://rag.example.com/api/v1/chat/completions"


def test_remote_fastgpt_requires_https() -> None:
    async def collect() -> None:
        client = FastGPTClient(configured_settings(fastgpt_base_url="http://rag.example.com"))
        async for _ in client.stream_chat(
            message="测试",
            conversation_id="guest_test",
            answer_id="ans_test",
            campus=None,
        ):
            pass

    with pytest.raises(FastGPTUpstreamError, match="HTTPS"):
        asyncio.run(collect())


def test_extract_official_sources_filters_and_deduplicates() -> None:
    sources = extract_official_sources(
        [
            {
                "quoteList": [
                    {
                        "q": "校园卡服务",
                        "a": "来源：https://its.szu.edu.cn/card/service",
                        "department": "信息中心",
                        "lastVerifiedAt": "2026-08-12",
                    },
                    {
                        "q": "重复来源",
                        "url": "https://its.szu.edu.cn/card/service",
                    },
                    {"q": "非校方页面", "url": "https://example.com/not-official"},
                    {"q": "非 HTTPS", "url": "http://www.szu.edu.cn/unsafe"},
                ]
            }
        ]
    )

    assert len(sources) == 1
    assert sources[0].url == "https://its.szu.edu.cn/card/service"
    assert sources[0].title == "校园卡服务"
    assert sources[0].department == "信息中心"


def test_stream_chat_translates_answer_and_late_flow_responses() -> None:
    official_url = "https://lib.szu.edu.cn/service/hours"
    flow_responses = json.dumps(
        [{"quoteList": [{"q": "图书馆开放时间", "url": official_url}]}],
        ensure_ascii=False,
    )
    upstream_sse = (
        'event: answer\n'
        'data: {"choices":[{"delta":{"content":"开放"},"finish_reason":null}]}\n\n'
        'event: answer\n'
        'data: {"choices":[{"delta":{"content":"时间"},"finish_reason":null}]}\n\n'
        'event: answer\n'
        'data: [DONE]\n\n'
        'event: flowResponses\n'
        f'data: {flow_responses}\n\n'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://rag.example.com/api/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-only-key"
        body = json.loads(request.content)
        assert body["appId"] == "app_test"
        assert body["detail"] is True
        assert body["stream"] is True
        assert body["variables"] == {"campus": "丽湖"}
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=upstream_sse.encode(),
        )

    async def collect():
        client = FastGPTClient(configured_settings(), transport=httpx.MockTransport(handler))
        return [
            event
            async for event in client.stream_chat(
                message="图书馆几点关门？",
                conversation_id="guest_test",
                answer_id="ans_test",
                campus="丽湖",
            )
        ]

    events = asyncio.run(collect())
    assert [(event.event, event.data) for event in events] == [
        ("delta", {"text": "开放"}),
        ("delta", {"text": "时间"}),
        (
            "sources",
            {
                "items": [
                    {
                        "title": "图书馆开放时间",
                        "url": official_url,
                        "department": None,
                        "publishedAt": None,
                        "lastVerifiedAt": None,
                    }
                ]
            },
        ),
    ]


def test_upstream_http_error_does_not_expose_response_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="secret upstream diagnostic")

    async def collect() -> None:
        client = FastGPTClient(configured_settings(), transport=httpx.MockTransport(handler))
        async for _ in client.stream_chat(
            message="测试",
            conversation_id="guest_test",
            answer_id="ans_test",
            campus=None,
        ):
            pass

    with pytest.raises(FastGPTUpstreamError) as caught:
        asyncio.run(collect())

    assert "401" in str(caught.value)
    assert "secret upstream diagnostic" not in str(caught.value)
