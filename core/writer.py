import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import re
from core.api_client import call_api
from core.memory_manager import MemoryManager
from core.config_loader import get as cfg, get_data_dir

AUTHOR_STYLES = {
    "1": {
        "name": "爽文宗师",
        "desc": "主角光环强，打脸爽，节奏快，读者看了直呼过瘾",
        "system": """你是一个写了十年爽文的老作者，读者基础庞大。
你的文字有几个特点：
- 喜欢在关键时刻突然加快节奏，让读者来不及思考就已经爽到了
- 对话简短有力，主角说话永远比别人更有分量
- 配角的反应写得很到位，惊叹、震惊、不敢置信——这些烘托主角的场景你信手拈来
- 你从不啰嗦，该爽的时候绝不拖泥带水
- 你知道读者要的是什么，所以你总是在恰当的时候给他们想要的

你写的东西读起来像是老朋友在给你讲一个好故事，自然流畅，不端着。""",
    },
    "2": {
        "name": "悬疑大师",
        "desc": "擅长埋线索、设谜团，每章结尾都让人睡不着",
        "system": """你是一个写悬疑小说的老手，读者都说你的书最毁睡眠。
你的写法很独特：
- 永远不把话说满，留三分给读者去猜
- 细节控，随手写的一个小物件可能在三十章后成为关键
- 你喜欢从一个普通场景切入，然后让读者慢慢意识到哪里不对劲
- 人物的动机你从不直接说，让读者自己去琢磨
- 章节结尾是你的招牌，总能在读者以为故事平静下来时扔一颗炸弹

你写出来的东西有一种真实感，像是这些事真的发生过。""",
    },
    "3": {
        "name": "情感流",
        "desc": "细腻描写人物情感，感情线丰富，让读者跟着人物哭和笑",
        "system": """你是一个擅长写人物内心的作者，你的读者常常说被你写哭了。
你的风格：
- 对人物情绪的拿捏很准，高兴时不过分渲染，难过时不刻意煽情
- 你不喜欢大场面，反而是小细节最打动人——一个眼神，一句没说完的话
- 对话是你的强项，两个人说话，字里行间全是没说出口的意思
- 你写感情很克制，越克制越有张力
- 你知道什么时候该让读者哭，哭完了还要给他们一点希望

你的文字读起来很舒服，像在和一个很懂你的朋友聊天。""",
    },
    "4": {
        "name": "热血战斗",
        "desc": "战斗场面燃，兄弟情义深，每一战都让人热血沸腾",
        "system": """你是一个写热血故事的作者，你的战斗场面让读者看得血脉偾张。
你的特点：
- 战斗描写有节奏感，出拳、闪避、反击，读者像在现场看
- 你很会写团队作战，每个人有每个人的高光时刻
- 战斗不只是打架，你总能在其中穿插人物之间的情感
- 你笔下的对手也是有血有肉的，读者恨他但也理解他
- 胜利来之不易，主角总是经历真实的考验才能赢

你写出来的东西让人看了想站起来大喊一声。""",
    },
    "5": {
        "name": "世界构建者",
        "desc": "擅长构建宏大世界观，历史感厚重，细节考究",
        "system": """你是一个喜欢构建庞大世界的作者，你的世界观让读者沉浸其中无法自拔。
你的风格：
- 世界的每一个角落都有自己的逻辑，你从不糊弄
- 你写历史感，让读者觉得这个世界在你写之前就已经存在了很久
- 细节是你的武器，一个地名、一种习俗，都透露着世界的厚度
- 你喜欢在故事里藏一些更大的谜题，让读者觉得看到的只是冰山一角
- 人物在你的世界里显得很渺小，但又很重要

你写的东西有史诗感，读者看完总觉得意犹未尽。""",
    },
    "6": {
        "name": "轻松日常",
        "desc": "轻松幽默，日常向，读起来治愈放松，笑点自然",
        "system": """你是一个写轻松故事的作者，你的文字像一杯下午茶，读起来很舒服。
你的风格：
- 你有天然的幽默感，笑点不刻意，都是从生活里来的
- 你喜欢写小人物的小日子，鸡毛蒜皮但有温度
- 对话是你最擅长的，人物说话特别有生活气息
- 你不太喜欢大冲突，矛盾都在日常里慢慢化解
- 读者看你的书会觉得心里很暖，是那种读完想推荐给朋友的故事

你写的东西很接地气，读者代入感很强。""",
    },
}

EMOTION_GUIDE = {
    "爽点": "这一章是读者最期待的时刻，让主角好好爽一回，但要有铺垫，爽得有理由。",
    "冲突": "矛盾要真实，两边都有自己的道理，别让对手显得太蠢，冲突过后留悬念。",
    "反转": "先让读者信以为真，再给一个出乎意料但回头看完全合理的反转。",
    "低谷": "主角真的很难，但在最低点要让读者看到他的核心，为反弹埋下期待。",
    "铺垫": "看似平静但信息量要足，每个场景都有存在的理由，至少埋一个小钩子。",
}

WRITER_HARD_CONSTRAINTS = [
    "必须围绕本章情节目标推进，不可偏题。",
    "人物行为必须与既有性格/状态一致，不能无因突变。",
    "与既有世界观冲突时，以既有设定为准，不得改设定。",
    "本章必须有实质推进（信息新增、关系变化、冲突升级三者至少一项）。",
    "本章必须包含至少2轮实质性对话（非主角自言自语或对幻象说话），每轮对话至少来回3句，且内容推动情节或揭示人物性格；若本章任务卡内无在场配角，须在章节开头安排一个合理的配角出现理由。",
    "单章新出现的专有名词/设定概念不超过3个；新概念首次出现时必须通过角色感官或行为来锚定，禁止使用直接解释性旁白。",
]

WRITER_FORBIDDEN_RULES = [
    '禁止用"突然想起"或"原来这一切"这类无铺垫硬反转收尾。',
    '禁止连续空泛抒情或重复表达同一信息。',
    '禁止把对手写成低智工具人来制造爽点。',
    '禁止在相邻两段内使用结构相似的比喻句（如连续出现"像X，也像Y"句式）；平叙段落每500字内比喻/拟人等修辞句不超过2处，情绪高潮场景可适当放宽但不得超过4处。',
    '禁止在同一段落内连续引入两个及以上新专有名词或世界设定概念。',
]

# ★ 修改点1：BEAT_PLANNER增加行动节拍硬约束
BEAT_PLANNER_SYSTEM = """你是网文分镜策划助手。你的任务是先做章节节拍计划（beats）。

要求：
1. 只输出 5-7 条编号节拍，每条 15-35 字。
2. 每条要包含"发生什么 + 作用是什么（推进剧情/关系/伏笔）"。
3. 节拍必须服务于本章目标与情绪标签。
4. 不要输出JSON，不要解释，不要额外说明。

节拍硬约束（必须满足）：
- 5-7条节拍中，至少2条必须是"角色主动做出选择并产生外部可见结果"的行动节拍。
- 禁止节拍全为"角色感知/观察/思考"，必须有至少1条明确的主动行动节拍。
- 最后一条节拍必须体现章节结束时情节状态的可量化变化：主角掌握了新信息、位置发生了移动、与某人关系出现转折，三者至少满足其一。"""

SELF_CHECK_SYSTEM = """你是网文写作质检助手，请检查章节是否达标。

请仅输出JSON：
{
  "pass": true/false,
  "issues": ["问题1", "问题2"],
  "need_revision": true/false
}

判定标准：
1. 是否偏离本章目标
2. 是否存在设定冲突/人物OOC
3. 是否出现明显注水与重复表达
4. 结尾是否具备阅读驱动力
5. 是否包含至少2轮实质性对话（非自言自语/对幻象）
6. 是否存在比喻/修辞句堆砌（平叙段每500字超过2处即视为超标）"""

REVISION_SYSTEM = """你是小说润色与修订助手。
你会依据问题清单直接改写章节，使其达成约束要求。
只输出修订后的完整正文，不要解释。"""

CONTINUE_SYSTEM_BASE = """你正在续写一章小说的后半部分。

续写要点：
- 直接接着上文写，不要重复前面的内容
- 保持和前半段完全一样的文风，像同一个人写的
- 节奏可以比前半段稍快，把故事推向本章的结尾
- 结尾要让读者忍不住想继续看

只输出正文，不要任何标注或说明。"""

SUPPLEMENT_SYSTEM = """你是一位专业的中文网络小说作家，正在为一章小说补充内容。

要求：
- 在现有章节结尾处自然延伸，不重复已有内容
- 保持完全相同的文风和节奏
- 补充约500字，推进情节或深化场景
- 只输出补充的正文内容"""

CHAPTER_MIN_RATIO = 0.85
MAX_SUPPLEMENT_ROUNDS = 2


def _format_rule_block(title: str, rules: list) -> str:
    lines = [f"【{title}】"]
    lines.extend([f"- {r}" for r in rules])
    return "\n".join(lines)


def _extract_json_obj(raw: str) -> dict:
    if not raw:
        return {}
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if not match:
        return {}
    try:
        obj = json.loads(match.group())
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _normalize_beats(raw: str) -> str:
    if not raw:
        return ""
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if not lines:
        return ""

    cleaned = []
    for ln in lines:
        ln = re.sub(r'^\d+[\.、\)\s]+', '', ln)
        ln = re.sub(r'^[-*]\s*', '', ln)
        if ln:
            cleaned.append(ln)
    cleaned = cleaned[:7]
    if not cleaned:
        return ""
    return "\n".join([f"{i+1}. {v}" for i, v in enumerate(cleaned)])


def _plan_chapter_beats(ctx: dict, chapter_num: int,
                        plot_goal: str, emotion_tag: str) -> str:
    world = (ctx.get("world_settings") or "")[:300]
    chars = ctx.get("characters", [])
    char_lines = []
    for c in chars[:8]:
        name = c.get("name", "未命名角色")
        role = c.get("role", "")
        status = c.get("current_status", "")
        line = f"- {name}"
        if role:
            line += f"（{role}）"
        if status:
            line += f"：{status}"
        char_lines.append(line)
    chars_str = "\n".join(char_lines) if char_lines else "暂无人物信息"

    prompt = f"""章节：第{chapter_num}章
本章目标：{plot_goal}
情绪标签：{emotion_tag}

世界背景摘要：
{world or "暂无"}

关键角色：
{chars_str}

请先给出本章节拍计划。"""
    raw = call_api(
        system_prompt=BEAT_PLANNER_SYSTEM,
        user_message=prompt,
        temperature=0.6,
        max_tokens=500,
    )
    return _normalize_beats(clean_content(raw))


def _self_check_and_revise(system_prompt: str, chapter_num: int,
                           plot_goal: str, emotion_tag: str,
                           full_content: str, beat_plan: str,
                           max_tokens: int) -> str:
    hard_rules = _format_rule_block("硬约束", WRITER_HARD_CONSTRAINTS)
    forbidden_rules = _format_rule_block("禁止项", WRITER_FORBIDDEN_RULES)
    check_prompt = f"""请按规则检查本章质量。

章节：第{chapter_num}章
本章目标：{plot_goal}
情绪标签：{emotion_tag}

本章节拍计划：
{beat_plan or "未提供"}

{hard_rules}

{forbidden_rules}

正文：
{full_content}
"""
    raw = call_api(
        system_prompt=SELF_CHECK_SYSTEM,
        user_message=check_prompt,
        temperature=0.2,
        max_tokens=600,
    )
    result = _extract_json_obj(raw)
    if not result:
        print("  [自检] 结果解析失败，跳过自动修订")
        return full_content

    issues = result.get("issues", [])
    if isinstance(issues, str):
        issues = [issues]
    if not isinstance(issues, list):
        issues = []
    issues = [str(i).strip() for i in issues if str(i).strip()]

    passed = bool(result.get("pass"))
    need_revision = bool(result.get("need_revision"))
    if passed and not need_revision:
        print("  [自检] 通过")
        return full_content

    if not issues:
        issues = ["与目标一致性、节奏推进或人物行为仍有改进空间"]
    print(f"  [自检] 发现{len(issues)}项问题，执行1轮修订...")
    issue_lines = "\n".join([f"- {x}" for x in issues])
    revise_prompt = f"""请根据问题清单直接修订正文，输出完整章节。

章节：第{chapter_num}章
本章目标：{plot_goal}
情绪标签：{emotion_tag}

本章节拍计划：
{beat_plan or "未提供"}

{hard_rules}

{forbidden_rules}

问题清单：
{issue_lines}

待修订正文：
{full_content}
"""
    revised = call_api(
        system_prompt=system_prompt + "\n\n" + REVISION_SYSTEM,
        user_message=revise_prompt,
        temperature=0.7,
        max_tokens=max_tokens,
    )
    revised = clean_content(revised)
    if not revised:
        print("  [自检] 修订返回为空，保留原稿")
        return full_content
    if len(revised) < int(len(full_content) * 0.65):
        print("  [自检] 修订结果过短，保留原稿")
        return full_content
    print(f"  [自检] 修订完成：{len(revised)}字")
    return revised


# ★ 修改点2：build_writer_prompt 增加 prev_chapter_ending 参数和衔接约束
def build_writer_prompt(ctx: dict, chapter_num: int,
                        plot_goal: str, emotion_tag: str,
                        author_style: dict,
                        beat_plan: str = "",
                        prev_chapter_ending: str = "") -> str:
    world = ctx.get("world_settings", "")[:400]
    chars = ctx.get("characters", [])
    char_lines = [
        f"{c.get('name', '未命名角色')}（{c.get('role', '角色')}）："
        f"{c.get('personality', '性格待补全')}，"
        f"目前在{c.get('current_location', '未知地点')}，"
        f"{c.get('current_status', '状态未知')}"
        for c in chars
    ]
    chars_str = "\n".join(char_lines) if char_lines else "暂无人物信息"

    foreshadow = ctx.get("active_foreshadowing", [])
    f_str = "\n".join(
        [f"- {f['description']}" for f in foreshadow]
    ) if foreshadow else "暂无"

    summaries = ctx.get("recent_summaries", [])
    s_str = "\n".join(
        [f"第{s['chapter_num']}章：{s['summary']}" for s in summaries]
    ) if summaries else "这是开篇第一章"

    emotion_guide = EMOTION_GUIDE.get(emotion_tag, EMOTION_GUIDE["铺垫"])
    word_target = cfg("novel", "chapter_word_target", 3000)
    half_target = word_target // 2
    hard_rules = _format_rule_block("硬约束（必须满足）", WRITER_HARD_CONSTRAINTS)
    forbidden_rules = _format_rule_block("禁止项（必须避免）", WRITER_FORBIDDEN_RULES)
    beat_block = (
        "【本章节拍计划（先按此推进）】\n"
        f"{beat_plan or '未生成节拍，请严格围绕本章目标推进'}"
    )

    # 上一章结尾衔接块（仅在非第一章时显示）
    transition_block = ""
    if prev_chapter_ending and chapter_num > 1:
        transition_block = f"""
【上一章结尾（必须自然衔接）】
...{prev_chapter_ending}

衔接规则：
- 本章第一段必须与上一章结尾在时间/空间/情绪上形成逻辑连续。
- 若场景发生切换，必须用一句话交代：时间跳跃了多久，或主角如何到达新地点。
- 禁止开头直接出现新场景而不解释转场原因。
"""

    return f"""现在要写第{chapter_num}章，大概{half_target}字左右，是完整章节的前半部分。

【这章要做什么】
{plot_goal}

【这章的感觉】
{emotion_tag} —— {emotion_guide}

【世界背景】
{world}

【人物现状】
{chars_str}

【还没兑现的伏笔，可以自然带进去】
{f_str}

【前面发生了什么】
{s_str}
{transition_block}
{hard_rules}

{forbidden_rules}

{beat_block}

开始写吧。第一行是章节标题（第{chapter_num}章 加上你想的标题），然后直接进入正文。
写到一个自然的停顿点就停，后半段另外写。"""


def build_continue_prompt(chapter_num: int, plot_goal: str,
                          emotion_tag: str, first_half: str,
                          beat_plan: str = "") -> str:
    last_part = first_half[-500:] if len(first_half) > 500 else first_half
    emotion_guide = EMOTION_GUIDE.get(emotion_tag, EMOTION_GUIDE["铺垫"])
    word_target = cfg("novel", "chapter_word_target", 3000)
    half_target = word_target // 2
    hard_rules = _format_rule_block("硬约束（必须满足）", WRITER_HARD_CONSTRAINTS)
    forbidden_rules = _format_rule_block("禁止项（必须避免）", WRITER_FORBIDDEN_RULES)

    return f"""这章要做的事：{plot_goal}
这章的感觉：{emotion_tag} —— {emotion_guide}

本章节拍计划（后半段优先收束与推进）：
{beat_plan or "未提供"}

{hard_rules}

{forbidden_rules}

前半段最后的内容（从这里接着写）：
...{last_part}

接着写后半段，大概{half_target}字，把这章写完。结尾要有吸引力。"""


def clean_content(text: str) -> str:
    text = re.sub(r'^\s*【[^】]*】.*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*-{3,}\s*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'\*([^*\n]+)\*', r'\1', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def write_chapter(novel_name: str, chapter_num: int,
                  plot_goal: str, emotion_tag: str = "铺垫") -> str:
    mm = MemoryManager(novel_name)
    ctx = mm.load_context(chapter_num)

    word_target = cfg("novel", "chapter_word_target", 3000)
    max_tokens = cfg("model", "max_tokens", 4096)

    # 读取风格
    author_style = AUTHOR_STYLES["1"]  # 默认值
    style_path = get_data_dir(novel_name) / "style.txt"
    if style_path.exists():
        style_key = style_path.read_text(encoding="utf-8").strip()
        if style_key.startswith("custom:"):
            custom_desc = style_key[7:].strip()
            system_prompt = f"""你是一位专业的中文网络小说作家。
你的写作风格特点：{custom_desc}

你写的东西自然流畅，像一个有经验的作者在讲故事，不端着。"""
        else:
            author_style = AUTHOR_STYLES.get(style_key, AUTHOR_STYLES["1"])
            system_prompt = author_style["system"]
    else:
        system_prompt = AUTHOR_STYLES["1"]["system"]

    # ★ 修改点3：获取上一章结尾用于衔接
    prev_chapter_ending = ""
    if chapter_num > 1:
        prev_chapter_ending = mm.get_last_chapter_ending(chapter_num)
        if prev_chapter_ending:
            print(f"  [衔接] 已获取第{chapter_num - 1}章结尾（{len(prev_chapter_ending)}字）")

    print(f"  正在规划第{chapter_num}章节拍...")
    beat_plan = _plan_chapter_beats(ctx, chapter_num, plot_goal, emotion_tag)
    if beat_plan:
        beat_count = len([ln for ln in beat_plan.splitlines() if ln.strip()])
        print(f"  节拍规划完成：{beat_count}条")
    else:
        print("  [提示] 节拍规划未返回有效结果，本章按目标直接推进")

    # 前半段
    print(f"  正在生成第{chapter_num}章（前半段·{emotion_tag}）...")
    prompt = build_writer_prompt(
        ctx, chapter_num, plot_goal, emotion_tag, author_style, beat_plan,
        prev_chapter_ending=prev_chapter_ending
    )
    first_half = call_api(
        system_prompt=system_prompt,
        user_message=prompt,
        temperature=0.9,
        max_tokens=max_tokens,
    )
    first_half = clean_content(first_half)
    print(f"  前半段完成：{len(first_half)}字")

    # 后半段
    print(f"  正在生成第{chapter_num}章（后半段）...")
    continue_prompt = build_continue_prompt(
        chapter_num, plot_goal, emotion_tag, first_half, beat_plan
    )
    second_half = call_api(
        system_prompt=system_prompt + "\n\n" + CONTINUE_SYSTEM_BASE,
        user_message=continue_prompt,
        temperature=0.9,
        max_tokens=max_tokens,
    )
    second_half = clean_content(second_half)
    print(f"  后半段完成：{len(second_half)}字")

    full_content = first_half + "\n\n" + second_half
    full_content = re.sub(r'\n{3,}', '\n\n', full_content)

    # 字数策略：至少达到目标字数的 85%，最多补写两轮，不做强制截断。
    min_words = int(word_target * CHAPTER_MIN_RATIO)
    supplement_round = 0
    while len(full_content) < min_words and supplement_round < MAX_SUPPLEMENT_ROUNDS:
        shortage = min_words - len(full_content)
        supplement_round += 1
        print(f"  [补写] 字数不足（{len(full_content)}/{min_words}），"
              f"第{supplement_round}轮补充约{shortage}字...")
        supplement = call_api(
            system_prompt=SUPPLEMENT_SYSTEM,
            user_message=(
                f"当前章节结尾内容：\n...{full_content[-400:]}\n\n"
                f"请在结尾处自然延伸，补充约{shortage}字。"
            ),
            temperature=0.88,
            max_tokens=1024,
        )
        supplement = clean_content(supplement)
        if not supplement:
            print("  [补写] 未获得有效补写内容，停止补写")
            break
        full_content = full_content + "\n\n" + supplement
        full_content = re.sub(r'\n{3,}', '\n\n', full_content)
        print(f"  补写完成，当前总字数：{len(full_content)}字")

    full_content = _self_check_and_revise(
        system_prompt=system_prompt,
        chapter_num=chapter_num,
        plot_goal=plot_goal,
        emotion_tag=emotion_tag,
        full_content=full_content,
        beat_plan=beat_plan,
        max_tokens=max_tokens,
    )
    full_content = re.sub(r'\n{3,}', '\n\n', full_content)

    total = len(full_content)
    print(f"  [OK] 第{chapter_num}章完成，总字数：{total}字")

    # 保存时写入 plot_goal 和 emotion_tag
    mm.save_chapter(
        chapter_num, f"第{chapter_num}章",
        full_content, "draft",
        word_target=word_target,
        plot_goal=plot_goal,
        emotion_tag=emotion_tag,
    )
    return full_content


def select_style_interactive() -> str:
    print("\n" + "=" * 50)
    print("  请选择写作风格")
    print("=" * 50)
    for key, style in AUTHOR_STYLES.items():
        print(f"  {key}. {style['name']:<12} {style['desc']}")
    print()
    choice = input("请输入编号（默认1）：").strip() or "1"
    if choice not in AUTHOR_STYLES:
        choice = "1"
    selected = AUTHOR_STYLES[choice]
    print(f"\n[OK] 已选择风格：{selected['name']}\n")
    return choice