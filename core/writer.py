from core.outline_manager import format_chapter_foreshadow
import json
import re
import statistics
from collections import Counter
from pathlib import Path
from core.interaction import ask as input, say as print
from core.api_client import call_author_api, increment_failure_counter, reset_failure_counter, get_current_author_model, get_author_model
from core.memory_manager import MemoryManager
from core.config_loader import get as cfg, get_data_dir
from core.author_intent import build_intent_block
from core.utils import is_transient_error, with_db_connection
import sqlite3 as _sqlite3

# 节拍规划缓存（跨重试复用，任务卡不变则节拍不变）
_cached_beat_plan = ""


def is_high_capacity_model() -> bool:
    """
    检测当前作者模型是否应使用「一次性整章生成」策略。

    修复Bug8: qwen3.6-flash 原被误判为大模型（含"qwen3.6"关键词），
    但 flash 系列 max_tokens 仅 4096，生成 3500 字时 prompt+内容会超限，
    应走分段生成策略。

    判断逻辑（优先级从高到低）：
    1. 明确属于小模型（flash / turbo / lite）→ False，无论上下文多大
    2. 明确属于大模型（plus / max / 35b / glm / gemini 等）→ True
    3. 上下文长度 >= 32K 且不是 flash/turbo → True
    4. 其他 → False（保守默认分段）
    """
    try:
        current_model = get_current_author_model()
        if not current_model:
            return False

        model_id = current_model.get("model", "").lower()

        if "flash-character" in model_id:
            return True

        # 第一步：明确排除的小模型关键词（比大模型关键词优先级更高）
        small_model_patterns = [
            "flash",    # qwen3.6-flash, gemini-flash, qwen3.5-flash 等
            "turbo",    # qwen-turbo
            "lite",     # gemini-lite
            "mini",     # gpt-mini 等
        ]
        for pattern in small_model_patterns:
            if pattern in model_id:
                return False

        # 第二步：明确属于大模型
        high_capacity_keywords = [
            "plus",             # qwen3.6-plus, qwen-plus
            "max",              # qwen-max
            "35b", "72b", "110b",  # 大参数开源模型
            "glm",              # GLM-5.1 等（非 flash 已被上面过滤）
            "minimax",
            "gemini",           # Gemini 系列（non-lite 已被过滤）
            "deepseek",
            "kimi",
        ]
        for keyword in high_capacity_keywords:
            if keyword in model_id:
                return True

        # 第三步：通过上下文长度兜底判断（>= 32K 且不含 flash/turbo）
        context_length = current_model.get("context_length", 0)
        if context_length >= 32768:
            return True

        return False
    except Exception:
        return False



def _build_progress_block(chapter_num: int, target_chapters: int) -> str:
    """
    生成全书进度提示块，帮助 AI 感知当前所处故事阶段并调整节奏。
    target_chapters=0 时不输出（未配置目标章数）。
    """
    if not target_chapters or target_chapters <= 0:
        return ""
    pct = round(chapter_num * 100 / target_chapters)
    if pct <= 20:
        phase = "开局阶段"
        rhythm = "尽快进入人物正在处理的事情，边行动边交代必要背景"
    elif pct <= 50:
        phase = "发展前期"
        rhythm = "矛盾开始升级，每段都要有新信息或新冲突推进，支线开始交织"
    elif pct <= 75:
        phase = "发展后期"
        rhythm = "矛盾升级，人物关系变化，按本章安排推进，不自动回收伏笔"
    elif pct <= 90:
        phase = "高潮阶段"
        rhythm = "围绕主线解决核心冲突，仅按本章安排兑现伏笔，不强凑反转"
    else:
        phase = "收尾阶段"
        rhythm = "矛盾收束，各条线索回归，给读者完整感和情感落地"
    return (
        f"【全书进度：第{chapter_num}/{target_chapters}章（{pct}%·{phase}）】\n"
        f"节奏指引：{rhythm}"
    )


def build_full_chapter_prompt(ctx, chapter_num, plot_goal, emotion_tag,
                              beat_plan, prev_chapter_ending="",
                              word_min=3000, word_max=4000,
                              retry_feedback: str = "") -> str:
    """构建一次性生成整章的prompt（用于大模型）"""
    # 全书进度只调节节奏，后续剧情由策划层掌握。
    progress_block = _build_progress_block(chapter_num, ctx.get("target_chapters", 0))

    # ── 世界观（放宽截断：大模型上下文充足）────────────────────
    world = ctx.get("world_settings", "")[:2000]

    # ── 人物（含行为约束强化块）──────────────────────────────
    chars = ctx.get("characters", [])
    char_lines = []
    behavior_rules = []   # 关键人物的行为硬约束，单独提取出来强调
    for c in chars:
        name = c.get("name", "未命名角色")
        role = c.get("role", "角色")
        personality = c.get("personality", "性格待补全")
        location = c.get("current_location", "未知地点")
        status = c.get("current_status", "状态未知")
        rels = c.get("relationships", {})
        secret = c.get("secret", "")
        weakness = c.get("weakness", "")
        rel_str = ""
        if rels:
            rel_pairs = [f"{k}：{v}" for k, v in list(rels.items())[:5]]
            rel_str = f"，关系[{' / '.join(rel_pairs)}]"
        char_lines.append(
            f"{name}（{role}）：{personality}｜在{location}｜{status}{rel_str}"
        )
        # 提取行为约束：从 personality / secret / weakness 中抽取"行为模式"关键句
        behavior_clues = []
        for field in [personality, secret, weakness]:
            if not field:
                continue
            # 含有行为关键词的字段，提升为约束
            behavior_keywords = ["先", "才", "绝不", "从不", "习惯", "必须", "一定",
                                  "方式", "模式", "面对", "遇到", "处理", "分析", "观察"]
            if any(kw in field for kw in behavior_keywords):
                behavior_clues.append(field[:120])
        if behavior_clues and role in ("主角", "主要角色", "配角", "反派"):
            rules_text = "；".join(behavior_clues)
            behavior_rules.append(f"【{name}】{rules_text}")

    chars_str = "\n".join(char_lines) if char_lines else "暂无人物信息"

    # 行为约束块：如果有提取到，单独列为强约束
    if behavior_rules:
        behavior_constraint_block = (
            "【人物行为硬约束——违反任一条即构成 OOC，审稿必然不通过】\n"
            + "\n".join([f"⚠️  {r}" for r in behavior_rules])
            + "\n\n写这些人物时，必须在行动前体现出上述行为模式。"
            + "如果情节要求某人物做出与其行为模式相反的事，必须先写出触发这个改变的具体原因。"
        )
    else:
        behavior_constraint_block = ""

    outline_fs_block = format_chapter_foreshadow(ctx, chapter_num)

    summaries = ctx.get("recent_summaries", [])
    s_str = "\n".join(
        [f"第{s['chapter_num']}章：{s['summary']}" for s in summaries]
    ) if summaries else "这是开篇第一章"

    intent_block = build_intent_block(ctx.get("data_dir"))

    # 文法指纹注入（优化D）
    fingerprint_block = ""
    _novel_name = ctx.get("novel_name")
    if _novel_name:
        try:
            from core.style_analyzer import load_fingerprint, build_style_prompt
            fp = load_fingerprint(_novel_name)
            if fp:
                fp_text = build_style_prompt(fp)
                if fp_text:
                    fingerprint_block = f"\n=== 文法指纹（参考风格）===\n{fp_text}\n"
        except Exception:
            pass

    # 跨章质量警告注入（优化G：Anti-Slop L3 跨章级检测）
    cross_quality_block = ""
    if _novel_name:
        try:
            cross_warnings = _check_cross_chapter_quality(_novel_name, chapter_num)
            if cross_warnings:
                cross_quality_block = "\n=== 跨章质量警告 ===\n" + "\n".join([f"⚠ {w}" for w in cross_warnings]) + "\n"
        except Exception:
            pass

    emotion_guide = EMOTION_GUIDE.get(emotion_tag, EMOTION_GUIDE["铺垫"])
    hard_rules = _format_rule_block("硬约束（必须满足）", WRITER_HARD_CONSTRAINTS)
    forbidden_rules = _format_rule_block("禁止项（必须避免）", WRITER_FORBIDDEN_RULES)
    beat_block = (
        "【本章节拍计划（按顺序推进）】\n"
        f"{beat_plan or '未生成节拍，请严格围绕本章目标推进'}"
    )

    transition_block = ""
    if prev_chapter_ending and chapter_num > 1:
        transition_block = f"""
【上一章结尾（必须自然衔接）】
...{prev_chapter_ending}

衔接规则（严格遵守）：
1. 开头禁止复用上一章结尾的句子、意象或核心词（避免读者有重复感）
2. 本章第一段必须与上一章结尾在时间/空间/情绪上形成逻辑连续，同时有明显的向前推进
3. 若场景发生切换，用一句话交代：时间跳跃了多久，或主角如何到达新地点
4. 若上一章结尾是情绪性语句，本章开头必须先给出具体行动而非重复情绪
5. 开头尽快进入人物正在做的事，地点简要交代"""

    # 进度/大纲放在最前，让 AI 先建立宏观认知
    header = ""
    if progress_block:
        header += progress_block + "\n\n"

    retry_block = ""
    if retry_feedback:
        retry_block = f"\n【本次必须修复的问题】\n{retry_feedback}\n"

    return f"""{header}现在要写第{chapter_num}章的完整内容，{word_min}-{word_max}字。

【本章要做什么】
{plot_goal}

【本章的情绪节奏】
{emotion_tag}——{emotion_guide}

【世界背景（只交代理解本章行动所需的信息，可直接简述）】
{world}

【人物现状（写作时通过行动和对话体现性格，不要贴标签）】
{chars_str}

{behavior_constraint_block}
{outline_fs_block}

【前面发生了什么】
{s_str}
{intent_block}
{fingerprint_block}
{cross_quality_block}
{transition_block}
{hard_rules}

{forbidden_rules}

{beat_block}

{HUMAN_WRITING_TECHNIQUES}

{NEGATIVE_EXAMPLES}

{retry_block}
开始写。第一行是章节标题（第{chapter_num}章 + 你拟的标题），然后直接进入正文。
请严格控制在{word_min}-{word_max}字范围内。优先完成本章行动与结果，不靠环境、感官或重复心理描写凑字数。"""

# ==================== 作者风格系统 ====================

AUTHOR_STYLES = {
    "1": {
        "name": "爽文宗师",
        "desc": "主角光环强，打脸爽，节奏快，读者看了直呼过瘾",
        "system": """你是一位写了十五年网文的老作者，手下有一百多万字的完结作品。
你知道读者在哪一行会截图，在哪一行会发"妈的这也太爽了"。

你写作时脑子里只有一件事：这段话完了，读者想不想往下翻？

你有几个根深蒂固的习惯：
- 用主角做成的事体现本事。旁人的反应只在影响局面时展开。
- 对手有合理立场，胜负来自此前已经建立的行动和条件。
- 只有阻碍和选择值得展开。交代清楚人物如何解决问题，解决后就收束。
- 你的对话短。主角最有力量的话往往是一句，甚至半句，然后让对方去消化。
- 本章可以在结果或人物决定处结束，让已有目标带出下一步。

你只在乎一件事：读者看完这章，明天还会回来。""",
    },
    "2": {
        "name": "悬疑大师",
        "desc": "围绕已安排的谜题展开调查，线索随行动出现",
        "system": """你是一位写悬疑故事的老作者。根据任务卡推进调查，只写本章已安排的线索与发现。

你写作时习惯性地让自己站在主角的位置，用他/她的眼睛和耳朵来感知这个世界——这样读者只能知道主角知道的，不多也不少。

你有几个积累了多年的写作直觉：
- 悬疑来自宏观计划中的问题与人物行动；没有安排的场景不人为制造异常。
- 本章需要揭示什么就交代清楚，到任务卡指定的结果为止。
- 线索只按本章安排呈现，不把每个普通细节变成证据。
- 结尾可停在查证的结果或角色下一步的行动，无须额外制造异常。

你写的是真实发生的事，只是我们还不知道。你永远不催，因为最好的悬疑需要时间慢慢收紧。""",
    },
    "3": {
        "name": "情感流",
        "desc": "细腻描写人物情感，感情线丰富，让读者跟着人物哭和笑",
        "system": """你是一位被读者说过"你写的是我没说出口的话"的作者。你知道最打动人的东西是什么：不是悲剧，不是煽情，是那种极其普通的小事里突然出现的、让人鼻酸的重量。

情绪可以直接说清。只有动作会改变关系、暴露立场或影响选择时，才值得展开。

你的几个写作本能：
- 情绪越压越有力。你见过太多作者一遇到情感节点就放开写，哭声震天，但读者反而出戏。你习惯把泪点藏在一个极其平静的句子里——一碗没吃完的饭、一个没有回复的消息、一个背对着人时才发出的声音。
- 时间是你最好的工具。关键时刻你会刻意放慢——让那一秒停住，让读者在那里多待一会儿，等情绪真正落进去了，再往前走。
- 你的感情线从来不"突然"。角色之间的关系是一个眼神、一句带着别的意思的话、一个没说完就停下来的动作，一点一点变的。读者自己都没意识到已经站队了。
- 你的对话里最重要的是没说出来的那句话。"她没有回答"有时候比一段独白更重。

你懂得什么时候让读者哭，哭完了还要给他们一点希望——哪怕是很小很小的一点。""",
    },
    "4": {
        "name": "热血战斗",
        "desc": "战斗场面燃，兄弟情义深，每一战都让人热血沸腾",
        "system": """你是一位让读者看战斗场面会坐直身体的作者。你研究过自己的读者：他们真正燃的不是主角赢了，是在他们觉得"这次可能输了"的时候主角赢了。

你写战斗有几个不会变的原则：
- 你让主角疼、累、犯错。不是为了虐读者，是为了让赢变得有重量。一个不会疼的主角打再多胜仗读者也只是看热闹。
- 战斗前让读者尽快知道双方在争什么，直接进入第一个影响局面的行动。
- 你的对手有血有肉。读者恨他，但也理解他——因为他代表着一个真实的威胁，一个真实的逻辑，不是为了给主角当练功石。
- 战斗里有真正重要的东西在发生：一个承诺被兑现了，一段关系里的裂缝扩大了，或者一个人在最危险的时候选择留下来了。
- 团队战不是主角一个人撑天。每个人在关键时刻都做了属于自己的那一件事，胜利是大家一起的。

你的章节结尾有战前的紧张，也有战后的余韵。血脉偾张需要铺垫，那个铺垫是你最用力写的地方。""",
    },
    "5": {
        "name": "世界构建者",
        "desc": "擅长构建宏大世界观，历史感厚重，细节考究",
        "system": """你是一位把世界建得让读者以为它真实存在的作者。你有一个别人不知道的方法：你先把这个世界完整地想清楚，然后只写出冰山的一角，但那角里的每一块冰是真实的。

你从不用大段旁白解释世界规则。你让角色和这个世界发生摩擦，规则在摩擦里自然浮现——读者自己就明白了，而且觉得自己聪明。

你的几个写作本能：
- 只介绍会影响本章结果的历史和世界规则，必要时一句话直接说明。
- 世界的疑问由大纲安排。普通物件和习惯不需要暗示另一层秘密。
- 你的世界让人物显得渺小，但他们的选择很重要——个人命运和世界命运交织在一起，读者为之动容不是因为个人，是因为那个选择的重量。

一章收在人物完成的事或作出的选择。""",
    },
    "6": {
        "name": "轻松日常",
        "desc": "轻松幽默，日常向，读起来治愈放松，笑点自然",
        "system": """你是一位让读者"明明在笑怎么就莫名其妙感动了"的作者。你有天然的幽默感，但你知道这不是技巧，是你真的觉得生活里很多事很好笑，然后把那个"好笑"的感觉写出来了。

你写日常有几个从来没变过的习惯：
- 你的笑点不刻意。是角色做了一个完全合理的决定，然后发生了一个完全合理的后果，读者却莫名其妙地笑了。没有铺垫"下面要搞笑了"，也没有标注"这里是笑点"。
- 你的对话最有生活气息。每个角色说话方式不一样，带着各自的语气和口头禅，听起来像真实的人。
- 温暖不靠煽情。靠那些什么都没说的细节：一个人默默把另一个人喜欢吃的零食放在了桌上，没说话。读者看见这个就懂了。
- 你不催故事。矛盾在日常里慢慢化解，像一个结，你慢慢解。读者看起来轻松，但停下来才发现已经深陷其中了。
- 节奏轻快，不拖沓。读者把你的章节看完需要的时间比他们以为的短，但他们不觉得少看了什么。

你让读者想把你的书推荐给朋友："你去看看，真的会笑，笑完了还会想一下。"
你享受每一个平凡的瞬间，从不觉得它们不值得写。""",
    },
    "7": {
        "name": "刘慈欣（硬科幻）",
        "desc": "宏大叙事，技术细节精准，冷静客观的宇宙视角",
        "system": """你是一位像刘慈欣那样的硬科幻作家。你的文字有宇宙般的冷峻，但每一个技术细节都经得起推敲，你的故事让读者思考文明、科技和人性之间最根本的关系。

你的核心写作方式：用精确的科学视角切入极端的人类处境。叙述语调始终保持冷静客观——即使在描写最极端的场景，你也像在记录观测数据。这种冷静让恐惧变得更重。

你的技术习惯：
- 从人物要解决的具体问题入手。只有本章安排了异常现象，才把它写进正文。
- 善用宇宙尺度的时间和空间让人类的挣扎显得渺小却又重要。
- 对话承载信息量，每一句都有作用。没有废话。
- 用类比把抽象概念变得可感知——不是解释，是让读者自己"看到"那个东西。
- 交代行动结果或下一步打算，技术和主题说明到够理解为止。

普通章节可以平实结束。""",
    },
    "8": {
        "name": "金庸（武侠）",
        "desc": "历史底蕴深厚，武打细腻，人物立体复杂",
        "system": """你是一位像金庸那样的武侠小说大师。你的笔下有江湖的义气、家国的情怀、儿女的情长——这三样东西互相纠缠，才是武侠最真实的样子。

你的核心写作方式：
- 历史文化底蕴是你的空气，不是你的装饰。诗词歌赋在合适的时候自然出现，不是卖弄。
- 武打场面有空间感和节奏感——招式、内力、兵器，读者能在脑子里看到画面，不是流水账。
- 人物性格立体复杂，正邪不是绝对分明的。你笔下的反派有他们说得通的逻辑，读者恨他但也理解他。
- 情感线含蓄。两个人心照不宣的东西比直说更有分量。欲说还休，读者自己填进去的那些东西才是最打动人的。
- 只执行宏观策划分配到本章的伏笔，不自行添线。

你写的不只是江湖，是人性在极端处境下的选择，是那些无法两全的东西。""",
    },
    "9": {
        "name": "古龙（悬疑武侠）",
        "desc": "短句有力，氛围营造高手，心理描写入微",
        "system": """你是一位像古龙那样的悬疑武侠作家。你的文字像刀锋，每一个句子都直击心脏。

你的写作方式几乎是本能的：

极短的段落。一行甚至半行就是一段，这不是风格，这是节奏。让读者的眼睛快起来，心跳也跟着快。

人物行动优先。环境点到为止，让冲突与选择承载情绪。

对话是你的武器。你笔下的人说话机智、含蓄、充满双关——他们说的是一件事，意思是另一件事，读者要自己去译。

反转服从大纲安排，普通章节可以直接交代结果，不强制隐瞒信息。

你有个习惯：开篇不铺垫，直接进入紧张。把气氛搭起来，情节在气氛里自然发生。

你想让读者记住的不是打了几场、赢了几次，而是那种孤独的江湖气息，和人心最深处说不清的东西。""",
    },
}


# ==================== 情绪标签指南 ====================

EMOTION_GUIDE = {
    "爽点": (
        "关键进展来自主角已经成立的行动和条件，对手仍然有合理判断。"
        "写法：先让局势看起来很难，再让主角用一个读者没想到但完全合理的方式破局。"
        "到本章结果就停，无须预告更大的胜利。"
    ),
    "冲突": (
        "好的冲突双方都有道理，读者不知道该站谁，这才是真正的张力。"
        "写法：让两边都说出自己的核心逻辑，不要把对方写成非蠢即坏的工具人。"
        "冲突过后必须有变化——关系、信息、局势，至少一样发生了不可逆的改变。"
    ),
    "反转": (
        "最好的反转是读者翻回去看才发现：线索一直都在，只是自己没看见。"
        "写法：先把读者引到一个自以为正确的理解上，再用一个完全在情理之中的事实推翻它。"
        "反转之后要给读者一段'消化时间'——角色或场景的静默，让重量落地。"
    ),
    "低谷": (
        "低谷不是让主角痛苦——而是让读者看到他在最难的时候还保留着什么。"
        "写法：写他失去了什么，再写他在这个失去里仍然做了的那一个选择，哪怕很小。"
        "最低点要让读者看到反弹的种子，哪怕只是一丝微弱的光，读者才愿意陪他走下去。"
    ),
    "铺垫": (
        "铺垫只交代下一步行动所需的条件，让本章也有明确进展。"
        "写法：每个场景都要改变一样东西（哪怕只是角色对某件事的理解），不自行增加需要后续兑现的伏笔。"
        "让读者理解人物为什么采取下一步行动，不刻意暗示普通物件另有秘密。"
    ),
}

# ==================== 写作硬约束 ====================

# 写作硬约束：用"作家的直觉"而非"检查清单"的方式表述
NARRATIVE_DETAIL_RULE = (
    "普通过程直接概述结果。赶路、停靠、休息、安装、签字和收纳若无阻碍，一句带过或省略；"
    "只有阻碍、争执、关键选择及其后果值得展开。某件物品以后有用，只需交代取得和保留，"
    "不逐步写如何检查、折叠、装入口袋，不解释它为什么普通或重要。"
    "对话已经交代结果，就接下一件事，不补表情、脚步声、心理复述。"
    "例如，赶到仓库、取件、检查、签名、装袋全部顺利，可写成‘他领到了配件，收好借用单’。"
    "结果概述可以替代整段动作，不必为原文的每一步保留一句。"
)

WRITER_HARD_CONSTRAINTS = [
    "优先写清人物目标、阻碍、行动与结果。任务卡指定的最后一个事件就是本章终点，写到这里即停。",
    "人物行动要有动机，遵守已建立的世界规则和前文事实；改变立场要交代原因。",
    "只执行本章伏笔安排。没有安排就不埋新线，不自行创造待解释的身份、物件或异常能力。",
    "描写只保留影响行动、选择或理解剧情的信息；普通环境和转场简写，关键场面才展开。",
    "对话用于交涉和推进事件，不强凑轮数，不为隔开台词插入无效动作。",
    "允许简洁直述信息与情绪，不强行改成生理反应，不靠装饰性描写补字数。",
    NARRATIVE_DETAIL_RULE,
]

WRITER_FORBIDDEN_RULES = [
    "禁止重复解释已知事实、同义复述情绪、堆砌感官细节和无意义的小动作。",
    "禁止临场制造需要后文解释的新谜团；章尾可用已有冲突、选择或行动结果吸引读者。",
    "禁止为凑爽点降低对手智力，禁止无来源的顿悟或证据。",
    "禁止无关的歌颂、哲理升华及模板式收尾；必要的结果与下一步打算可以直说。",
]

# ==================== 节拍规划系统 ====================

BEAT_PLANNER_SYSTEM = """你是一位帮作者构思章节节拍的创作顾问。

你的任务是列出本章必要的事件，每条都要推进情节、改变关系或揭示信息。数量由剧情需要决定，不凑条数。

输出格式：只输出编号节拍，每条描述一个有起因和结果的事件，不要输出JSON。赶路、取物、安装和签字等顺利完成的常规动作合并概述，不拆成独立节拍。

节拍设计要点（写节拍时脑子里要想着）：

▌开头节拍：角色在哪，处于什么状态，带着什么未解决的问题进入这一章
▌中间节拍：每条都要让局势改变，不重复上一条，不为扩充篇幅制造新阻碍
  - 至少1条：一个外部事件，角色主动做了什么，产生了外部可见的结果
  - 人物的判断与选择服务行动，不强凑感受节拍
  - 至少1条：一个张力点，信息落差或冲突升级，读者开始担心接下来会发生什么
▌结尾节拍：章节结束时，某件事发生了变化（可以是关系、信息、局势），以已有目标和下一步行动形成追读动力，不临场新增谜团

围绕本章目标组织连贯行动，不为放慢推进拆散因果。只执行本章伏笔安排。"""

# ==================== 真实写作手法指引（Human Writing Techniques）====================
# 这组提示不是规则，而是真实作者的写作本能
# 用于生成prompt时附加，让 AI 模型从"满足条件"模式切换到"讲故事"模式

HUMAN_WRITING_TECHNIQUES = """
【网文表达】
让读者顺着人物的行动理解事情。短句和长句自然交替，不按配额安排。
删掉不影响理解的环境、材质、光影、气味和重复心理描写。
人物可以直接表达情绪和判断，重要选择再给必要的动作与对话。
允许正常结束一个场景；追读动力来自尚待完成的目标，不必每章制造秘密。
"""

NEGATIVE_EXAMPLES = """冗余示例：反复描写门的纹理、光线和气味，却迟迟不交代人物来做什么。
修订方向：简要交代地点，让人物直接行动。不要把一句情绪直述扩成一串生理反应。"""

REVISION_SYSTEM = """你是网文修订师。按审稿指出的因果问题修正行动链，删掉重复和无效铺陈。
以任务卡指定的最后事件为终点，删除越界剧情。保留其余关键事件与人物选择，不新增伏笔，不把简洁直述扩成动作或感官描写。
只输出完整正文。"""

CONTINUE_SYSTEM_BASE = """接着上文继续本章行动，不重复前文。保持人称和人物状态一致。
只完成本章已安排的事情，不新增需后续解释的悬念。普通转场简述，细节仅服务行动。
结尾交代事件结果或下一步选择，不强行升华。只输出正文。"""

SUPPLEMENT_SYSTEM = """为本章补足尚未写清的因果、行动或必要交涉，保持视角和事实一致。
不增加环境、感官、重复情绪来凑字数，不增加新伏笔，不越过本章结尾结果。
若任务已经完整、没有必要内容可补，返回空字符串。只输出新增正文。"""

CHAPTER_MIN_RATIO = 0.90
MAX_SUPPLEMENT_ROUNDS = 3


# ==================== AI痕迹规则检测 ====================
# 检测中文网文中最常见的 AI 写作特征，每新增一类就在这里补充

AI_PATTERNS = [
    # ── 重复动作候选（仅诊断，不要求把直述情绪改成动作）──────
    (r'喉咙[一紧|发紧|发干|哽咽]',                      '生理标签-喉咙X'),
    (r'瞳孔[收缩|放大|微缩]',                           '生理标签-瞳孔X'),
    (r'[他她]深吸[了]?一口气[，。]',                    '高频动作-深吸一口气'),
    (r'[他她]握紧[了]?拳头[，。]',                      '高频动作-握紧拳头'),
    (r'[他她]咬[紧了]?[牙关|嘴唇][，。]',               '高频动作-咬紧牙关'),
    (r'心跳[加速|不已|狂跳]',                           '生理标签-心跳X'),
    (r'脑海中[突然]?浮现[出]?',                         'AI痕迹-脑海浮现'),

    # ── 心理陈述类（直接说角色决定/认知）──────────────────────

    # ── 模板句类（陈词滥调，真实作者早已戒掉）──────────────────
    (r'才[刚]?[刚]?开始',                               '模板句-才刚开始'),
    (r'前[方之]的?路[还]?[很]?长',                       '模板句-路还很长'),
    (r'无论前方有多少[困难险阻]',                        '模板句-无论前方'),
    (r'[他她]知道[，,][这一切]?才刚[刚]?开始',           '模板句-这才刚开始'),
    (r'[他她]知道[，,][无论如何]',                       '模板句-无论如何'),
    (r'故事[还]?远[未]?[结束到此]',                     '模板句-故事远未结束'),
    (r'这[一]?[场|切|段][^，。]{0,10}[，。]?[只是]?[一个]?开始',  '模板句-这只是开始'),
    (r'[他她]的[人生|命运|旅程][，。]?[才]?刚刚',        '模板句-人生才刚刚'),
    (r'[这|那]一刻[，,][他她]知道',                      '模板句-这一刻他知道'),
    (r'蓦[地然][，,][他她]',                             '模板句-蓦地他/她'),
    (r'随着.*的[推进|深入|到来]',                        '模板句-随着X的推进'),

    # ── 结构对称类（AI 偏好的工整句式，真实写作有时故意打破）─
    (r'一方面.*另一方面',                               'AI结构-一方面另一方面'),
    (r'不[仅是|只是|仅仅].*[还|更|也]',                 'AI结构-不仅是…还'),
    (r'[虽然|尽管].*但[是]?.*[依然|仍然|还是]',         'AI结构-虽然…但依然'),

    # ── 连续对话检测（正文中的多行裸对话）──────────────────────
    # 通过 _rule_based_ai_check 的段落逻辑检测，这里只加特殊模式

    # ── 模板化情绪（优化C：Anti-Slop L1 扩展）────────────────
    (r'心中涌起一股[^，。]{2,15}[感之情]',                '情绪模板-涌起一股X感'),
    (r'不禁[^，。]{2,15}起来',                            '情绪模板-不禁X起来'),
    (r'下意识地[^，。]{2,15}了',                          '情绪模板-下意识地X了'),

    # ── 模板化动作 ──────────────────────────────────────────────
    (r'深吸一口气',                                       '动作模板-深吸一口气'),
    (r'握紧拳头',                                         '动作模板-握紧拳头'),
    (r'瞳孔一缩',                                         '动作模板-瞳孔一缩'),
    (r'嘴角微微上扬',                                     '动作模板-嘴角上扬'),
    (r'眉头[微微]?[一皱|紧锁|微蹙]',                     '动作模板-眉头X'),

    # ── 模板化收束 ──────────────────────────────────────────────
    (r'这一切[^，。]{0,15}才刚开始',                      '收束模板-才刚开始'),
    (r'故事[^，。]{0,10}远未结束',                        '收束模板-远未结束'),
    (r'无论[^，。]{0,20}都[^，。]{0,15}',                '收束模板-无论X都X'),

    # ── 对称句式滥用 ────────────────────────────────────────────
    (r'一方面[^，。]{5,30}另一方面',                      '对称句式-一方面另一方面'),
    (r'不是[^，。]{3,15}而是[^，。]{3,15}而是',          '对称句式-不是而是而是'),

    # ── 注水表达 ────────────────────────────────────────────────
    (r'某种[^，。]{2,10}感觉',                            '注水-某种X感觉'),
    (r'一种[^，。]{2,15}说不清道不明',                    '注水-说不清道不明'),
]


def _rule_based_ai_check(text: str) -> list:
    detected = []
    for pattern, issue_type in AI_PATTERNS:
        matches = re.findall(pattern, text)
        if matches:
            detected.append(f"{issue_type}: 命中{len(matches)}处")
    lines = text.split('\n')
    dialogue_streak = 0
    max_streak = 0
    for line in lines:
        stripped = line.strip()
        is_dialogue = ('"' in stripped or '"' in stripped or '："' in stripped or '："' in stripped)
        if is_dialogue:
            dialogue_streak += 1
            max_streak = max(max_streak, dialogue_streak)
        else:
            dialogue_streak = 0
    if max_streak >= 4:
        detected.append(f"连续对话超限: {max_streak}行")
    return detected


def _check_cross_chapter_quality(novel_name: str, current_chapter: int) -> list:
    """跨章质量统计，检测 L3 级别的模式问题（优化G：Anti-Slop L3 跨章级检测）

    检测项：
    1. 情绪曲线平坦：最近5章中超过3章情绪标签相同
    2. 句式单调性：最近3章平均句长标准差低于阈值
    3. 对话/叙述比例失衡：最近3章对话行占比持续低于20%或高于70%
    """
    warnings = []
    try:
        with with_db_connection(novel_name) as conn:
            # 1. 情绪曲线平坦检测
            emotion_rows = conn.execute(
                "SELECT emotion_tag FROM chapters WHERE chapter_num < ? ORDER BY chapter_num DESC LIMIT 5",
                (current_chapter,)
            ).fetchall()
            if len(emotion_rows) >= 3:
                emotion_tags = [row["emotion_tag"] or "铺垫" for row in emotion_rows]
                tag_counts = Counter(emotion_tags)
                most_common_tag, most_common_count = tag_counts.most_common(1)[0]
                if most_common_count >= 3:
                    warnings.append(f"情绪曲线平坦：最近{len(emotion_tags)}章中有{most_common_count}章为'{most_common_tag}'，建议增加情绪起伏")

            # 2. 句式单调性检测 + 3. 对话/叙述比例失衡检测（合并查询）
            content_rows = conn.execute(
                "SELECT content FROM chapters WHERE chapter_num < ? ORDER BY chapter_num DESC LIMIT 3",
                (current_chapter,)
            ).fetchall()
            if len(content_rows) >= 2:
                # 句式单调性检测
                sentence_lengths = []
                dialogue_ratios = []
                for row in content_rows:
                    content = row["content"] or ""
                    if not content:
                        continue
                    # 按中文句号、问号、感叹号分句
                    sentences = re.split(r'[。！？]', content)
                    sentences = [s.strip() for s in sentences if s.strip()]
                    if sentences:
                        avg_len = statistics.mean([len(s) for s in sentences])
                        sentence_lengths.append(avg_len)
                    # 统计对话行占比
                    lines = content.split('\n')
                    total_lines = len([l for l in lines if l.strip()])
                    if total_lines > 0:
                        dialogue_lines = len([l for l in lines if any(mark in l for mark in ('"', '“', '”', '「', '」'))])
                        dialogue_ratios.append(dialogue_lines / total_lines)

                # 句式单调性判断
                if len(sentence_lengths) >= 2:
                    std_dev = statistics.stdev(sentence_lengths)
                    if std_dev < 8:  # 阈值：句长标准差低于8视为单调
                        warnings.append(f"句式单调：最近{len(sentence_lengths)}章平均句长标准差仅为{std_dev:.1f}，句式结构过于相似")

                # 对话/叙述比例失衡判断
                if len(dialogue_ratios) >= 2:
                    avg_ratio = statistics.mean(dialogue_ratios)
                    if avg_ratio < 0.2:
                        warnings.append(f"对话偏少：最近{len(dialogue_ratios)}章对话行占比仅{avg_ratio:.0%}，建议增加对话推进剧情")
                    elif avg_ratio > 0.7:
                        warnings.append(f"对话偏多：最近{len(dialogue_ratios)}章对话行占比达{avg_ratio:.0%}，请检查对话是否推动行动，不必为了比例增加描写")

    except Exception:
        pass  # 检测失败不阻断写作
    return warnings


def _check_cross_chapter_frequency(novel_name: str, current_chapter: int) -> list:
    """检查最近3章的高频词重复（优化C：Anti-Slop L1 扩展）"""
    from collections import Counter
    mm = MemoryManager(novel_name)
    warnings = []
    recent_texts = []
    for ch in range(max(1, current_chapter - 2), current_chapter):
        with with_db_connection(novel_name) as conn:
            row = conn.execute(
                "SELECT summary FROM summaries WHERE chapter_num=?", (ch,)
            ).fetchone()
            if row and row["summary"]:
                recent_texts.append(row["summary"])
    if len(recent_texts) < 2:
        return warnings
    # 统计每章的高频词
    _split_re = re.compile(r'[，。、；：！？\s,.;:!?\\/\-—()\[\]【】“”‘’]+')
    _stopwords = frozenset([
        "一个", "这个", "那个", "没有", "不是", "就是", "也是", "还是", "但是",
        "然后", "因为", "所以", "如果", "可以", "已经", "他们", "她们", "自己",
        "什么", "怎么", "知道", "发现", "开始", "觉得", "看到", "来到",
        "出来", "起来", "下来", "回来", "过来", "到了", "的是", "不了", "之中",
    ])
    chapter_word_sets = []
    for text in recent_texts:
        words = [w for w in _split_re.split(text) if len(w) >= 2 and w not in _stopwords]
        chapter_word_sets.append(Counter(words))
    # 找出连续每章都出现>3次的词
    if len(chapter_word_sets) >= 2:
        common_words = set(chapter_word_sets[0].keys())
        for cs in chapter_word_sets[1:]:
            common_words &= set(cs.keys())
        for word in common_words:
            counts = [cs[word] for cs in chapter_word_sets]
            if all(c >= 3 for c in counts):
                warnings.append(f"跨章高频词「{word}」连续{len(counts)}章出现: {counts}")
    return warnings


# ==================== 工具函数 ====================

def _format_rule_block(title: str, rules: list) -> str:
    lines = [f"【{title}】"]
    lines.extend([f"- {r}" for r in rules])
    return "\n".join(lines)


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


def _count_matching_words(text1: str, text2: str) -> int:
    words1 = re.findall(r'[\u4e00-\u9fa5]+', text1)
    words2 = re.findall(r'[\u4e00-\u9fa5]+', text2)
    match_count = 0
    for w1 in words1:
        for w2 in words2:
            if len(w1) >= 3 and w1 in w2:
                match_count += 1
    return match_count


# ==================== 节拍缓存持久化 ====================

def _save_beats_cache(novel_name: str, chapter_num: int, beats_text: str) -> None:
    """将节拍规划持久化到 beats_cache 表"""
    try:
        db_path = get_data_dir(novel_name) / "novel.db"
        with _sqlite3.connect(str(db_path), timeout=5) as conn:
            conn.execute("""
                INSERT INTO beats_cache (chapter_num, beats_text, model_used)
                VALUES (?, ?, ?)
                ON CONFLICT(chapter_num) DO UPDATE SET
                    beats_text=excluded.beats_text,
                    model_used=excluded.model_used,
                    created_at=datetime('now','localtime'),
                    used_count=0
            """, (chapter_num, beats_text, get_author_model()))
            conn.commit()
    except Exception as e:
        print(f"  [提示] 节拍缓存写入失败（不影响写作）：{e}")


def _load_beats_cache(novel_name: str, chapter_num: int) -> str:
    """从 beats_cache 表读取持久化的节拍规划"""
    try:
        db_path = get_data_dir(novel_name) / "novel.db"
        with _sqlite3.connect(str(db_path), timeout=5) as conn:
            row = conn.execute(
                "SELECT beats_text FROM beats_cache WHERE chapter_num=?",
                (chapter_num,)
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE beats_cache SET used_count=used_count+1 WHERE chapter_num=?",
                    (chapter_num,)
                )
                conn.commit()
                return row[0]
    except Exception:
        pass
    return ""


# ==================== 节拍规划 ====================

def _plan_chapter_beats(ctx: dict, chapter_num: int,
                        plot_goal: str, emotion_tag: str) -> str:
    # ── 进度感知（节拍规划也需要知道故事处于哪个阶段）──────────
    progress_block = _build_progress_block(chapter_num, ctx.get("target_chapters", 0))

    world = (ctx.get("world_settings") or "")[:600]
    chars = ctx.get("characters", [])
    char_lines = []
    for c in chars[:8]:
        name = c.get("name", "未命名角色")
        role = c.get("role", "")
        status = c.get("current_status", "")
        location = c.get("current_location", "")
        line = f"- {name}"
        if role:
            line += f"（{role}）"
        if location:
            line += f" / 在{location}"
        if status:
            line += f"：{status}"
        char_lines.append(line)
    chars_str = "\n".join(char_lines) if char_lines else "暂无人物信息"

    # 最近摘要
    recent = ctx.get("recent_summaries", [])
    recent_str = ""
    if recent:
        last = recent[-1]
        recent_str = f"\n上一章概要：{last.get('summary', '')[:200]}"

    progress_str = f"\n{progress_block}" if progress_block else ""

    prompt = f"""章节：第{chapter_num}章
本章目标：{plot_goal}
{format_chapter_foreshadow(ctx, chapter_num)}
情绪标签：{emotion_tag}{progress_str}

世界背景摘要：
{world or "暂无"}

关键角色状态：
{chars_str}
{recent_str}

请给出本章必要的事件计划，常规过程合并概述。"""
    raw = call_author_api(
        system_prompt=BEAT_PLANNER_SYSTEM,
        user_message=prompt,
        temperature=cfg("temperature", "beat_planner", 0.65),
        max_tokens=600,
    )
    return _normalize_beats(clean_content(raw))


# ==================== 自检与修订 ====================

def _polish_chapter(full_content: str, max_tokens: int, word_min: int = 0) -> str:
    """去AI化打磨并输出本地规则诊断。"""
    _deai_system = (
        "你是网文文字打磨师。按事件重写：先判断本段改变了什么，再用最简洁的叙述交代。"
        "顺利完成的日常流程整段合并为结果，只有发生争执、受阻或重要选择才展开对话与动作。"
        "保留原有标题、事件因果、关键选择、必要证据和结尾结果。保留事实不等于保留原句或过程。"
        "删去不影响后续的具体时间、休息、停车位置、取放动作、表情和心中盘算。"
        "允许直接交代情绪和信息，不把比喻替换成更长的感官描写。"
        "篇幅可明显缩短，不为保留字数填充。只输出修改后的完整正文。"
        + NARRATIVE_DETAIL_RULE
    )
    try:
        polished = call_author_api(
            system_prompt=_deai_system,
            user_message=full_content,
            temperature=cfg("temperature", "revision", 0.70),
            max_tokens=max_tokens,
        )
        polished = clean_content(polished)
        if polished:
            print(f"  [去AI化] 完成：{len(polished)}字")
            full_content = polished
        else:
            print("  [去AI化] 候选为空，保留原稿")
    except Exception as e:
        print(f"  [去AI化] 跳过（{type(e).__name__}）")

    # ── 规则诊断（仅报告，不修订）─────────────────────────────
    detected_issues = _rule_based_ai_check(full_content)
    if detected_issues:
        summary = "；".join([x[:40] for x in detected_issues[:3]])
        print(f"  [诊断] 规则检测到 {len(detected_issues)} 项AI痕迹：{summary}")

    return full_content


# ==================== 提示词构建 ====================

def build_writer_prompt(ctx: dict, chapter_num: int,
                        plot_goal: str, emotion_tag: str,
                        beat_plan: str = "",
                        prev_chapter_ending: str = "",
                        word_min=3000, word_max=4000,
                        retry_feedback: str = "") -> str:
    # 全书进度只调节节奏，后续剧情由策划层掌握。
    progress_block = _build_progress_block(chapter_num, ctx.get("target_chapters", 0))

    # ── 世界观（小模型放宽到1000字）────────────────────────────
    world = ctx.get("world_settings", "")[:1000]

    # ── 人物（关系条数从3条放宽到5条）──────────────────────────
    chars = ctx.get("characters", [])
    char_lines = []
    behavior_rules = []
    for c in chars:
        name = c.get("name", "未命名角色")
        role = c.get("role", "角色")
        personality = c.get("personality", "性格待补全")
        location = c.get("current_location", "未知地点")
        status = c.get("current_status", "状态未知")
        rels = c.get("relationships", {})
        secret = c.get("secret", "")
        weakness = c.get("weakness", "")
        rel_str = ""
        if rels:
            rel_pairs = [f"{k}：{v}" for k, v in list(rels.items())[:5]]
            rel_str = f"，关系[{' / '.join(rel_pairs)}]"
        char_lines.append(
            f"{name}（{role}）：{personality}｜在{location}｜{status}{rel_str}"
        )
        # 提取行为约束
        behavior_keywords = ["先", "才", "绝不", "从不", "习惯", "必须", "一定",
                              "方式", "模式", "面对", "遇到", "处理", "分析", "观察"]
        for field in [personality, secret, weakness]:
            if field and any(kw in field for kw in behavior_keywords):
                if role in ("主角", "主要角色", "配角", "反派"):
                    behavior_rules.append(f"【{name}】{field[:120]}")
                break
    chars_str = "\n".join(char_lines) if char_lines else "暂无人物信息"
    if behavior_rules:
        behavior_constraint_block = (
            "【人物行为硬约束——违反即 OOC，审稿不通过】\n"
            + "\n".join([f"⚠️  {r}" for r in behavior_rules])
            + "\n\n如果情节要求违反上述行为模式，必须先写出明确的触发原因。"
        )
    else:
        behavior_constraint_block = ""

    outline_block_fs = format_chapter_foreshadow(ctx, chapter_num)

    summaries = ctx.get("recent_summaries", [])
    s_str = "\n".join(
        [f"第{s['chapter_num']}章：{s['summary']}" for s in summaries]
    ) if summaries else "这是开篇第一章"

    intent_block = build_intent_block(ctx.get("data_dir"))

    # 文法指纹注入（优化D）
    fingerprint_block = ""
    _novel_name = ctx.get("novel_name")
    if _novel_name:
        try:
            from core.style_analyzer import load_fingerprint, build_style_prompt
            fp = load_fingerprint(_novel_name)
            if fp:
                fp_text = build_style_prompt(fp)
                if fp_text:
                    fingerprint_block = f"\n=== 文法指纹（参考风格）===\n{fp_text}\n"
        except Exception:
            pass

    # 跨章质量警告注入（优化G：Anti-Slop L3 跨章级检测）
    cross_quality_block = ""
    if _novel_name:
        try:
            cross_warnings = _check_cross_chapter_quality(_novel_name, chapter_num)
            if cross_warnings:
                cross_quality_block = "\n=== 跨章质量警告 ===\n" + "\n".join([f"⚠ {w}" for w in cross_warnings]) + "\n"
        except Exception:
            pass

    emotion_guide = EMOTION_GUIDE.get(emotion_tag, EMOTION_GUIDE["铺垫"])
    half_min = word_min // 2
    half_max = word_max // 2
    hard_rules = _format_rule_block("硬约束（必须满足）", WRITER_HARD_CONSTRAINTS)
    forbidden_rules = _format_rule_block("禁止项（必须避免）", WRITER_FORBIDDEN_RULES)
    beat_block = (
        "【本章节拍计划（按顺序推进）】\n"
        f"{beat_plan or '未生成节拍，请严格围绕本章目标推进'}"
    )

    transition_block = ""
    if prev_chapter_ending and chapter_num > 1:
        transition_block = f"""
【上一章结尾（必须自然衔接）】
...{prev_chapter_ending}

衔接规则（严格遵守）：
1. 开头禁止复用上一章结尾的句子、意象或核心词（避免读者有重复感）
2. 本章第一段必须与上一章结尾在时间/空间/情绪上形成逻辑连续，同时有明显的向前推进
3. 若场景发生切换，用一句话交代：时间跳跃了多久，或主角如何到达新地点
4. 若上一章结尾是情绪性语句，本章开头必须先给出具体行动而非重复情绪
5. 开头尽快进入人物正在做的事，地点简要交代"""

    header = ""
    if progress_block:
        header += progress_block + "\n\n"

    retry_block = ""
    if retry_feedback:
        retry_block = f"\n【本次必须修复的问题】\n{retry_feedback}\n"

    return f"""{header}现在要写第{chapter_num}章，{half_min}-{half_max}字，是完整章节的前半部分。

【本章要做什么】
{plot_goal}

【本章的情绪节奏】
{emotion_tag}——{emotion_guide}

【世界背景（只交代理解本章行动所需的信息，可直接简述）】
{world}

【人物现状（写作时通过行动和对话体现性格，不要贴标签）】
{chars_str}

{behavior_constraint_block}


{outline_block_fs}

【前面发生了什么】
{s_str}
{intent_block}
{fingerprint_block}
{cross_quality_block}
{transition_block}
{hard_rules}

{forbidden_rules}

{beat_block}

{HUMAN_WRITING_TECHNIQUES}

{NEGATIVE_EXAMPLES}

{retry_block}
开始写。第一行是章节标题（第{chapter_num}章 + 你拟的标题），然后直接进入正文。
只写前半段节拍，停在一个自然转折处。把任务卡的最终结果留给后半段。
请严格控制在{half_min}-{half_max}字范围内。"""


def build_continue_prompt(chapter_num: int, plot_goal: str,
                          emotion_tag: str, first_half: str,
                          beat_plan: str = "",
                          word_min=3000, word_max=4000,
                          retry_feedback: str = "", foreshadow_block: str = "") -> str:
    last_part = first_half[-600:] if len(first_half) > 600 else first_half
    emotion_guide = EMOTION_GUIDE.get(emotion_tag, EMOTION_GUIDE["铺垫"])
    half_min = max(1, word_min - len(first_half) - 2)
    half_max = max(half_min, word_max - len(first_half) - 2)
    hard_rules = _format_rule_block("硬约束（必须满足）", WRITER_HARD_CONSTRAINTS)
    forbidden_rules = _format_rule_block("禁止项（必须避免）", WRITER_FORBIDDEN_RULES)

    retry_block = ""
    if retry_feedback:
        retry_block = f"\n【本次必须修复的问题】\n{retry_feedback}\n"

    return f"""这章的任务：{plot_goal}
{foreshadow_block}
这章的感觉：{emotion_tag}——{emotion_guide}

本章节拍计划（后半段以收束本章节点为主）：
{beat_plan or "未提供"}

{hard_rules}

{forbidden_rules}

【前半段结尾（从这里接着写，不要重复这段内容）】
...{last_part}

接着写后半段，{half_min}-{half_max}字，把这章写完。

【结尾】
写到任务卡指定的结果就停，可以直述人物的下一步选择。
不重复前文，不追加未安排的谜团，不用固定画面、感官细节或议论拉长结尾。

{NEGATIVE_EXAMPLES}

{retry_block}"""

def _fit_chapter_length(content: str, minimum: int, maximum: int) -> str:
    """超长时尝试精简全文，保留结尾和伏笔，最终仍由正常审核把关。"""
    if len(content) <= maximum:
        return content

    target = (minimum + maximum) // 2
    closing = re.split(r"(?<=[。！？!?])(?=[^。！？!?])", content.strip())[-1].strip()
    shorter = clean_content(call_author_api(
        system_prompt=(
            "你是小说编辑。通过删除或缩短冗余内容精简全文，保留事实、关键线索和主角行动。"
            "不得增加新内容，最后一句原样保留。只输出完整正文。"
        ),
        user_message=f"将以下正文精简到约 {target} 字，总字符数（含标点和换行）必须在 {minimum}–{maximum} 之间。\n\n{content}",
        temperature=0.25,
        max_tokens=min(int(maximum * 1.4), 7000),
    ))
    if minimum <= len(shorter) <= maximum and shorter.endswith(closing):
        print(f"  [精简] 超长正文从 {len(content)} 字调整为 {len(shorter)} 字")
        return shorter
    print("  [精简] 未得到符合字数范围的版本，保留原稿供审稿判断")
    return content


def chapter_length_issue(content: str) -> str:
    size = len(content.strip())
    minimum = cfg("novel", "chapter_word_min", 3000)
    maximum = cfg("novel", "chapter_word_max", 4000)
    if not minimum <= size <= maximum:
        return f"正文字数为 {size}，须在 {minimum}–{maximum} 字之间"
    return ""


def clean_content(text: str) -> str:
    text = re.sub(r'^\s*-{3,}\s*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'\*([^*\n]+)\*', r'\1', text)
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()


# ==================== 局部修改函数 ====================

def revise_chapter(novel_name: str, chapter_num: int,
                   original_content: str, feedback: str,
                   plot_goal: str, emotion_tag: str = "铺垫",
                   beat_plan: str = "") -> str:
    """基于审核反馈对原文进行局部修改，保留未被指出问题的段落。"""
    mm = MemoryManager(novel_name)
    ctx = mm.load_context(chapter_num)
    if ctx["summary_history_incomplete"]:
        print("  [提示] 本章已被阶段摘要覆盖，重写时可能缺少早期逐章记忆")
    ctx["novel_name"] = novel_name

    word_target = cfg("novel", "chapter_word_target", 3500)
    word_min = cfg("novel", "chapter_word_min", 3000)
    word_max = cfg("novel", "chapter_word_max", 4000)
    max_tokens_cfg = cfg("model", "max_tokens", 4096)

    author_style = AUTHOR_STYLES["1"]
    style_path = get_data_dir(novel_name) / "style.txt"
    if style_path.exists():
        style_key = style_path.read_text(encoding="utf-8").strip()
        if style_key.startswith("custom:"):
            custom_desc = style_key[7:].strip()
            system_prompt = f"""你是一位专业的中文网络小说作家。
你的写作风格特点：{custom_desc}

你修改时只动有问题的地方，保留原文的语气、节奏和好的段落。"""
        else:
            author_style = AUTHOR_STYLES.get(style_key, AUTHOR_STYLES["1"])
            system_prompt = author_style["system"]
    else:
        system_prompt = AUTHOR_STYLES["1"]["system"]

    hard_rules = _format_rule_block("硬约束（必须满足）", WRITER_HARD_CONSTRAINTS)
    forbidden_rules = _format_rule_block("禁止项（必须避免）", WRITER_FORBIDDEN_RULES)

    revise_prompt = f"""【原文】
{original_content}

【修改要求】
{feedback}

【本章任务】
{plot_goal}（{emotion_tag}）
{format_chapter_foreshadow(ctx, chapter_num)}

{hard_rules}

{forbidden_rules}

根据任务卡修复因果，删除越界或重复内容。正文控制在{word_min}–{word_max}字。
只输出修改后的完整正文，不要任何说明。"""

    print(f"  [修改] 正在基于原文进行局部修改...")
    full_content = call_author_api(
        system_prompt=system_prompt + "\n\n" + REVISION_SYSTEM,
        user_message=revise_prompt,
        temperature=cfg("temperature", "revision", 0.70),
        max_tokens=min(int(word_max * 1.75), 7000),
    )
    full_content = clean_content(full_content)
    print(f"  [修改] 局部修改完成：{len(full_content)}字")

    if len(full_content) < word_min * 0.5:
        print("  [修改] 修改结果过短，回退到原文")
        full_content = original_content

    # 去AI化
    full_content = _polish_chapter(full_content, max_tokens_cfg, word_min)
    full_content = re.sub(r'\n{3,}', '\n\n', full_content)

    # 跨章词频检查（优化C）
    cross_warnings = _check_cross_chapter_frequency(novel_name, chapter_num)
    if cross_warnings:
        print(f"  [自检] 跨章词频异常：")
        for w in cross_warnings:
            print(f"    ⚠ {w}")

    # 字数补写
    supplement_round = 0
    while len(full_content) < word_min and supplement_round < MAX_SUPPLEMENT_ROUNDS:
        if len(full_content) >= word_max:
            break
        shortage = word_min - len(full_content)
        supplement_round += 1
        print(f"  [补写] 字数不足（{len(full_content)}/{word_min}），"
              f"第{supplement_round}轮补充约{shortage}字...")
        supplement = call_author_api(
            system_prompt=SUPPLEMENT_SYSTEM,
            user_message=(
                f"当前章节结尾内容：\n...{full_content[-400:]}\n\n"
                f"本章目标：{plot_goal}\n"
                f"{format_chapter_foreshadow(ctx, chapter_num)}\n"
                f"仅在因果或行动尚未完成时补充，最多{shortage}字；已完整则返回空字符串。"
            ),
            temperature=cfg("temperature", "writing_supplement", 0.75),
            max_tokens=1024,
        )
        supplement = clean_content(supplement)
        if not supplement:
            break
        full_content = f"{full_content}\n\n{supplement}"
        full_content = re.sub(r'\n{3,}', '\n\n', full_content)
        print(f"  补写完成，当前总字数：{len(full_content)}字")

    full_content = _fit_chapter_length(full_content, word_min, word_max)
    total = len(full_content)

    print(f"  [OK] 第{chapter_num}章修改完成，总字数：{total}字")
    mm.save_chapter(
        chapter_num, f"第{chapter_num}章",
        full_content, "草稿",
        word_target=word_target,
        plot_goal=plot_goal,
        emotion_tag=emotion_tag,
    )
    return full_content


# ==================== 主写作函数 ====================

def write_chapter(novel_name: str, chapter_num: int,
                  plot_goal: str, emotion_tag: str = "铺垫",
                  retry_feedback: str = "") -> str:
    mm = MemoryManager(novel_name)
    ctx = mm.load_context(chapter_num)
    if ctx["summary_history_incomplete"]:
        print("  [提示] 本章已被阶段摘要覆盖，重写时可能缺少早期逐章记忆")
    ctx["novel_name"] = novel_name

    word_target = cfg("novel", "chapter_word_target", 3500)
    word_min = cfg("novel", "chapter_word_min", 3000)
    word_max = cfg("novel", "chapter_word_max", 4000)
    max_tokens_cfg = cfg("model", "max_tokens", 4096)
    draft_status = "草稿"

    # 读取风格
    author_style = AUTHOR_STYLES["1"]
    style_path = get_data_dir(novel_name) / "style.txt"
    if style_path.exists():
        style_key = style_path.read_text(encoding="utf-8").strip()
        if style_key.startswith("custom:"):
            custom_desc = style_key[7:].strip()
            system_prompt = f"""你是一位专业的中文网络小说作家。
你的写作风格特点：{custom_desc}

你的核心写作哲学：
- 情绪可以简洁直述，行动优先，不堆感官描写
- 对话有潜台词，字里行间藏着角色没说出口的意思
- 每个场景都在改变一件事，哪怕只是一个人对另一个人的看法

你写的东西自然流畅，像一个有经验的作者在讲故事，不端着。"""
        else:
            author_style = AUTHOR_STYLES.get(style_key, AUTHOR_STYLES["1"])
            system_prompt = author_style["system"]
    else:
        system_prompt = AUTHOR_STYLES["1"]["system"]

    # 获取上一章结尾
    prev_chapter_ending = ""
    if chapter_num > 1:
        prev_chapter_ending = mm.get_last_chapter_ending(chapter_num)
        if prev_chapter_ending:
            print(f"  [衔接] 已获取第{chapter_num - 1}章结尾（{len(prev_chapter_ending)}字）")

    # 节拍规划（重试时复用缓存，任务卡不变则节拍不变）
    global _cached_beat_plan
    if _cached_beat_plan and retry_feedback:
        # 优先级1：内存缓存（同进程内重试）
        beat_plan = _cached_beat_plan
        beat_count = len([ln for ln in beat_plan.splitlines() if ln.strip()])
        print(f"  [复用] 沿用本次节拍规划（共{beat_count}条）")
    elif retry_feedback:
        # 优先级2：DB缓存（跨会话重试）
        cached = _load_beats_cache(novel_name, chapter_num)
        if cached:
            beat_plan = cached
            _cached_beat_plan = cached
            beat_count = len([ln for ln in beat_plan.splitlines() if ln.strip()])
            print(f"  [复用] 从数据库恢复节拍规划（共{beat_count}条）")
        else:
            print(f"  正在规划第{chapter_num}章节拍...")
            beat_plan = _plan_chapter_beats(ctx, chapter_num, plot_goal, emotion_tag)
            if beat_plan:
                _cached_beat_plan = beat_plan
                _save_beats_cache(novel_name, chapter_num, beat_plan)
                beat_count = len([ln for ln in beat_plan.splitlines() if ln.strip()])
                print(f"  节拍规划完成：{beat_count}条")
            else:
                print("  [提示] 节拍规划未返回有效结果，按目标直接推进")
    else:
        # 优先级3：正常首次写作
        print(f"  正在规划第{chapter_num}章节拍...")
        beat_plan = _plan_chapter_beats(ctx, chapter_num, plot_goal, emotion_tag)
        if beat_plan:
            _cached_beat_plan = beat_plan
            _save_beats_cache(novel_name, chapter_num, beat_plan)
            beat_count = len([ln for ln in beat_plan.splitlines() if ln.strip()])
            print(f"  节拍规划完成：{beat_count}条")
        else:
            print("  [提示] 节拍规划未返回有效结果，按目标直接推进")

    # 智能检测：判断是否使用大模型一次性生成
    use_single_pass = is_high_capacity_model()
    
    if use_single_pass:
        # ========== 大模型策略：一次性生成整章（1次API调用）==========
        print(f"  🚀 检测到大容量模型，采用「一次性生成」模式")
        print(f"  正在生成第{chapter_num}章（完整章节·{emotion_tag}）...")
        
        prompt = build_full_chapter_prompt(
            ctx, chapter_num, plot_goal, emotion_tag, beat_plan,
            prev_chapter_ending=prev_chapter_ending,
            word_min=word_min, word_max=word_max,
            retry_feedback=retry_feedback
        )

        full_content = call_author_api(
            system_prompt=system_prompt + "\n" + "\n".join(WRITER_HARD_CONSTRAINTS),
            user_message=prompt,
            temperature=cfg("temperature", "writing_main", 0.85),
            max_tokens=min(int(word_max * 1.75), 7000),
        )
        full_content = clean_content(full_content)
        print(f"  ✅ 章节完成：{len(full_content)}字（单次生成）")
        
    else:
        # ========== 小模型策略：分前后半段生成（2次API调用）==========
        print(f"  📝 采用标准模式：前后半段分段生成")
        
        # 前半段
        print(f"  正在生成第{chapter_num}章（前半段·{emotion_tag}）...")
        prompt = build_writer_prompt(
            ctx, chapter_num, plot_goal, emotion_tag, beat_plan,
            prev_chapter_ending=prev_chapter_ending,
            word_min=word_min, word_max=word_max,
            retry_feedback=retry_feedback
        )
        first_half = call_author_api(
            system_prompt=system_prompt + "\n" + "\n".join(WRITER_HARD_CONSTRAINTS),
            user_message=prompt,
            temperature=cfg("temperature", "writing_first_half", 0.90),
            max_tokens=min(int(word_max // 2 * 1.5), max_tokens_cfg),
        )
        first_half = clean_content(first_half)
        print(f"  前半段完成：{len(first_half)}字")

        # 后半段
        print(f"  正在生成第{chapter_num}章（后半段）...")
        continue_prompt = build_continue_prompt(
            chapter_num, plot_goal, emotion_tag, first_half, beat_plan,
            word_min=word_min, word_max=word_max,
            retry_feedback=retry_feedback,
            foreshadow_block=format_chapter_foreshadow(ctx, chapter_num)
        )
        second_half = call_author_api(
            system_prompt=system_prompt + "\n\n" + CONTINUE_SYSTEM_BASE,
            user_message=continue_prompt,
            temperature=cfg("temperature", "writing_second_half", 0.70),
            max_tokens=min(int(word_max // 2 * 1.5), max_tokens_cfg),
        )
        second_half = clean_content(second_half)
        print(f"  后半段完成：{len(second_half)}字")

        full_content = f"{first_half}\n\n{second_half}"
        del first_half, second_half
        full_content = re.sub(r'\n{3,}', '\n\n', full_content)

    # 检测与上一章结尾的潜在复用（两种生成模式均需检查）
    if prev_chapter_ending and len(prev_chapter_ending) > 50:
        content_start = full_content[:100] if len(full_content) > 100 else full_content
        if _count_matching_words(prev_chapter_ending[-50:], content_start) > 15:
            print("  [警告] 检测到与上一章结尾的潜在复用，将标记修订")
            draft_status = "草稿(有问题)"

    # 字数补写
    min_words = word_min
    max_words = word_max
    supplement_round = 0
    while len(full_content) < min_words and supplement_round < MAX_SUPPLEMENT_ROUNDS:
        if len(full_content) >= max_words:
            print(f"  [补写] 已达字数上限（{len(full_content)}/{max_words}），停止补写")
            break
        shortage = min_words - len(full_content)
        supplement_round += 1
        print(f"  [补写] 字数不足（{len(full_content)}/{min_words}），"
              f"第{supplement_round}轮补充约{shortage}字...")
        supplement = call_author_api(
            system_prompt=SUPPLEMENT_SYSTEM,
            user_message=(
                f"当前章节结尾内容：\n...{full_content[-400:]}\n\n"
                f"本章目标：{plot_goal}\n"
                f"{format_chapter_foreshadow(ctx, chapter_num)}\n"
                f"仅在因果或行动尚未完成时补充，最多{shortage}字；已完整则返回空字符串。"
            ),
            temperature=cfg("temperature", "writing_supplement", 0.75),
            max_tokens=1024,
        )
        supplement = clean_content(supplement)
        if not supplement:
            print("  [补写] 未获得有效补写内容，停止补写")
            break
        full_content = f"{full_content}\n\n{supplement}"
        del supplement
        full_content = re.sub(r'\n{3,}', '\n\n', full_content)
        print(f"  补写完成，当前总字数：{len(full_content)}字")

    # 自检与修订
    full_content = _polish_chapter(full_content, max_tokens_cfg, word_min)
    full_content = re.sub(r'\n{3,}', '\n\n', full_content)

    # 跨章词频检查（优化C）
    cross_warnings = _check_cross_chapter_frequency(novel_name, chapter_num)
    if cross_warnings:
        print(f"  [自检] 跨章词频异常：")
        for w in cross_warnings:
            print(f"    ⚠ {w}")

    full_content = _fit_chapter_length(full_content, word_min, word_max)
    total = len(full_content)

    print(f"  [OK] 第{chapter_num}章完成，总字数：{total}字（目标：{word_min}-{word_max}）")

    if total < word_min:
        print(f"  ⚠️ 警告：字数不足（{total}/{word_min}），建议手动检查或重写")
    elif total <= int(word_max * 1.1):
        if total > word_max:
            excess = total - word_max
            excess_pct = (excess / word_max) * 100
            print(f"  🟢 略超: 字数略超上限（{total}/{word_max}，+{excess_pct:.0f}%，+{excess}字）")
    elif total <= int(word_max * 1.3):
        excess = total - word_max
        excess_pct = (excess / word_max) * 100
        print(f"  🟡 标超: 字数超标（{total}/{word_max}，+{excess_pct:.0f}%，+{excess}字）| 建议检查是否可精简")
    else:
        excess = total - word_max
        excess_pct = (excess / word_max) * 100
        print(f"  🔴 严重超标: 字数严重超标（{total}/{word_max}，+{excess_pct:.0f}%，+{excess}字）| 建议重写或手动裁剪")

    mm.save_chapter(
        chapter_num, f"第{chapter_num}章",
        full_content, draft_status,
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
    choice = input("请输入编号（默认1）：", options=tuple(
        (key, item["name"]) for key, item in AUTHOR_STYLES.items()
    )).strip() or "1"
    if choice not in AUTHOR_STYLES:
        choice = "1"
    selected = AUTHOR_STYLES[choice]
    print(f"\n[OK] 已选择风格：{selected['name']}\n")
    return choice
