from datetime import timedelta
import json
from uuid import uuid4

from fastapi.testclient import TestClient

from app.auth import CurrentUser, get_current_user
from app.database import Database, User, get_database, utc_now
from app.main import _rank_recommendations, app


def make_database() -> tuple[Database, str]:
    database = Database("sqlite+pysqlite:///:memory:")
    user_id = str(uuid4())
    with database.session() as session, session.begin():
        session.add(
            User(
                id=user_id,
                username="history_user",
                password_hash="test-only",
                shell_color="#E95F68",
                skin_color="#FFF0C7",
            )
        )
    return database, user_id


def test_conversation_history_is_user_owned_and_deletable() -> None:
    database, user_id = make_database()
    conversation_id = "chat_history_test"
    database.ensure_conversation(user_id=user_id, conversation_id=conversation_id, title="我叫什么？")
    database.add_chat_message(
        message_id=str(uuid4()),
        conversation_id=conversation_id,
        user_id=user_id,
        role="user",
        content="我叫什么？",
        attachments_json=json.dumps([{"name": "课程表.pdf", "kind": "document"}], ensure_ascii=False),
        source_type="input",
    )
    database.add_chat_message(
        message_id=str(uuid4()),
        conversation_id=conversation_id,
        user_id=user_id,
        role="assistant",
        content="你叫小荔。",
        answer_id="ans_test",
        sources_json=json.dumps([]),
    )

    app.dependency_overrides[get_database] = lambda: database
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=user_id, authenticated=True, memory_consent=True
    )
    client = TestClient(app)
    try:
        listed = client.get("/api/users/me/conversations")
        messages = client.get(f"/api/users/me/conversations/{conversation_id}/messages")
        deleted = client.delete(f"/api/users/me/conversations/{conversation_id}")
        missing = client.get(f"/api/users/me/conversations/{conversation_id}/messages")
    finally:
        app.dependency_overrides.clear()

    assert listed.status_code == 200
    assert listed.json()[0]["messageCount"] == 2
    assert [item["role"] for item in messages.json()] == ["user", "assistant"]
    assert messages.json()[0]["attachments"] == [{"name": "课程表.pdf", "kind": "document"}]
    assert deleted.status_code == 204
    assert missing.status_code == 404


def test_saved_conversation_filters_old_cross_topic_sources_on_read() -> None:
    database, user_id = make_database()
    conversation_id = "chat_old_sources"
    database.ensure_conversation(user_id=user_id, conversation_id=conversation_id, title="图书馆几点关门？")
    database.add_chat_message(
        message_id=str(uuid4()),
        conversation_id=conversation_id,
        user_id=user_id,
        role="user",
        content="丽湖图书馆三楼几点关门？",
        source_type="input",
    )
    database.add_chat_message(
        message_id=str(uuid4()),
        conversation_id=conversation_id,
        user_id=user_id,
        role="assistant",
        content="三楼书库 22:30 关闭。",
        sources_json=json.dumps(
            [
                {"title": "图书馆服务", "url": "https://lib.szu.edu.cn/services/hours"},
                {"title": "餐厅营业安排", "url": "https://hqb.szu.edu.cn/dining/hours"},
            ],
            ensure_ascii=False,
        ),
    )
    app.dependency_overrides[get_database] = lambda: database
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=user_id, authenticated=True, memory_consent=True
    )
    client = TestClient(app)
    try:
        response = client.get(f"/api/users/me/conversations/{conversation_id}/messages")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()[1]["sources"] == [
        {
            "title": "图书馆服务",
            "url": "https://lib.szu.edu.cn/services/hours",
            "department": None,
            "publishedAt": None,
            "lastVerifiedAt": None,
        }
    ]


def test_recommendations_favor_recent_distinct_user_inputs() -> None:
    now = utc_now()
    rows = [
        {"question": "校园卡丢了怎么补办？", "userId": "u1", "source": "input", "createdAt": now},
        {"question": "校园卡丢了怎么补办", "userId": "u2", "source": "input", "createdAt": now},
        {"question": "图书馆几点关门？", "userId": "u1", "source": "recommendation", "createdAt": now},
        {"question": "这学期什么时候放假？", "userId": "u3", "source": "input", "createdAt": now - timedelta(days=6)},
    ]

    ranked = _rank_recommendations(rows, now)

    assert ranked[0]["question"].startswith("校园卡")
    assert ranked[0]["label"] == "近 7 天高频"
    assert len(ranked) == 4
