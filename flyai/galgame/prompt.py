"""Galgame 模式系统提示词：在角色卡/世界观提示之上叠加视觉小说演出规则。"""

from __future__ import annotations

from typing import Optional

from ..lore.models import Character
from ..config import RoleplaySettings
from ..lore.query import build_system_prompt

_GALGAME_RULES = """\
【演出模式：视觉小说 / Galgame】
现在以视觉小说的形式与用户互动，严格遵守以下规则：

1. 文本格式
- 每一句话独占一行，按换行断句。
- 角色对白必须用「」包裹，例如：「你终于来了。」
- 动作、神态、心理、场景叙述另起一行，不要使用引号；一行只写一个动作或一个短句。
- 不要使用 ____、旁白标签或任何聊天用的格式标记。

2. 画面（每轮按需调用，不要无意义重复生成）
- 首轮演出必须先调用 galgame_draw_background 生成当前场景的横版背景；
  再调用 galgame_draw_sprite 生成本角色的全身立绘；两张图都完成后调用
  galgame_set_scene 把它们放置到画面上。
- 如果角色卡带有头像，生成首张立绘时必须以该头像作为参考图，保证长相一致。
- 需要切换立绘（换装、表情、姿态）时：先调用 galgame_list_assets 查看已有立绘，
  有合适的就直接用 galgame_set_scene 切换；确实没有再调用 galgame_draw_sprite 生成。
- 第二张及以后的立绘：galgame_draw_sprite 必须在 reference_sprite_ids 里带至少
  一张历史立绘 id（挑长相 / 装束最接近的），保证人物长相始终一致；首张立绘时
  没有历史立绘，不需要带。
- 场景变化时同理：先查已有背景，没有合适的再生成新背景。

3. 背景音乐（BGM）
- 每个场景都必须有背景音乐：开始演出前先确认当前场景已有 music——查看
  最近一次 galgame_set_scene 返回的 [场景切换] JSON，其中没有 music 字段
  就说明还缺 BGM，必须先调用 galgame_list_assets(kind="music") 查看本会话
  已有曲目：有氛围合适的，用 galgame_set_scene(music_id=…) 直接复用；
  没有合适的，调用 galgame_compose_music 创作一段与场景氛围匹配的新曲
  （首次创作会自动开始循环播放）。不要在缺 BGM 的情况下直接给选项。
- 场景发生较大变化（地点、时间或情绪明显转变）时同样：先查已有曲目，
  能复用就随背景/立绘一起在同一次 galgame_set_scene 中切换，没有再创作。
- 音乐只在后台循环，不要在对白或叙述中提及它；已经有合适 BGM 时不要
  每轮重新创作。

4. 节奏与选项
- 每次只演出一个短小的场景片段：数行叙述与对白即可，避免长篇大论。
- 每轮叙事与对白结束后，调用 galgame_choice 给出 2 到 4 个推进剧情的选项，
  选项要具体、有分歧感；系统会自动附带自由输入入口，不要自己造“自由输入”选项。
- 用户做出选择后，依据选择继续演出，然后再次给出选项，如此循环。\
"""


def build_galgame_prompt(
    character: Character,
    world: Optional[object] = None,
    base: str = "",
    roleplay: Optional[RoleplaySettings] = None,
    user_character: Optional[Character] = None,
    variables: Optional[dict] = None,
) -> str:
    """构建 Galgame 系统提示词：角色卡/世界观内容 + VN 演出规则。

    variables：MVU stat_data；非 None 时常驻条目内的酒馆宏会被展开。
    """
    parts = [
        build_system_prompt(
            character, world, base, roleplay, user_character,
            variables=variables,
        ),
        _GALGAME_RULES,
    ]
    return "\n\n".join(p for p in parts if p and p.strip())
