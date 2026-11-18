"""爱发电自动发卡：webhook 校验、幂等、上限/补发、领取与配置脱敏。"""

import asyncio
import hashlib
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import httpx
import pytest
from fastapi import HTTPException

from flyai.afdian import afdian_sign, compute_credits, pick_paid_order
from flyai.afdian_orders import AfdianOrderStore
from flyai.api import routes_afdian
from flyai.api.routes_afdian import (
    ClaimPayload,
    admin_reissue_order,
    afdian_public_info,
    afdian_webhook,
    claim_afdian_order,
    my_afdian_orders,
)
from flyai.api.routes_config import ConfigPayload, get_config, put_config
from flyai.auth_sessions import AuthSessionStore, LoginRateLimiter
from flyai.cdk import CdkError, CdkStore
from flyai.config import AfdianPlanSettings, AppConfig
from flyai.user import ADMIN_LEVEL, ADMIN_UID


class FakeUserStore:
    def __init__(self):
        self.users = {}

    def add(self, uid, name, level=0, email=""):
        u = SimpleNamespace(id=uid, name=name, level=level, email=email)
        self.users[uid] = u
        return u

    def get_by_id(self, uid):
        return self.users.get(uid)

    def get_by_name(self, name):
        for u in self.users.values():
            if u.name == name:
                return u
        return None


class FakeMailer:
    def __init__(self):
        self.sent = []

    def send(self, cfg, to, subject, text):
        self.sent.append({"to": to, "subject": subject, "text": text})


def _state(tmp_path):
    cfg = AppConfig()
    cfg.afdian.enabled = True
    cfg.afdian.user_id = "af-user"
    cfg.afdian.token = "af-token"
    cfg.afdian.webhook_token = "whsec"
    cfg.afdian.plans = [
        AfdianPlanSettings(plan_id="p1", name="月卡", amount=10, ratio=1.2)
    ]
    state = SimpleNamespace(
        config=cfg,
        config_path=tmp_path / "config.json",
        user_store=FakeUserStore(),
        _scopes={},
        auth_sessions=AuthSessionStore(),
        login_limiter=LoginRateLimiter(),
        cdk_store=CdkStore(tmp_path),
        afdian_orders=AfdianOrderStore(tmp_path),
        mailer=FakeMailer(),
        afdian_transport=None,
        current_uid="u-bob",
        reload_providers=lambda: None,
        reload_tools=lambda: None,
    )
    state.user_store.add(ADMIN_UID, name="admin", level=ADMIN_LEVEL)
    state.user_store.add("u-bob", name="bob", email="bob@example.com")
    state.user_store.add("u-carol", name="carol")
    state.user_store.add("u-dave", name="dave")
    state.user_store.add("u10", name="user")
    state.user = lambda request: SimpleNamespace(uid=state.current_uid)
    return state


def _req(state, uid=ADMIN_UID):
    is_admin = uid == ADMIN_UID
    token = state.auth_sessions.issue(
        uid, uid, "admin" if is_admin else "user", ADMIN_LEVEL if is_admin else 0
    )
    return SimpleNamespace(
        headers={"authorization": "Bearer " + token},
        client=SimpleNamespace(host="127.0.0.1"),
        app=SimpleNamespace(state=SimpleNamespace(flyai=state)),
    )


class _HookReq:
    def __init__(self, state, body=b"", token="whsec"):
        self.app = SimpleNamespace(state=SimpleNamespace(flyai=state))
        self.headers = {}
        self.client = SimpleNamespace(host="127.0.0.1")
        self.query_params = {"token": token}
        self._body = body

    async def stream(self):
        yield self._body


def _transport(items=None, ec=200):
    def handler(request):
        return httpx.Response(
            200,
            json={"ec": ec, "em": "" if ec == 200 else "bad", "data": {"list": items or []}},
        )

    return httpx.MockTransport(handler)


def _order(no="T1", status=2, amount="60.00", plan_id="p1", remark="bob"):
    return {
        "out_trade_no": no,
        "status": status,
        "total_amount": amount,
        "plan_id": plan_id,
        "plan_title": "月卡",
        "remark": remark,
        "user_id": "af1",
    }


def _body(order):
    return json.dumps({"data": {"order": order}}).encode("utf-8")


@pytest.fixture(autouse=True)
def _reset_claim_failures():
    routes_afdian._CLAIM_FAILS.clear()
    yield
    routes_afdian._CLAIM_FAILS.clear()


def _hook(state, order=None, body=None, token="whsec"):
    body = body if body is not None else _body(order or _order())
    return asyncio.run(afdian_webhook(_HookReq(state, body=body, token=token)))


# ---- 纯函数 ----

def test_sign_and_pick_paid_order():
    params = '{"out_trade_no":"T1"}'
    raw = "tk" + "params" + params + "ts" + "1700000000" + "user_id" + "u1"
    assert afdian_sign("tk", params, 1700000000, "u1") == (
        hashlib.md5(raw.encode("utf-8")).hexdigest())
    items = [_order(status=1), _order(no="T2", status=2)]
    assert pick_paid_order(items, "T1") is None
    assert pick_paid_order(items, "T2")["out_trade_no"] == "T2"


def test_compute_credits_plan_and_fallback(tmp_path):
    state = _state(tmp_path)
    assert compute_credits(state.config.afdian, _order()) == 12.0
    # 未匹配方案 → 默认金额 0 时按实付金额 × 默认倍率
    assert compute_credits(state.config.afdian, _order(plan_id="other")) == 60.0
    state.config.afdian.default_ratio = 2
    assert compute_credits(state.config.afdian, _order(plan_id="other")) == 120.0


# ---- 公开接口 ----

def test_public_info(tmp_path):
    state = _state(tmp_path)
    state.config.afdian.purchase_url = "https://afdian.com/a/x"
    state.config.afdian.purchase_label = "支持我"
    resp = afdian_public_info(_req(state))
    assert resp.headers["cache-control"] == "no-store"
    assert json.loads(resp.body.decode()) == {
        "enabled": True,
        "purchase_url": "https://afdian.com/a/x",
        "purchase_label": "支持我",
    }
    state.config.afdian.enabled = False
    assert json.loads(afdian_public_info(_req(state)).body.decode())["enabled"] is False


def test_webhook_guards(tmp_path):
    state = _state(tmp_path)
    state.config.afdian.enabled = False
    with pytest.raises(HTTPException) as ei:
        _hook(state)
    assert ei.value.status_code == 404

    state.config.afdian.enabled = True
    with pytest.raises(HTTPException) as ei:
        _hook(state, token="nope")
    assert ei.value.status_code == 401

    with pytest.raises(HTTPException) as ei:
        _hook(state, body=b"x" * 70000)
    assert ei.value.status_code == 413

    with pytest.raises(HTTPException) as ei:
        _hook(state, body=b"{")
    assert ei.value.status_code == 400

    with pytest.raises(HTTPException) as ei:
        _hook(state, body=b'{"data":{"order":{}}}')
    assert ei.value.status_code == 400


# ---- 发卡 ----

def test_webhook_issues_bound_cdk(tmp_path):
    state = _state(tmp_path)
    state.afdian_transport = _transport([_order()])
    resp = _hook(state)
    assert json.loads(resp.body) == {"ec": 200, "em": ""}

    rec = state.afdian_orders.get("T1")
    assert rec["state"] == "issued" and rec["credits"] == 12.0
    assert rec["bound_uid"] == "u-bob" and rec["plan_title"] == "月卡"
    code = rec["cdk_code"]
    assert code
    assert state.cdk_store.list()[0]["bound_uid"] == "u-bob"
    # 绑定用户可兑换，其他用户被拒
    assert state.cdk_store.check_redeem("u-bob", code)["amount"] == 12.0
    with pytest.raises(CdkError):
        state.cdk_store.check_redeem("u-carol", code)


def test_webhook_is_idempotent(tmp_path):
    state = _state(tmp_path)
    state.afdian_transport = _transport([_order()])
    assert _hook(state).status_code == 200
    assert _hook(state).status_code == 200
    assert len(state.cdk_store.list()) == 1
    assert state.afdian_orders.get("T1")["state"] == "issued"


def test_webhook_unpaid_and_api_error_are_verify_failed(tmp_path):
    state = _state(tmp_path)
    state.afdian_transport = _transport([_order(status=1)])
    resp = _hook(state)
    assert json.loads(resp.body)["ec"] == 500
    assert state.afdian_orders.get("T1")["state"] == "verify_failed"

    state.afdian_transport = _transport([], ec=100)
    resp = _hook(state, order=_order(no="T2"))
    assert json.loads(resp.body)["ec"] == 500
    assert state.afdian_orders.get("T2")["state"] == "verify_failed"
    assert state.cdk_store.list() == []


def test_cap_pending_then_reissue(tmp_path):
    state = _state(tmp_path)
    state.config.afdian.max_total_amount = 100
    state.afdian_transport = _transport([_order("T1")])
    assert _hook(state).status_code == 200

    state.afdian_transport = _transport([_order("T2", remark="")])
    assert _hook(state, order=_order("T2", remark="")).status_code == 200
    rec = state.afdian_orders.get("T2")
    assert rec["state"] == "pending_cap" and rec["cdk_code"] == ""
    # 待补发订单可以领取？不行——尚未发卡
    state.current_uid = "u-bob"
    with pytest.raises(HTTPException) as ei:
        claim_afdian_order(ClaimPayload(out_trade_no="T2"), _req(state))
    assert ei.value.status_code == 409

    state.config.afdian.max_total_amount = -1
    result = asyncio.run(admin_reissue_order("T2", _req(state)))
    assert result["result"]["state"] == "issued"
    assert result["order"]["state"] == "issued" and result["order"]["cdk_code"]


def test_email_delivery_only_when_enabled_and_bound(tmp_path):
    state = _state(tmp_path)
    state.afdian_transport = _transport([_order()])
    _hook(state)
    # 默认 SMTP 关闭：不调用
    assert state.mailer.sent == []

    state.afdian_orders._orders.clear()
    state.config.email.enabled = True
    _hook(state)
    assert len(state.mailer.sent) == 1
    assert state.mailer.sent[0]["to"] == "bob@example.com"
    assert state.afdian_orders.get("T1")["email_sent"] is True


# ---- 用户领取 ----

def test_claim_binds_and_is_idempotent(tmp_path):
    state = _state(tmp_path)
    state.afdian_transport = _transport([_order(remark="")])
    _hook(state, order=_order(remark=""))
    rec = state.afdian_orders.get("T1")
    assert rec["bound_uid"] == "" and rec["cdk_code"]

    state.current_uid = "u-bob"
    resp = claim_afdian_order(ClaimPayload(out_trade_no="T1"), _req(state))
    assert resp["code"] == rec["cdk_code"] and resp["credits"] == 12.0
    assert state.afdian_orders.get("T1")["bound_uid"] == "u-bob"
    # 重复领取幂等
    again = claim_afdian_order(ClaimPayload(out_trade_no="T1"), _req(state))
    assert again["code"] == rec["cdk_code"]

    state.current_uid = "u-carol"
    with pytest.raises(HTTPException) as ei:
        claim_afdian_order(ClaimPayload(out_trade_no="T1"), _req(state))
    assert ei.value.status_code == 409

    state.current_uid = "u-bob"
    with pytest.raises(HTTPException) as ei:
        claim_afdian_order(ClaimPayload(out_trade_no="NOPE"), _req(state))
    assert ei.value.status_code == 404


def test_my_orders_only_returns_own(tmp_path):
    state = _state(tmp_path)
    state.afdian_transport = _transport([_order()])
    _hook(state)
    state.current_uid = "u-bob"
    mine = my_afdian_orders(_req(state))["orders"]
    assert len(mine) == 1 and mine[0]["out_trade_no"] == "T1"
    assert mine[0]["redeemed"] is False
    # 兑换后标记为已兑换
    code = mine[0]["code"]
    state.cdk_store.mark_redeemed(code, "u-bob")
    assert my_afdian_orders(_req(state))["orders"][0]["redeemed"] is True
    state.current_uid = "u-carol"
    assert my_afdian_orders(_req(state))["orders"] == []


# ---- 配置脱敏 ----

def test_config_masks_preserves_and_hides(tmp_path):
    state = _state(tmp_path)
    af = get_config(_req(state))["afdian"]
    assert af["token_set"] is True and af["token"] != "af-token"
    assert af["webhook_token_set"] is True
    # webhook Token 明文下发，便于后台拼接/复制回调地址
    assert af["webhook_token"] == "whsec"
    # 普通用户完全看不到该段
    assert "afdian" not in get_config(_req(state, "u10"))

    put_config(
        ConfigPayload(afdian={"enabled": True, "token": "", "webhook_token": ""}),
        _req(state),
    )
    assert state.config.afdian.token == "af-token"
    assert state.config.afdian.webhook_token == "whsec"

    # 首次保存 webhook Token 留空时自动生成
    state.config.afdian.webhook_token = ""
    put_config(ConfigPayload(afdian={"enabled": True}), _req(state))
    assert len(state.config.afdian.webhook_token) >= 16
