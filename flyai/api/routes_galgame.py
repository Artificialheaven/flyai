"""Galgame 模式专用接口：前端驱动的分句语音合成 + 阻塞选项应答。

与 AI 工具调用无关：流式接收中前端在拿到完整对白时直接请求 /tts，
用户点击选项时请求 /choice 放行阻塞中的 galgame_choice 工具。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from ..deepseek_tools import DeepSeekToolsClient, usable
from ..errors import DeepSeekError
from ..galgame.savepoints import SavePointStore
from ..metering import billable_chars, make_usage_meter, tts_cost
from ..sessions.json_store import session_from_dict, session_to_dict
from ..sessions.resolve import member_character
from ..tts import DoubaoTtsClient
from ..tts.protocol import TtsError
from ..user import ADMIN_UID

logger = logging.getLogger("flyai.galgame.api")

router = APIRouter(prefix="/api/galgame", tags=["galgame"])

_TEXT_MAX_LEN = 1000
_FORMAT_EXT = {
    "mp3": ".mp3",
    "ogg_opus": ".ogg",
    "pcm": ".wav",
}


class GalgameTtsBody(BaseModel):
    session_id: str
    text: str


class GalgameChoiceBody(BaseModel):
    run_id: str
    text: str


class SavePointBody(BaseModel):
    session_id: str
    name: Optional[str] = ""


class SavePointRestoreBody(BaseModel):
    session_id: str
    savepoint_id: str
    save_current: bool = False
    current_name: Optional[str] = ""


def _galgame_session(state, sc, session_id: str):
    s = sc.store.get(session_id)
    if s is None:
        raise HTTPException(404, "会话不存在")
    if getattr(s, "mode", "normal") != "galgame":
        raise HTTPException(400, "该会话不是 Galgame 模式")
    # 功能权限门槛：admin 恒过；其余用户按配置的最低等级拦截（先于 TTS 缓存）
    access = getattr(getattr(state, "config", None), "access", None)
    required = int(getattr(access, "galgame_min_level", 0) or 0) if access else 0
    level = int(getattr(sc, "level", 0) or 0)
    if sc.uid != ADMIN_UID and level < required:
        raise HTTPException(
            403,
            "权限等级不足（Galgame 需要 Lv%d，当前 Lv%d）" % (required, level),
        )
    return s


def _tts_price(ds) -> float:
    try:
        return max(0.0, float(getattr(ds, "tts_price", 3.0)))
    except (TypeError, ValueError):
        return 3.0


def _galgame_character(sc, session):
    """取 Galgame 房间内唯一 AI 座位的有效角色卡。"""
    member = None
    for m in session.members:
        if not m.is_user:
            member = m
            break
    if member is None:
        raise HTTPException(400, "房间内没有 AI 角色")
    return member_character(member, sc.lore), member


@router.post("/tts")
async def galgame_tts(body: GalgameTtsBody, request: Request):
    """把一句对白合成为音频。等级门槛与音色解析复用 TTS 配置：
    角色卡绑定音色优先，否则默认音色。返回音频 URL。"""
    state = request.app.state.flyai
    sc = state.user(request)
    session = _galgame_session(state, sc, body.session_id)

    text = (body.text or "").strip()
    if not text:
        raise HTTPException(400, "文本为空")
    if len(text) > _TEXT_MAX_LEN:
        raise HTTPException(400, "文本过长（超过 %d 字）" % _TEXT_MAX_LEN)

    # 已合成过（含刷新后重入）：直接返回已保存的音频 URL，不再请求一次 TTS
    overlay0 = state.galgame_tts_overlay.get(session.id, {})
    cached_url = (getattr(session, "galgame_tts", None) or {}).get(text) \
        or overlay0.get(text)
    if cached_url:
        return {"url": cached_url, "speaker": "", "voice_source": "cache"}

    tts = state.config.tts
    user_level = int(getattr(sc, "level", 0) or 0)
    required_level = int(tts.min_level or 0)
    if user_level < required_level:
        raise HTTPException(
            403,
            "权限等级不足（语音合成需要 Lv%d，当前 Lv%d）"
            % (required_level, user_level),
        )

    ds = getattr(state.config, "deepseek", None)
    meter = make_usage_meter(state, sc)
    price = _tts_price(ds)
    cost = tts_cost(billable_chars(text), price)
    if cost > 0 and not meter.available():
        raise HTTPException(402, "余额不足，请充值")

    char, _member = _galgame_character(sc, session)
    bound_voice = getattr(char, "voice", "") or ""
    engine = ""
    ds_error = ""

    if getattr(tts, "engine", "deepseek") == "deepseek" and usable(ds, "tts"):
        default_voice = getattr(ds, "tts_voice", "stella")
        if bound_voice in ("stella", "tide"):
            speaker, voice_source = bound_voice, "character"
        else:
            speaker = default_voice if default_voice in ("stella", "tide") else "stella"
            voice_source = "default"
        try:
            content = await asyncio.wait_for(
                DeepSeekToolsClient(ds).tts(text, speaker, "wav"),
                timeout=float(getattr(ds, "timeout_seconds", 60) or 60),
            )
        except (DeepSeekError, asyncio.TimeoutError) as exc:
            logger.warning("Galgame DeepSeek TTS 失败，回退豆包: %s", exc)
            ds_error = str(exc) or type(exc).__name__
        else:
            engine = "deepseek"
            audio_format = "wav"
            ext = ".wav"
            sample_rate = 24000

    if not engine:
        # 回退豆包：只有走这条路才要求豆包 Key
        if not (tts.api_key or "").strip():
            raise HTTPException(
                400,
                "语音合成不可用：DeepSeek %s，且未配置豆包语音 API Key"
                % ("调用失败（%s）" % ds_error if ds_error
                   else "未启用或未配置 Key"),
            )
        speaker = (bound_voice or tts.default_speaker or "").strip()
        voice_source = "character" if bound_voice else "default"
        if not speaker:
            raise HTTPException(400, "未配置默认音色（default_speaker）")

        try:
            result = await asyncio.wait_for(
                DoubaoTtsClient(tts).synthesize(text, speaker),
                timeout=tts.timeout_seconds,
            )
        except asyncio.TimeoutError:
            raise HTTPException(504, "语音合成超时（%d 秒）" % tts.timeout_seconds)
        except TtsError as exc:
            logger.warning("Galgame TTS 服务错误: %s %s", exc.code, exc.message)
            raise HTTPException(502, "语音合成失败：%s" % (exc.message or exc.code))
        except Exception as exc:  # 传输层错误
            logger.exception("Galgame TTS 调用失败")
            raise HTTPException(502, "语音合成失败：%s: %s"
                               % (type(exc).__name__, exc))

        engine = "doubao"
        audio_format = (tts.format or "mp3").strip()
        ext = _FORMAT_EXT.get(audio_format, ".mp3")
        content = result.audio
        sample_rate = int(tts.sample_rate)
        if audio_format == "pcm":
            from ..tools.tts_tool import pcm_to_wav

            content = pcm_to_wav(content, sample_rate)

    services = {
        "upload_dir": sc.upload_dir,
        "upload_url_base": "/uploads/%s" % sc.uid,
    }
    from ..tools.tts_tool import save_generated_audio

    audio_meta = {
        "speaker": speaker,
        "voice_source": voice_source,
        "text_len": len(text),
        "format": audio_format,
        "sample_rate": sample_rate,
        "galgame": True,
    }
    saved = save_generated_audio(services, content, ext, audio_meta)
    # 回退豆包同样按 DeepSeek 字符单价计费
    meter.charge("tts", billable_chars(text), "字符", cost, engine,
                 session_id=session.id)
    # 记入进程内 overlay：由 drive 在其保存点合并落盘。端点不能直接 save：
    # 它持有的是磁盘旧实例，会覆盖 drive 内存中尚未落盘的助理内容
    state.galgame_tts_overlay.setdefault(session.id, {})[text] = saved["url"]
    return {"url": saved["url"], "speaker": speaker, "voice_source": voice_source}


@router.post("/choice")
async def galgame_choice(body: GalgameChoiceBody, request: Request):
    """用户对阻塞中的 galgame_choice 工具作答（选项或自由输入）。"""
    state = request.app.state.flyai
    sc = state.user(request)
    run_id = (body.run_id or "").strip()
    text = (body.text or "").strip()
    if not run_id or not text:
        raise HTTPException(400, "run_id 与 text 不能为空")
    try:
        ok = state.galgame_choices.resolve(run_id, sc.uid, text)
    except KeyError:
        raise HTTPException(404, "选项不存在或已失效（可能运行已结束）")
    except PermissionError:
        raise HTTPException(403, "无权应答该选项")
    if not ok:
        raise HTTPException(400, "选项已被消费")
    return {"ok": True}


# ===== 存档点 =====

def _savepoint_store(state, sc) -> SavePointStore:
    return SavePointStore(state.data_dir, sc.uid)


def _fold_tts_overlay(state, session) -> None:
    """把进程内 TTS overlay 合并进会话（与 drive 同一套合并规则）。"""
    ov = state.galgame_tts_overlay.get(session.id)
    if not ov:
        return
    merged = dict(ov)
    merged.update(getattr(session, "galgame_tts", None) or {})
    session.galgame_tts = merged


async def _cancel_active_run(state, sc, sid: str) -> None:
    """存档/回档前停掉本会话仍在进行的运行并等它收尾（drive 的 finally
    会把半截内容落盘），否则后台运行之后会覆盖回档后的会话。"""
    run = state.run_manager.active_in_session(sc.uid, sid)
    if run is None:
        return
    state.galgame_choices.discard(run.id)
    if run.task is not None:
        run.task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(run.task), timeout=10)
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
            pass


@router.get("/savepoints")
async def list_savepoints(session_id: str, request: Request):
    """列出本会话全部存档点（按创建顺序）。"""
    state = request.app.state.flyai
    sc = state.user(request)
    _galgame_session(state, sc, session_id)
    items = _savepoint_store(state, sc).list(session_id)
    return {"savepoints": [r.to_dict() for r in items]}


@router.post("/savepoints")
async def create_savepoint(body: SavePointBody, request: Request):
    """把当前位置保存为存档点。不打断正在进行的运行：drive 会在选项点
    落盘，因此快照即使用户边演出边存，也只可能落后数秒、不会回写覆盖。"""
    state = request.app.state.flyai
    sc = state.user(request)
    session = _galgame_session(state, sc, body.session_id)
    snapshot = session_to_dict(session)
    # overlay 只并入快照，不写回会话文件（运行内存中可能有更新的内容）
    ov = state.galgame_tts_overlay.get(session.id)
    if ov:
        merged = dict(ov)
        merged.update(snapshot.get("galgame_tts") or {})
        snapshot["galgame_tts"] = merged
    rec = _savepoint_store(state, sc).create(
        session.id, snapshot, body.name or "")
    return {"savepoint": rec.to_dict()}


@router.post("/savepoints/restore")
async def restore_savepoint(body: SavePointRestoreBody, request: Request):
    """回到某个存档点。save_current=true 时先把当前位置另存一个存档点。"""
    state = request.app.state.flyai
    sc = state.user(request)
    session = _galgame_session(state, sc, body.session_id)
    sp_store = _savepoint_store(state, sc)
    sp_id = (body.savepoint_id or "").strip()
    if not sp_id:
        raise HTTPException(400, "缺少存档点 id")
    target = sp_store.load_snapshot(session.id, sp_id)
    if target is None:
        raise HTTPException(404, "存档点不存在或已失效")

    await _cancel_active_run(state, sc, session.id)
    # 重新读取当前会话（运行收尾后的最新状态），再决定是否另存
    current = sc.store.get(session.id)
    _fold_tts_overlay(state, current)
    current_sp = None
    if body.save_current:
        current_sp = sp_store.create(
            session.id, session_to_dict(current), body.current_name or "")

    restored = session_from_dict(target, session.id)
    if restored.id != session.id:
        raise HTTPException(400, "存档点与当前会话不匹配")
    sc.store.save(restored)
    # overlay 中的文本可能指向已不存在的剧情：清掉，后续播放按快照重新积累
    state.galgame_tts_overlay.pop(session.id, None)
    return {"ok": True, "savepoint": current_sp.to_dict() if current_sp else None}


class _SavePointDeleteBody(BaseModel):
    session_id: str
    savepoint_id: str


@router.post("/savepoints/delete")
async def delete_savepoint(body: _SavePointDeleteBody, request: Request):
    """删除一个存档点。"""
    state = request.app.state.flyai
    sc = state.user(request)
    session = _galgame_session(state, sc, body.session_id)
    ok = _savepoint_store(state, sc).delete(session.id, body.savepoint_id)
    if not ok:
        raise HTTPException(404, "存档点不存在")
    return {"ok": True}
