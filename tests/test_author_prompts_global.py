"""作者运行时：injectPrompts 位置语义、全局变量、EJS 聊天视图。"""

from types import SimpleNamespace

from flyai.api.routes_chat import _author_prompt_depth, _author_prompt_extra
from flyai.api.routes_st import GlobalVarsBody, get_global_vars, put_global_vars
from flyai.schemas import Message
from flyai.template.prompt import _chat_view


def _session(prompts):
    return SimpleNamespace(attributes={"author": {"prompts": prompts}})


def test_author_prompt_positions():
    session = _session([
        {"id": "a", "content": "系统注入", "position": 0},
        {"id": "b", "content": "历史末尾", "position": 1, "depth": 0, "role": 1},
        {"id": "c", "content": "深度 4 注入", "position": "in_chat", "depth": 4},
        {"id": "d", "content": "前置块", "position": "before_prompt"},
    ])
    system = _author_prompt_extra(session)
    assert "系统注入" in system and "前置块" in system
    assert "历史末尾" not in system and "深度 4" not in system
    blocks = _author_prompt_depth(session)
    assert [block["content"] for block in blocks] == ["历史末尾", "深度 4 注入"]
    assert blocks[0]["depth"] == 0 and blocks[0]["role"] == "user"
    assert blocks[1]["depth"] == 4


def _request(settings_path):
    sc = SimpleNamespace(uid="u1", settings_path=settings_path)
    state = SimpleNamespace(user=lambda request: sc)
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(flyai=state)))


def test_global_vars_roundtrip(tmp_path):
    settings = tmp_path / "settings.json"
    request = _request(settings)
    assert get_global_vars(request)["variables"] == {}
    put_global_vars(GlobalVarsBody(variables={"phone": {"unread": 2}}), request)
    assert get_global_vars(request)["variables"] == {"phone": {"unread": 2}}
    assert (tmp_path / "st_global_vars.db").is_file()
    assert not (tmp_path / "st_global_vars.json").exists()


def test_chat_view_st_shape():
    history = [
        Message(role="user", content="你好", id="u1"),
        Message(role="assistant", content="回复", id="a1", name="角色"),
    ]
    view = _chat_view(history)
    assert view[0]["mes"] == "你好" and view[0]["is_user"] is True
    assert view[1]["mes"] == "回复" and view[1]["is_user"] is False
    assert view[1]["message_id"] == 1 and view[1]["extra"] == {}
