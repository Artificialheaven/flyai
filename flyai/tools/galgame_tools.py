"""Galgame 模式专用工具（6 个）：背景绘制、立绘绘制、作曲、资产清单、场景切换、选项。

仅在 Galgame 会话中可用（所需上下文经 ctx.services 注入）。生图复用
image_tool 的 select_image_model / ark_generate；资产落入 GalgameAssetStore：
立绘绑定角色卡跨会话复用，背景/音乐按会话存放。前端依据输出标记渲染：

- ``[背景已生成] <url>`` / ``[立绘已生成] <url>`` / ``[音乐已生成] <url>``
- ``[场景切换] <单行 JSON>``
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..galgame.choices import ChoiceTimeout
from ..galgame.midi import MidiSpecError, render_midi
from .base import Tool, ToolContext, ToolResult
from .image_tool import ark_generate, select_image_model
from .read_image_tool import ReadImageTool

logger = logging.getLogger("flyai.galgame")

_ALLOWED_SIZES = ("1K", "2K", "4K")
_AVATAR_MAX_BYTES = 10 * 1024 * 1024
_EXT_MEDIA = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def _need_galgame(ctx: ToolContext) -> Tuple[Any, str, str, Dict]:
    """取出 galgame 上下文；缺失时抛 RuntimeError（由调用方转失败结果）。"""
    services = ctx.services or {}
    assets = services.get("galgame_assets")
    cid = (services.get("galgame_char_id") or "").strip()
    sid = (services.get("galgame_sid") or "").strip()
    state = services.get("galgame_state")
    if assets is None or not cid or not sid or state is None:
        raise RuntimeError("当前不是 Galgame 会话，galgame 工具不可用")
    return assets, cid, sid, state


def _select_model(services: Dict[str, Any]):
    model_cfg = select_image_model(services)
    if model_cfg is None:
        raise RuntimeError(
            "当前没有可用的生图模型：请在设置中添加自己的生图模型，或联系管理员共享"
        )
    if not (model_cfg.api_key or "").strip():
        raise RuntimeError("该生图模型未配置 API Key")
    return model_cfg


def _norm_size(arguments: Dict[str, Any]) -> str:
    size = str(arguments.get("size") or "2K").strip()
    return size if size in _ALLOWED_SIZES else "2K"


def _asset_line(rec, marker: str) -> str:
    info = json.dumps(rec.to_dict(), ensure_ascii=False, separators=(",", ":"))
    return "%s %s\n[资产信息] %s" % (marker, rec.url, info)


def _select_sprite_refs(arguments: Dict[str, Any], assets, cid: str, state):
    """选出本次绘制使用的历史立绘参考：``(records, auto_picked)``。

    AI 传了 reference_sprite_ids 时按 id 解析（去重、最多 2 个）；已有历史
    立绘但没给有效 id 时自动补选——当前画面上的立绘优先，否则最新一张。
    首张立绘（无历史）返回 ``([], False)``。
    """
    existing = assets.list_sprites(cid)
    if not existing:
        return [], False

    picked = []
    seen = set()
    raw_ids = arguments.get("reference_sprite_ids") or []
    if isinstance(raw_ids, list):
        for rid in raw_ids[:2]:
            rid = str(rid or "").strip()
            if not rid or rid in seen:
                continue
            rec = assets.get_sprite(cid, rid)
            if rec is not None:
                picked.append(rec)
                seen.add(rec.id)

    if picked:
        return picked[:2], False

    # 自动补选：当前画面上的立绘优先，其次最新一张（index 按生成时间排列）
    cur_url = (state.get("sprite") or {}).get("url") if state else ""
    fallback = None
    for rec in existing:
        if rec.url == cur_url:
            fallback = rec
            break
    if fallback is None:
        fallback = existing[-1]
    return [fallback], True


class GalgameDrawBackgroundTool(Tool):
    name = "galgame_draw_background"
    description = (
        "Galgame 模式专用：生成一张横版场景背景（文生图，画面中无人物）。"
        "description 用中文描述场景（地点、时间、天气、光线、氛围、画风）。"
        "首次生成的背景会自动放置到画面上；之后生成的背景需再调用 "
        "galgame_set_scene 才会切换。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "description": {"type": "string", "description": "场景描述"},
            "size": {"type": "string", "enum": list(_ALLOWED_SIZES),
                     "description": "图片尺寸，默认 2K"},
        },
        "required": ["description"],
        "additionalProperties": False,
    }

    async def run(self, arguments: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        description = str(arguments.get("description") or "").strip()
        if not description:
            return ToolResult(ok=False, output="[参数错误] 请提供场景描述（description）")
        try:
            assets, cid, sid, state = _need_galgame(ctx)
            model_cfg = _select_model(ctx.services)
            size = _norm_size(arguments)
        except RuntimeError as exc:
            return ToolResult(ok=False, output="[工具错误] %s" % exc)

        prompt = (
            "%s。横版宽幅构图，galgame 视觉小说场景背景，画面中无人物，"
            "精美游戏场景原画，电影级光影，细节丰富" % description
        )
        try:
            content, ext, _media = await ark_generate(model_cfg, prompt, size)
            rec = assets.add_background(sid, content, ext, description, prompt)
        except Exception as exc:
            logger.exception("Galgame 背景生成失败")
            return ToolResult(
                ok=False, output="[工具错误] 背景生成失败: %s: %s"
                % (type(exc).__name__, exc)
            )
        # 首个背景自动设为当前场景
        auto = False
        if not state.get("background"):
            state["background"] = {"id": rec.id, "url": rec.url}
            auto = True
        note = "已自动放置到画面上。" if auto else "需再调用 galgame_set_scene 才会切换到新背景。"
        output = _asset_line(rec, "[背景已生成]") + "\n背景已成功生成，%s" % note
        return ToolResult(
            output=output,
            meta={"galgame_asset": rec.to_dict(), "galgame_kind": "background",
                  "galgame_auto_set": auto},
        )


class GalgameDrawSpriteTool(Tool):
    name = "galgame_draw_sprite"
    description = (
        "Galgame 模式专用：生成本角色的全身立绘（纯绿幕背景，系统会自动抠像）。"
        "description 用中文描述立绘（服装、表情、姿态、装束、画风）。\n"
        "参考图规则：\n"
        "- 首张立绘：没有历史立绘，use_avatar=true 且角色卡带头像时以头像参考；\n"
        "- 第二张起：必须先用 galgame_list_assets(kind=\"sprites\") 查看已有立绘，"
        "挑选长相 / 装束最接近的一张，把它的 id 放入 reference_sprite_ids"
        "（至少 1 个，最多 2 个），保证长相始终一致；没传时系统会自动补一张"
        "（当前画面上的立绘优先）。\n"
        "首次生成的立绘会自动放置到画面上；之后生成的立绘需再调用 "
        "galgame_set_scene 才会切换。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "description": {"type": "string", "description": "立绘描述（服装/表情/姿态）"},
            "reference_sprite_ids": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 2,
                "description": "历史立绘资产 id。首张立绘省略；第二张起至少 1 个",
            },
            "use_avatar": {"type": "boolean",
                           "description": "是否同时用角色卡头像做参考图，默认 true"},
            "size": {"type": "string", "enum": list(_ALLOWED_SIZES),
                     "description": "图片尺寸，默认 2K"},
        },
        "required": ["description"],
        "additionalProperties": False,
    }

    async def run(self, arguments: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        description = str(arguments.get("description") or "").strip()
        if not description:
            return ToolResult(ok=False, output="[参数错误] 请提供立绘描述（description）")
        try:
            assets, cid, sid, state = _need_galgame(ctx)
            model_cfg = _select_model(ctx.services)
            size = _norm_size(arguments)
        except RuntimeError as exc:
            return ToolResult(ok=False, output="[工具错误] %s" % exc)

        # 历史立绘参考（第二张起强制至少一张）
        sprite_recs, auto_ref = _select_sprite_refs(arguments, assets, cid, state)

        ref_images: List[Tuple[str, str]] = []
        resolver = ReadImageTool()
        for rec in sprite_recs:
            target, _u = resolver._resolve(rec.url, ctx, ctx.services)
            if target is None or not target.is_file():
                continue
            raw = target.read_bytes()
            if 0 < len(raw) <= _AVATAR_MAX_BYTES:
                media = _EXT_MEDIA.get(target.suffix.lower(), "image/png")
                import base64 as _b64
                ref_images.append((media, _b64.b64encode(raw).decode("ascii")))

        ref_notes = []
        if sprite_recs:
            ref_notes.append("已参考 %d 张历史立绘%s"
                             % (len(sprite_recs), "（自动补选）" if auto_ref else ""))

        # 角色卡头像（首张立绘时是长相一致的主要依据；路径解析复用安全边界）
        avatar_note = ""
        if arguments.get("use_avatar", True):
            character = (ctx.services or {}).get("galgame_character")
            avatar = getattr(character, "avatar", "") or ""
            if avatar:
                target, _url = resolver._resolve(avatar, ctx, ctx.services)
                if target is not None and target.is_file():
                    raw = target.read_bytes()
                    if 0 < len(raw) <= _AVATAR_MAX_BYTES:
                        media = _EXT_MEDIA.get(target.suffix.lower(), "image/png")
                        import base64 as _b64
                        ref_images.append((media, _b64.b64encode(raw).decode("ascii")))
                        avatar_note = "已使用角色头像作为参考图。"

        ref_note = (" ".join(ref_notes + ([avatar_note] if avatar_note else []))
                    or "无可用参考图，按文生图处理。")

        prompt = (
            "%s。galgame 视觉小说角色全身立绘，单人，正面朝向观众，"
            "全身完整入镜（从头到脚），居中站立，纯色色度键绿幕背景（纯绿色 "
            "#00FF00，无阴影渐变、无杂物、无地面道具），光线均匀柔和，"
            "精致游戏立绘，细腻高清" % description
        )
        try:
            content, ext, _media = await ark_generate(
                model_cfg, prompt, size, ref_images=ref_images or None
            )
            rec = assets.add_sprite(cid, content, ext, description, prompt)
        except Exception as exc:
            logger.exception("Galgame 立绘生成失败")
            return ToolResult(
                ok=False, output="[工具错误] 立绘生成失败: %s: %s"
                % (type(exc).__name__, exc)
            )
        auto = False
        if not state.get("sprite"):
            state["sprite"] = {"id": rec.id, "url": rec.url}
            auto = True
        note = "已自动放置到画面上。" if auto else "需再调用 galgame_set_scene 才会切换到新立绘。"
        output = _asset_line(rec, "[立绘已生成]") + "\n立绘已成功生成，%s%s" % (ref_note, note)
        return ToolResult(
            output=output,
            meta={"galgame_asset": rec.to_dict(), "galgame_kind": "sprite",
                  "galgame_auto_set": auto},
        )


_NOTE_SCHEMA = {
    "type": "object",
    "properties": {
        "t": {"type": "number", "minimum": 0,
              "description": "起始时间，单位为拍（0=小节起点，0.5=反拍，1=第二拍）"},
        "d": {"type": "number", "exclusiveMinimum": 0,
              "description": "时值，单位为拍（1=四分音符，0.5=八分，2=二分）"},
        "n": {"type": "integer", "minimum": 0, "maximum": 127,
              "description": "MIDI 音高（60=中央C；鼓轨中为鼓编号：36 底鼓/38 军鼓/42 闭镲/46 开镲/49 强音镲）"},
        "v": {"type": "integer", "minimum": 1, "maximum": 127,
              "description": "力度，默认 64"},
    },
    "required": ["t", "d", "n"],
    "additionalProperties": False,
}
_TRACK_SCHEMA = {
    "type": "object",
    "properties": {
        "program": {"type": "integer", "minimum": 0, "maximum": 127,
                    "description": "GM 音色编号（0 原声钢琴/4 电钢琴/24 尼龙吉他/33 指弹贝斯/40 小提琴/48 弦乐组/61 法国号/73 长笛/80 方波合成/88 新世纪垫）；drums=true 时不要填"},
        "drums": {"type": "boolean",
                  "description": "是否为打击乐轨（自动走鼓通道，鼓音符用 n=36/38/42/46/49）"},
        "volume": {"type": "integer", "minimum": 0, "maximum": 127,
                   "description": "音量，默认 70"},
        "pan": {"type": "integer", "minimum": 0, "maximum": 127,
                "description": "声像 0 左/64 中/127 右，默认 64"},
        "reverb": {"type": "integer", "minimum": 0, "maximum": 127,
                   "description": "混响量，默认 40"},
        "notes": {"type": "array", "items": _NOTE_SCHEMA,
                  "description": "本轨音符"},
    },
    "required": ["notes"],
    "additionalProperties": False,
}


class GalgameComposeMusicTool(Tool):
    name = "galgame_compose_music"
    description = (
        "Galgame 模式专用：根据当前场景氛围创作一段 MIDI 背景音乐（BGM），"
        "系统会在浏览器中用 GS 合成器循环播放。做法：\n"
        "1) 先定调与音阶（如 C 大调：可用音 60/62/64/65/67/69/71/72；"
        "A 小调：57/59/60/62/64/65/67/69；五声音阶更不容易出错）；\n"
        "2) 用 3~5 条轨：旋律/副旋律（program 选 0/4/40/73 等）、"
        "和声或琶音（program 0/24/48，可每拍/每半拍一个音铺满）、"
        "低音（program 32/33，每小节根音）、鼓（drums=true，"
        "底鼓 36 在第 1、3 拍、军鼓 38 在第 2、4 拍、镲 42 每半拍）；\n"
        "3) 所有音符 t/d 必须对齐节拍网格（0.5 的整数倍）；\n"
        "4) 结尾落回主和弦，让循环首尾衔接自然（如 C 大调结束在 C-E-G：60/64/67）；\n"
        "5) 长度 8~12 小节、约 30~45 秒，总音符数不超过 600。\n"
        "description 用中文写明情绪与用途（如『日常·温馨·厨房』）。"
        "首次创作的曲目自动开始播放；之后的曲目需再调用 "
        "galgame_set_scene(music_id=…) 才会切换。不要每轮重新创作。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "description": {"type": "string", "description": "情绪/风格/用途描述"},
            "bpm": {"type": "integer", "minimum": 40, "maximum": 200,
                    "description": "速度，安静场景 60~80，紧张场景 100~140，默认 76"},
            "bars": {"type": "integer", "minimum": 1, "maximum": 16,
                     "description": "小节数，默认 8"},
            "beats_per_bar": {"type": "integer", "minimum": 2, "maximum": 8,
                              "description": "每小节拍数，默认 4"},
            "tracks": {"type": "array", "items": _TRACK_SCHEMA,
                       "description": "音轨（3~5 条），通道自动分配"},
        },
        "required": ["description", "tracks"],
        "additionalProperties": False,
    }

    async def run(self, arguments: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        description = str(arguments.get("description") or "").strip()
        if not description:
            return ToolResult(ok=False, output="[参数错误] 请提供音乐的情绪/用途描述（description）")
        if not isinstance(arguments.get("tracks"), list):
            return ToolResult(ok=False, output="[参数错误] 请提供 tracks 音轨数组")
        try:
            assets, _cid, sid, state = _need_galgame(ctx)
        except RuntimeError as exc:
            return ToolResult(ok=False, output="[工具错误] %s" % exc)

        spec = {
            "bpm": arguments.get("bpm", 76),
            "bars": arguments.get("bars", 8),
            "beats_per_bar": arguments.get("beats_per_bar", 4),
            "tracks": arguments.get("tracks"),
        }
        try:
            data = render_midi(spec)
        except MidiSpecError as exc:
            # 把具体约束反馈给模型，便于其修正参数重试
            return ToolResult(ok=False, output="[参数错误] 音乐结构不合法：%s" % exc)
        spec_json = json.dumps(spec, ensure_ascii=False)
        try:
            rec = assets.add_music(sid, data, ".mid", description, spec_json)
        except Exception as exc:
            logger.exception("Galgame 音乐保存失败")
            return ToolResult(
                ok=False, output="[工具错误] 音乐保存失败: %s: %s"
                % (type(exc).__name__, exc)
            )
        auto = False
        if not state.get("music"):
            state["music"] = {"id": rec.id, "url": rec.url}
            auto = True
        note = "已自动开始循环播放。" if auto else "需再调用 galgame_set_scene(music_id=…) 才会切换。"
        output = _asset_line(rec, "[音乐已生成]") + "\n音乐已成功创作，%s" % note
        return ToolResult(
            output=output,
            meta={"galgame_asset": rec.to_dict(), "galgame_kind": "music",
                  "galgame_auto_set": auto},
        )


class GalgameListAssetsTool(Tool):
    name = "galgame_list_assets"
    description = (
        "Galgame 模式专用：查看已有资产清单。kind=sprites 查看本角色的全部立绘"
        "（跨会话复用）；kind=backgrounds 查看本会话的全部背景；"
        "kind=music 查看本会话的全部背景音乐。"
        "需要切换立绘/背景/音乐前先调用本工具，有合适的直接 galgame_set_scene，"
        "没有再生成新的。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["sprites", "backgrounds", "music"],
                     "description": "资产类型"},
        },
        "required": ["kind"],
        "additionalProperties": False,
    }

    async def run(self, arguments: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        kind = str(arguments.get("kind") or "").strip()
        if kind not in ("sprites", "backgrounds", "music"):
            return ToolResult(
                ok=False,
                output="[参数错误] kind 只能是 sprites、backgrounds 或 music",
            )
        try:
            assets, cid, sid, _state = _need_galgame(ctx)
        except RuntimeError as exc:
            return ToolResult(ok=False, output="[工具错误] %s" % exc)
        if kind == "sprites":
            records = assets.list_sprites(cid)
            label, what, unit = "本角色已有立绘", "立绘", "张"
        elif kind == "backgrounds":
            records = assets.list_backgrounds(sid)
            label, what, unit = "本会话已有背景", "背景", "张"
        else:
            records = assets.list_music(sid)
            label, what, unit = "本会话已有背景音乐", "音乐", "条"
        if not records:
            return ToolResult(
                output="%s清单为空：没有任何%s，需要时请直接调用生成工具。"
                % (label, what)
            )
        lines = ["%s（共 %d %s），按生成时间排列："
                 % (label, len(records), unit)]
        for rec in records:
            lines.append("- id=%s | %s | %s"
                         % (rec.id, rec.name or "(无描述)", rec.url))
        lines.append("需要切换时，把对应 id 传给 galgame_set_scene。")
        return ToolResult(output="\n".join(lines))


class GalgameSetSceneTool(Tool):
    name = "galgame_set_scene"
    description = (
        "Galgame 模式专用：切换当前画面的背景、人物立绘和/或背景音乐。"
        "参数都可选：只传其中一个就只切换一项。"
        "id 由 galgame_list_assets 或生成工具的 [资产信息] 给出。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "background_id": {"type": "string", "description": "背景资产 id"},
            "sprite_id": {"type": "string", "description": "立绘资产 id"},
            "music_id": {"type": "string", "description": "背景音乐资产 id"},
        },
        "additionalProperties": False,
    }

    async def run(self, arguments: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        bg_id = str(arguments.get("background_id") or "").strip()
        sp_id = str(arguments.get("sprite_id") or "").strip()
        mu_id = str(arguments.get("music_id") or "").strip()
        if not bg_id and not sp_id and not mu_id:
            return ToolResult(
                ok=False,
                output="[参数错误] 请至少提供 background_id、sprite_id 或 music_id",
            )
        try:
            assets, cid, sid, state = _need_galgame(ctx)
        except RuntimeError as exc:
            return ToolResult(ok=False, output="[工具错误] %s" % exc)

        switched = {}
        if bg_id:
            rec = assets.get_background(sid, bg_id)
            if rec is None:
                return ToolResult(
                    ok=False, output="[参数错误] 背景 id 不存在（先 galgame_list_assets 确认）: %s" % bg_id
                )
            state["background"] = {"id": rec.id, "url": rec.url}
            switched["background"] = rec.to_dict()
        if sp_id:
            rec = assets.get_sprite(cid, sp_id)
            if rec is None:
                return ToolResult(
                    ok=False, output="[参数错误] 立绘 id 不存在（先 galgame_list_assets 确认）: %s" % sp_id
                )
            state["sprite"] = {"id": rec.id, "url": rec.url}
            switched["sprite"] = rec.to_dict()
        if mu_id:
            rec = assets.get_music(sid, mu_id)
            if rec is None:
                return ToolResult(
                    ok=False, output="[参数错误] 音乐 id 不存在（先 galgame_list_assets(kind=\"music\") 确认）: %s" % mu_id
                )
            state["music"] = {"id": rec.id, "url": rec.url}
            switched["music"] = rec.to_dict()
        payload = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
        output = "[场景切换] %s\n画面已更新，可继续演出。" % payload
        return ToolResult(output=output, meta={"galgame_scene": dict(state)})


class GalgameChoiceTool(Tool):
    name = "galgame_choice"
    description = (
        "Galgame 模式专用：每轮叙事与对白结束后调用，给出 2 到 4 个推进剧情的"
        "选项。options 为简短具体的选项文案数组；系统会自动附带自由输入入口，"
        "不要自己造“自由输入”选项。调用后等待用户选择，用户选择的文本即为工具结果，"
        "据此继续演出。"
    )
    interactive = True
    parameters = {
        "type": "object",
        "properties": {
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 2,
                "maxItems": 4,
                "description": "2 到 4 个选项文案",
            }
        },
        "required": ["options"],
        "additionalProperties": False,
    }

    async def run(self, arguments: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        raw_options = arguments.get("options") or []
        options = [str(o).strip() for o in raw_options if str(o).strip()]
        if len(options) < 2:
            return ToolResult(ok=False, output="[参数错误] 至少提供 2 个有效选项")
        options = options[:4]
        services = ctx.services or {}
        manager = services.get("galgame_choice_manager")
        uid = services.get("uid") or ""
        if manager is None or not ctx.run_id or not uid:
            return ToolResult(
                ok=False, output="[工具错误] 当前会话不支持选项交互"
            )
        # 阻塞可能持续数分钟，且一个 run 内会循环多段：先把截至目前的全部
        # 对话与场景态落盘，否则刷新后只有第一条用户消息、AI 回复全部丢失
        try:
            gstore = services.get("galgame_store")
            gsess = services.get("galgame_session")
            ov = services.get("galgame_tts_overlay")
            if gstore is not None and gsess is not None:
                if ov:
                    merged = dict(ov)
                    merged.update(getattr(gsess, "galgame_tts", None) or {})
                    gsess.galgame_tts = merged
                gstore.save(gsess)
        except Exception:
            logger.exception("Galgame 选项前保存失败")
        try:
            selected = await manager.wait_for(ctx.run_id, uid)
        except ChoiceTimeout as exc:
            return ToolResult(ok=False, output="[工具错误] %s" % exc)
        except Exception as exc:  # 运行被取消等
            logger.exception("Galgame 选项等待异常")
            return ToolResult(
                ok=False, output="[工具错误] 等待选项失败: %s: %s"
                % (type(exc).__name__, exc)
            )
        selected = (selected or "").strip()
        output = "用户选择了：「%s」\n请严格依据该选择与当前剧情继续演出。" % selected
        return ToolResult(
            output=output,
            meta={"galgame_choice": {"selected": selected, "options": options}},
        )
