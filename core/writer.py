import json
import re
from core.api_client import call_author_api, increment_failure_counter, reset_failure_counter, get_current_author_model
from core.memory_manager import MemoryManager
from core.config_loader import get as cfg, get_data_dir
from core.utils import extract_json_obj, is_transient_error


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
        rhythm = "重点建立世界感和人物关系，植入悬念，节奏稳健，不要急着上大冲突"
    elif pct <= 50:
        phase = "发展前期"
        rhythm = "矛盾开始升级，每段都要有新信息或新冲突推进，支线开始交织"
    elif pct <= 75:
        phase = "发展后期"
        rhythm = "矛盾烈度明显上升，伏笔开始回收，人物关系趋于紧张，节奏加快"
    elif pct <= 90:
        phase = "高潮阶段"
        rhythm = "全面爆发期，伏笔全部兑现，节奏最快，每章都要有强烈冲突或反转"
    else:
        phase = "收尾阶段"
        rhythm = "矛盾收束，各条线索回归，给读者完整感和情感落地"
    return (
        f"【全书进度：第{chapter_num}/{target_chapters}章（{pct}%·{phase}）】\n"
        f"节奏指引：{rhythm}"
    )


def _build_outline_block(outline: str, max_chars: int = 1200) -> str:
    """
    生成全书大纲提示块。outline 为空时返回空字符串。
    max_chars 控制传入提示词的最大长度（大模型可放宽）。
    """
    if not outline:
        return ""
    trimmed = outline[:max_chars]
    if len(outline) > max_chars:
        trimmed += "\n…（大纲后续略）"
    return (
        "【全书大纲（把握宏观走向，当前章节内容要服务于整体节奏，不要提前交代结局）】\n"
        + trimmed
    )


def build_full_chapter_prompt(ctx, chapter_num, plot_goal, emotion_tag,
                              beat_plan, prev_chapter_ending="",
                              word_min=3000, word_max=4000) -> str:
    """构建一次性生成整章的prompt（用于大模型）"""
    # ── 进度 & 大纲（新增）──────────────────────────────────────
    progress_block = _build_progress_block(chapter_num, ctx.get("target_chapters", 0))
    outline_block  = _build_outline_block(ctx.get("outline", ""), max_chars=1500)

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

    # ── 伏笔（统一使用优先级排序后的 hints，不再重复原始列表）──
    foreshadow_hints = ctx.get("foreshadow_hints", [])
    if foreshadow_hints:
        f_block = "【伏笔提示（按优先级排序，逾期/久悬的必须本章处理）】\n" + "\n".join([f"- {h}" for h in foreshadow_hints])
    else:
        f_block = "（暂无需处理的伏笔）"

    summaries = ctx.get("recent_summaries", [])
    s_str = "\n".join(
        [f"第{s['chapter_num']}章：{s['summary']}" for s in summaries]
    ) if summaries else "这是开篇第一章"

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
5. 开头第一句话要有画面感，让读者立刻能想象出场景"""

    # 进度/大纲放在最前，让 AI 先建立宏观认知
    header = ""
    if progress_block:
        header += progress_block + "\n\n"
    if outline_block:
        header += outline_block + "\n\n"

    return f"""{header}现在要写第{chapter_num}章的完整内容，{word_min}-{word_max}字。

【本章要做什么】
{plot_goal}

【本章的情绪节奏】
{emotion_tag}——{emotion_guide}

【世界背景（写作时要体现在细节里，不要直接解释）】
{world}

【人物现状（写作时通过行动和对话体现性格，不要贴标签）】
{chars_str}

{behavior_constraint_block}
{f_block}

【前面发生了什么】
{s_str}
{transition_block}
{hard_rules}

{forbidden_rules}

{beat_block}

{HUMAN_WRITING_TECHNIQUES}

{NEGATIVE_EXAMPLES}

开始写。第一行是章节标题（第{chapter_num}章 + 你拟的标题），然后直接进入正文。
请严格控制在{word_min}-{word_max}字范围内。如果某个场景还没写透，宁愿少推进一步也不要压缩细节。"""

# ==================== 作者风格系统 ====================

AUTHOR_STYLES = {
    "1": {
        "name": "爽文宗师",
        "desc": "主角光环强，打脸爽，节奏快，读者看了直呼过瘾",
        "system": """你是一位写了十五年网文的老作者，手下有一百多万字的完结作品。
你知道读者在哪一行会截图，在哪一行会发"妈的这也太爽了"。

你写作时脑子里只有一件事：这段话完了，读者想不想往下翻？

你有几个根深蒂固的习惯：
- 你从不直接说主角有多强。你只写他做了什么，然后让旁边的人反应——一个喝水的人把杯子放下了，一个说话的人停顿了两秒，一个一向傲慢的人低下了头。读者自己会换算。
- 对手不蠢。他们输是因为主角比他们早了三步，而读者翻回去看的时候会发现这三步早就埋好了。"原来如此"的感觉比"哇好厉害"更能让人爽。
- 你的节奏是有意识的：铺垫部分你会故意把节奏压住，像弹弓拉满，然后爽点一句话崩出来，读者还没反应过来就已经屏住呼吸了。
- 你的对话短。主角最有力量的话往往是一句，甚至半句，然后让对方去消化。
- 你的章节末尾永远让人想继续。可以是主角下一步棋的暗示，可以是对手意识到自己大事不妙的瞬间，可以是一个没人预料到的人突然出现。

你只在乎一件事：读者看完这章，明天还会回来。""",
    },
    "2": {
        "name": "悬疑大师",
        "desc": "擅长埋线索、设谜团，每章结尾都让人睡不着",
        "system": """你是一位写悬疑故事的老作者。你有一个读者不知道的秘密：你构建谜题的方式是从答案往回写，把线索一个个藏进去，但每一个都要让人觉得"这只是一个普通的细节"。

你写作时习惯性地让自己站在主角的位置，用他/她的眼睛和耳朵来感知这个世界——这样读者只能知道主角知道的，不多也不少。

你有几个积累了多年的写作直觉：
- 气氛永远先于情节。一个没有问题的场景，你会用三个不对劲的细节让读者觉得哪里有问题——说不出来，但就是不对。等读者紧张起来了，情节才来。
- 你绝不把答案给全。你给七分，读者会用剩下的三分把自己吓到。你见过太多把谜底写得太满的同行，读者看到答案反而失望。
- 你的每一个具体描写都有功能：一根头发丝、一个对话里的停顿、一个被顺手放下的东西。在第一章是细节，在第五章是线索，在第十章是证据。
- 你的章节结尾有两种：一种是一个新发现让之前所有的理解都需要重来，一种是刚刚安静下来的空气里出现了一个不应该在这里的声音。

你写的是真实发生的事，只是我们还不知道。你永远不催，因为最好的悬疑需要时间慢慢收紧。""",
    },
    "3": {
        "name": "情感流",
        "desc": "细腻描写人物情感，感情线丰富，让读者跟着人物哭和笑",
        "system": """你是一位被读者说过"你写的是我没说出口的话"的作者。你知道最打动人的东西是什么：不是悲剧，不是煽情，是那种极其普通的小事里突然出现的、让人鼻酸的重量。

你从不直接写"她很悲伤"。你写她把那条消息存到相册里，又删掉，又从回收站找回来，放在一个别人永远找不到的文件夹里。读者看完自己就难受了，不需要你解释。

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
- 战斗前的静默是你的强项。双方都没动的那几秒，你用来写空气、呼吸、手上的汗，把读者的心悬到嗓子眼，然后才让战斗开始。
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
- 历史感不靠堆词。靠那些暗示"这里发生过更多事"的细节：一块磨损的石板、一个人人都懂却没人说破的禁忌、一句"那是很久以前的事了"带过去的巨大留白。
- 新的概念和规则要用角色的感受来锚定。他惊讶、困惑、或者习以为常——这个反应告诉读者该怎么理解这个东西，比直接解释有效十倍。
- 你喜欢藏更大的谜题。读者永远感觉这个世界比他看到的大，那种"还有更多秘密"的感觉让他们停不下来。
- 你的世界让人物显得渺小，但他们的选择很重要——个人命运和世界命运交织在一起，读者为之动容不是因为个人，是因为那个选择的重量。

你写出来的东西有史诗感。读者看完一章，感觉看见了一扇窗——窗外还有更大的东西。""",
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
- 开篇从一个异常现象或技术细节入手，让读者自己意识到它意味着什么，你不直接说。
- 善用宇宙尺度的时间和空间让人类的挣扎显得渺小却又重要。
- 对话承载信息量，每一句都有作用。没有废话。
- 用类比把抽象概念变得可感知——不是解释，是让读者自己"看到"那个东西。
- 章节结尾留下开放性的哲学重量，不是简单的情绪宣泄。

你写的每一章应该让读者感受到：在浩瀚宇宙面前，人类的渺小与伟大同时存在。""",
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
- 伏笔千里。草蛇灰线，读者翻回去看才发现第一章就埋好了。

你写的不只是江湖，是人性在极端处境下的选择，是那些无法两全的东西。""",
    },
    "9": {
        "name": "古龙（悬疑武侠）",
        "desc": "短句有力，氛围营造高手，心理描写入微",
        "system": """你是一位像古龙那样的悬疑武侠作家。你的文字像刀锋，每一个句子都直击心脏。

你的写作方式几乎是本能的：

极短的段落。一行甚至半行就是一段，这不是风格，这是节奏。让读者的眼睛快起来，心跳也跟着快。

氛围先于一切。风、雪、月、酒、孤独——你用这些东西搭台子，人在台子上出现的时候已经有了重量。

对话是你的武器。你笔下的人说话机智、含蓄、充满双关——他们说的是一件事，意思是另一件事，读者要自己去译。

反转在最后。真相永远藏在最后，而且翻回去看，每一个细节都在说它，只是读者没看见。

你有个习惯：开篇不铺垫，直接进入紧张。把气氛搭起来，情节在气氛里自然发生。

你想让读者记住的不是打了几场、赢了几次，而是那种孤独的江湖气息，和人心最深处说不清的东西。""",
    },
}


# ==================== 情绪标签指南 ====================

EMOTION_GUIDE = {
    "爽点": (
        "这是读者最期待的时刻——但最好的爽不是靠对手变蠢，而是靠主角前几章埋下的那颗棋突然发动。"
        "写法：先让局势看起来很难，再让主角用一个读者没想到但完全合理的方式破局。"
        "结尾留一个更大的阶段性胜利预告，或者一个刚刚意识到被碾压的对手。"
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
        "铺垫不是什么都不发生——是让一个小变化在读者心里留下一个小钩子。"
        "写法：每个场景都要改变一样东西（哪怕只是角色对某件事的理解），至少埋一个将来会用到的细节。"
        "让读者觉得'这个应该之后会有用吧'——这种感觉就是铺垫成功的标志。"
    ),
}

# ==================== 写作硬约束 ====================

# 写作硬约束：用"作家的直觉"而非"检查清单"的方式表述
WRITER_HARD_CONSTRAINTS = [
    "跟着本章目标走。章节结束时，读者必须知道至少一件新的事，或看到至少一段关系发生了变化，或感受到冲突比开头更紧。",
    "人物不能无缘无故变。他的行为要和我们已经知道的他一致；如果他变了，读者必须能看到是什么触发了他。",
    "世界已经建立的规则不能打破。如果要打破，那就是新的情节，需要铺垫和代价。",
    "至少有2轮真正的对话——两个人来回说话，内容在做事（推进情节、改变关系、或揭示一个之前不知道的事实），不是互相解释设定。如果场景里只有主角一个人，章节开头需要安排一个合理的理由让另一个人出现。",
    "一次只聚焦一个核心情节节点。宁愿把一件事写透，也不要把三件事都写浅。",
    "场景要让人看得见。用角色的眼睛、耳朵、鼻子来呈现——他看到了什么颜色，听到了什么声音，闻到了什么气味。不要从高空往下俯视超过3句。",
    "对话不能裸奔。4行以上的连续台词之间，必须有动作、表情、停顿或环境细节把它们隔开。",
]

WRITER_FORBIDDEN_RULES = [
    # 情绪写法类
    ('禁止直接说角色的感受——不写"她感到紧张/他心中涌起/她不禁感动"，'
     '改用生理反应（手指停了、杯子没放稳）、具体动作（把东西放下又拿起来）、'
     '他人观察（另一个人注意到他的眼神变了）来传达情绪。'),
    ('禁止这些高频烂俗动作：深吸一口气平复情绪、握紧拳头下定决心、瞳孔收缩察觉危险、'
     '喉咙发紧说不出话——找一个这个角色专属的、更具体的动作替代。'),
    ('禁止在结尾出现任何版本的心理陈述（她决定了/他知道该怎么做了/她下定了决心）'
     '——用一个具体的行动来展示这个决定。'),
    # 模板句类
    ('禁止任何版本的"这才刚开始"收尾：这一切才刚刚开始/故事还远未结束/'
     '前方的路还很长/无论如何他都会坚持'
     '——结尾必须落在一个具体的动作、物品、声音、或一句没有被回答的话上。'),
    ('禁止"突然想起"或"蓦然意识到"这类无铺垫顿悟'
     '——如果角色想起了什么，读者在前文里必须见过那个东西。'),
    # 结构类
    ('禁止连续说同一件事：换个词重复一遍意思一样的句子直接删掉。'
     '每个句子要比上一句多说一点什么，或从不同角度来，或往前推进一步。'),
    ('禁止把对手写蠢来凑爽点——对手要有自己说得通的逻辑，'
     '输是因为信息不对称或准备不如主角充分，不是因为智商掉线。'),
    ('禁止用工整的对称句式表达复杂情感（一方面…另一方面、虽然…但依然），'
     '真实的人在高压时刻想的不是对称的句子。'),
    ('禁止为赶进度跳过场景过渡——如果两个场景之间有时间跳跃或空间移动，'
     '用一句具体的话交代过去了多久、或主角是怎么到达新地点的。'),
    '禁止在相邻两段内出现结构相似的比喻句；平叙段每500字内比喻/拟人不超过2处。',
]

# ==================== 节拍规划系统 ====================

BEAT_PLANNER_SYSTEM = """你是一位帮作者构思章节节拍的创作顾问。

你的任务是把这一章拆解成5-7个具体的"发生了什么"，每个都要推进情节、改变关系或揭示信息——不能原地踏步。

输出格式：只输出5-7条编号节拍，每条20-40字，描述具体发生了什么事，不要解释原因，不要输出JSON。

节拍设计要点（写节拍时脑子里要想着）：

▌开头节拍：角色在哪，处于什么状态，带着什么未解决的问题进入这一章
▌中间节拍（3-5条）：每条都要让局势向一个方向移动——不是重复上一条，而是比上一条更进一步或发生转折
  - 至少1条：一个外部事件，角色主动做了什么，产生了外部可见的结果
  - 至少1条：一个内部节拍，角色注意到/感受到某个改变，让读者停下来感受一秒
  - 至少1条：一个张力点，信息落差或冲突升级，读者开始担心接下来会发生什么
▌结尾节拍：章节结束时，某件事发生了变化（可以是关系、信息、局势），且必须留下一个让读者想往下看的钩子

一章只聚焦一个核心节点。节拍里如果出现了两个"大推进"，合并成一个。"""

# ==================== 真实写作手法指引（Human Writing Techniques）====================
# 这组提示不是规则，而是真实作者的写作本能
# 用于生成prompt时附加，让 AI 模型从"满足条件"模式切换到"讲故事"模式

HUMAN_WRITING_TECHNIQUES = """
【真实的写作手法——不是规则，是本能】

▌句子长短要有变化
真实的作者不会一直用同样长度的句子。紧张的时候句子短。空间大了，节奏慢了，句子自然就长了。
连续三个短句之后来一个长句，节奏是活的。不要每段句子长度差不多。

▌每个场景要有一个不一样的感官细节
不是"她走进了一间昏暗的房间"，而是"地板有一块漆脱落了，踩上去是空心的声音"。
那个具体的细节让读者觉得自己也在那里。每个场景至少有一个这样的细节，但不要超过三个——多了就变成背景介绍了。

▌对话要有空隙
真实的对话里有停顿、有没说完就停下来的句子、有两个人同时说话的混乱、有沉默。
不是每句话都要接上去，有时候"她没有回答"比回答更重。

▌角色说话方式各有不同
主角和配角说话不是同一个人。年纪大的、见过世面的人说话里有省略；年轻的、紧张的人说话会绕弯子。
一个角色的口头禅或者说话习惯，是读者辨认他的方式。

▌人物的情绪要藏在行为里
他紧张——他在摆弄手里的东西，他在看向不同的方向，他回答问题比平时慢了半拍。
他高兴——他走路步子大了，他主动打开了窗，他说了一句平时不会说的话。
不要说"他感到"，让读者自己感到。

▌段落结尾不要"总结"
一段话说完了，就停在最后一个细节上。不要再加一句"他的心情更加复杂了"——读者已经知道了。
真实的写作里，段落结尾往往是一个画面，不是一个结论。

▌结尾要是一个悬念或者一个动作，不是一个总结
章节最后一段要让读者想往下翻。可以是一件新出现的事，可以是一句没有被回应的话，
可以是一个刚刚发生的、但原因还不清楚的变化。不能是主角在脑子里总结"这次的收获是……"。
"""

# ==================== 自检系统 ====================

SELF_CHECK_SYSTEM = """你是网文写作质检助手，专门识别AI写作痕迹和写作质量问题。

请仅输出JSON（不要Markdown，不要解释）：
{
  "pass": true/false,
  "issues": ["问题1（引用原文具体句子）", "问题2"],
  "need_revision": true/false
}

判定标准（发现任一项即 pass=false，issues 里写明具体问题并引用原文）：

【AI痕迹检查——最高优先级】
A1. 情绪标签直陈：是否有"她感到/他知道/她不禁/他心中涌起/不由自主"等直接陈述感受的句子？（必须改为行为/生理/他人视角）
A2. 高频烂俗动作：是否有"深吸一口气/握紧拳头/瞳孔收缩/喉咙发紧"等过度使用的套路动作？
A3. 心理陈述结尾：是否有"她决定了/他知道该怎么做了/她下定决心"收束章节？
A4. 模板收束句：是否有任何版本的"这才刚开始/路还很长/无论如何"？
A5. 无铺垫顿悟：是否有"突然想起/蓦然意识到/恍然大悟"且前文无对应铺垫？
A6. 对称句式滥用：是否有过多"虽然…但依然/一方面…另一方面/不仅是…还"的工整结构？
A7. 句子等长：全文句子长度是否过于均匀，缺乏节奏变化（长短句交错才是真实写作）？

【质量检查】
Q1. 情节偏题：是否偏离本章任务卡目标？
Q2. 人物OOC：角色行为是否与已知性格不符且无触发原因？
Q3. 重复表达：是否有换个词说同一件事的冗余段落？
Q4. 场景无细节：是否有连续3句以上的纯抽象描述，没有任何具体感官细节？
Q5. 裸对话：是否有4行以上连续台词无任何动作/表情/环境间隔？
Q6. 结尾无张力：结尾是否是一个总结性/说明性的句子，而不是一个具体画面或悬念？"""

# ==================== 修订与续写系统 ====================

NEGATIVE_EXAMPLES = """
【反面教材——这些句子让读者一眼看出是AI写的】

情绪直陈（直接说感受，应改用行为/生理/他人视角）：
❌ "他感到紧张，心跳加速。"
❌ "她心中涌起一股暖意，眼眶有些湿润。"
❌ "他不禁有些感动，内心深处涌起了复杂的情绪。"
❌ "她感到一阵轻松，心情好了很多。"
✓ 改：他的手按在桌沿上，指节微微发白。
✓ 改：她转过脸，盯着窗外的路灯看了很久。
✓ 改：陈默注意到他说完那句话之后就没再说话了，茶杯还没动过。

心理陈述（直接说决定/认知，应改用行动展示）：
❌ "她决定了，无论如何都要找到答案。"
❌ "他知道该怎么做了，心中已有了计划。"
❌ "她意识到，自己一直以来都错了。"
❌ "他突然想起了什么，脸色微变。"
✓ 改：她把手机屏幕翻过来，扣在桌上。
✓ 改：他停了一下，把那份文件重新放进了抽屉。
✓ 改：她看了他一眼，什么都没说，走向另一个方向。

模板收束句（任何版本的"这才刚开始"）：
❌ "这一切才刚刚开始。"
❌ "前方的路还很长，而他必须勇敢地走下去。"
❌ "无论前方有多少困难，他都会坚持下去。"
❌ "他知道，这只是一个开始。"
❌ "故事还远未结束，而他们，才刚刚出发。"
✓ 改：通讯器突然响了。屏幕上是一个陌生的号码。
✓ 改：她把那张纸叠好，放进了口袋。门关上的声音很轻。
✓ 改：林深在原地站了片刻，然后走向了相反的方向。

高频烂俗动作（深吸一口气/握紧拳头/瞳孔收缩）：
❌ "他深吸一口气，平复了一下情绪。"
❌ "她握紧拳头，下定决心。"
❌ "他的瞳孔微微收缩，察觉到了危险。"
✓ 改：他把手里的杯子放下，没喝。
✓ 改：她把钱包在手心攥了一下，然后站起来了。
✓ 改：他的眼神扫过去又扫回来，停在那扇门上。

结构过于工整（AI喜欢的对称句式）：
❌ "一方面，他想留下来；另一方面，他知道自己不能。"
❌ "虽然她很害怕，但她依然选择了前进。"
✓ 改：他拿起了包，又放下了。又拿起来，这次没放下。
✓ 改：她走出去了。脚步是快的，但手没有放开门把。
"""

REVISION_SYSTEM = f"""你是专业的网文修订师，专门消除AI写作痕迹，让文字听起来像真人写的。

修订原则：
- 只动有问题的地方，保留原文的语气和节奏
- 改情绪标签时，想想这个角色在这一刻会做什么具体的小动作
- 改模板收束时，找到这段里最后一个具体的画面停在那里
- 改对称句式时，把其中一半打破——让它更像人在说话，不像人在背稿子
- 改等长句子时，把其中几句劈短，或者把两句合成一句长的

{NEGATIVE_EXAMPLES}

只输出修订后的完整正文，不要任何说明。"""

CONTINUE_SYSTEM_BASE = """你正在续写一章小说的后半部分。

你只需要做一件事：像同一个作者继续写，不像换了一个人。

具体要求：
- 直接接着上文写，第一句不能重复上文最后出现过的任何词语或意象
- 文风、视角、叙事节奏和前半段一致——用同样长短的句子，同样的人称，同样的远近感
- 后半段可以稍微加速，把情节推向本章的落地点，但每个场景仍然要有细节
- 最后一段要落在一个画面、一个声音、或一句没有被回应的话上，不是总结

禁止出现的写法（写出来就是AI）：
- 他/她知道/感到/意识到/明白了 + 情绪从句
- 深吸一口气/握紧拳头/瞳孔收缩 这类套路动作
- 这才刚刚开始/前方的路还很长/无论如何 这类总结句
- 她决定了/她知道该怎么做了 这类心理陈述结尾

只输出正文，不要任何说明。"""

SUPPLEMENT_SYSTEM = """你是一位专业的中文网络小说作家，正在为一章小说补充内容。

补充要点：
- 直接接着上文写，第一句不能出现上文最后出现过的任何词语
- 保持完全一样的文风、人称和叙事视角——像同一个人继续写，不是换了一个人
- 补充约500字：可以是一个场景的延伸，可以是一段漏掉的对话，可以是一个需要时间展开的感知时刻
- 有意义的字，不是注水——每句话都在说一件原本就应该在这里的事
- 句子长短要有变化，不要全是同样节奏的句子
- 禁止出现情绪标签（感到/心中涌起/不禁）、模板句（才刚开始/路还很长）或高频烂俗动作（深吸一口气/握紧拳头）
- 只输出补充的正文，不要任何说明和标注"""

CHAPTER_MIN_RATIO = 0.90
MAX_SUPPLEMENT_ROUNDS = 3


# ==================== AI痕迹规则检测 ====================
# 检测中文网文中最常见的 AI 写作特征，每新增一类就在这里补充

AI_PATTERNS = [
    # ── 情绪直陈类（直接说角色感受，应改用行为/生理反应）──────
    (r'[他她][知感明白意识]道[，,][^。]{2,60}[。]?',    '情绪直陈-他/她知道/感到'),
    (r'[他她]心中涌起[^，。]{2,20}[，。]',              '情绪标签-心中涌起'),
    (r'[他她]感到[^，。]{2,20}[，。]',                  '情绪标签-感到X'),
    (r'[他她]内心深处[^，。]{2,20}[，。]',              '情绪标签-内心深处'),
    (r'[他她]不禁[^，。]{2,20}[，。]',                  '情绪标签-不禁X'),
    (r'[他她]不由[自]?主[地的]',                        '情绪标签-不由自主'),
    (r'[一股|一阵][^，。]{2,8}(之情|感|感觉)涌上[^，。]{2,10}[，。]', '情绪标签-情绪涌上'),
    (r'心头[一紧|一颤|一沉|一热|一暖]',                 '情绪标签-心头一X'),
    (r'喉咙[一紧|发紧|发干|哽咽]',                      '生理标签-喉咙X'),
    (r'瞳孔[收缩|放大|微缩]',                           '生理标签-瞳孔X'),
    (r'[他她]深吸[了]?一口气[，。]',                    '高频动作-深吸一口气'),
    (r'[他她]握紧[了]?拳头[，。]',                      '高频动作-握紧拳头'),
    (r'[他她]咬[紧了]?[牙关|嘴唇][，。]',               '高频动作-咬紧牙关'),
    (r'心跳[加速|不已|狂跳]',                           '生理标签-心跳X'),
    (r'脑海中[突然]?浮现[出]?',                         'AI痕迹-脑海浮现'),

    # ── 心理陈述类（直接说角色决定/认知）──────────────────────
    (r'[他她]决定了[，。]',                             '心理陈述-决定了'),
    (r'[他她]知道该怎么做了[，。]',                      '心理陈述-知道该怎么做'),
    (r'[他她]下定了决心[，。]',                          '心理陈述-下定决心'),
    (r'[他她]突然想[起到明白][^，。]{2,30}[，。]',       '心理陈述-突然想起'),
    (r'[他她]意识到[，,][^。]{2,40}[。]',               '心理陈述-意识到'),
    (r'[他她]明白了[，。]',                              '心理陈述-明白了'),
    (r'[他她]恍然大悟[，。]',                            '心理陈述-恍然大悟'),

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
    (r'"[^"]{3,60}"\n"[^"]{3,60}"\n"[^"]{3,60}"\n"[^"]{3,60}"', '连续裸对话≥4行'),
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

    # 大纲摘要（给节拍规划提供宏观方向，截短避免 prompt 过长）
    outline = ctx.get("outline", "")
    outline_str = f"\n全书大纲摘要：{outline[:400]}" if outline else ""

    progress_str = f"\n{progress_block}" if progress_block else ""

    prompt = f"""章节：第{chapter_num}章
本章目标：{plot_goal}
情绪标签：{emotion_tag}{progress_str}{outline_str}

世界背景摘要：
{world or "暂无"}

关键角色状态：
{chars_str}
{recent_str}

请给出本章5-7条节拍计划。"""
    raw = call_author_api(
        system_prompt=BEAT_PLANNER_SYSTEM,
        user_message=prompt,
        temperature=cfg("temperature", "beat_planner", 0.65),
        max_tokens=600,
    )
    return _normalize_beats(clean_content(raw))


# ==================== 自检与修订 ====================

def _self_check_and_revise(system_prompt: str, chapter_num: int,
                           plot_goal: str, emotion_tag: str,
                           full_content: str, beat_plan: str,
                           max_tokens: int) -> str:
    hard_rules = _format_rule_block("硬约束", WRITER_HARD_CONSTRAINTS)
    forbidden_rules = _format_rule_block("禁止项", WRITER_FORBIDDEN_RULES)

    detected_issues = _rule_based_ai_check(full_content)

    if detected_issues:
        print(f"  [自检] 发现{len(detected_issues)}项AI痕迹问题，执行修订...")
        issue_lines = "\n".join([f"- {x}" for x in detected_issues])
        revise_prompt = f"""请根据问题清单直接修订正文，输出完整章节。

章节：第{chapter_num}章
本章目标：{plot_goal}
情绪标签：{emotion_tag}

本章节拍计划：
{beat_plan or "未提供"}

{hard_rules}

{forbidden_rules}

【必须修复的问题 - 全部是AI痕迹】
{issue_lines}

【修复要求】
1. 用具体行为/生理反应替换所有"他知道/她感到"类心理陈述
2. 用具体感官细节替换所有情绪标签直给
3. 删掉所有"才刚开始/路还很长/无论前方"类模板句
4. 对话必须与动作/表情/环境描写交替出现
5. 结尾落在一个具体的行动或画面，不能是总结句

【待修订正文】
{full_content}"""
        revised = call_author_api(
            system_prompt=system_prompt + "\n\n" + REVISION_SYSTEM,
            user_message=revise_prompt,
            temperature=cfg("temperature", "revision", 0.70),
            max_tokens=max_tokens,
        )
        revised = clean_content(revised)
        if revised and len(revised) >= int(len(full_content) * 0.65):
            print(f"  [自检] 修订完成：{len(revised)}字")
            return revised

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
    raw = call_author_api(
        system_prompt=SELF_CHECK_SYSTEM,
        user_message=check_prompt,
        temperature=cfg("temperature", "self_check", 0.20),
        max_tokens=600,
    )
    result = extract_json_obj(raw)
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
        issues = ["情节推进、节奏或人物行为仍有改进空间"]
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

【必须修复的问题】
{issue_lines}

【待修订正文】
{full_content}
"""
    revised = call_author_api(
        system_prompt=system_prompt + "\n\n" + REVISION_SYSTEM,
        user_message=revise_prompt,
        temperature=cfg("temperature", "revision", 0.70),
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


# ==================== 提示词构建 ====================

def build_writer_prompt(ctx: dict, chapter_num: int,
                        plot_goal: str, emotion_tag: str,
                        beat_plan: str = "",
                        prev_chapter_ending: str = "",
                        word_min=3000, word_max=4000) -> str:
    # ── 进度 & 大纲（小模型上下文有限，大纲截短到600字）────────
    progress_block = _build_progress_block(chapter_num, ctx.get("target_chapters", 0))
    outline_block  = _build_outline_block(ctx.get("outline", ""), max_chars=600)

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

    # ── 伏笔（统一使用优先级排序后的 hints）────────────────────
    foreshadow_hints = ctx.get("foreshadow_hints", [])
    if foreshadow_hints:
        f_block = "【伏笔提示（按优先级排序，逾期/久悬的必须本章处理）】\n" + "\n".join([f"- {h}" for h in foreshadow_hints])
    else:
        f_block = "（暂无需处理的伏笔）"

    summaries = ctx.get("recent_summaries", [])
    s_str = "\n".join(
        [f"第{s['chapter_num']}章：{s['summary']}" for s in summaries]
    ) if summaries else "这是开篇第一章"

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
5. 开头第一句话要有画面感，让读者立刻能想象出场景"""

    header = ""
    if progress_block:
        header += progress_block + "\n\n"
    if outline_block:
        header += outline_block + "\n\n"

    return f"""{header}现在要写第{chapter_num}章，{half_min}-{half_max}字，是完整章节的前半部分。

【本章要做什么】
{plot_goal}

【本章的情绪节奏】
{emotion_tag}——{emotion_guide}

【世界背景（写作时要体现在细节里，不要直接解释）】
{world}

【人物现状（写作时通过行动和对话体现性格，不要贴标签）】
{chars_str}

{behavior_constraint_block}
{f_block}

【前面发生了什么】
{s_str}
{transition_block}
{hard_rules}

{forbidden_rules}

{beat_block}

{HUMAN_WRITING_TECHNIQUES}

{NEGATIVE_EXAMPLES}

开始写。第一行是章节标题（第{chapter_num}章 + 你拟的标题），然后直接进入正文。
写到一个自然的停顿点停下，后半段另外续写。
请严格控制在{half_min}-{half_max}字范围内。"""


def build_continue_prompt(chapter_num: int, plot_goal: str,
                          emotion_tag: str, first_half: str,
                          beat_plan: str = "",
                          word_min=3000, word_max=4000) -> str:
    last_part = first_half[-600:] if len(first_half) > 600 else first_half
    emotion_guide = EMOTION_GUIDE.get(emotion_tag, EMOTION_GUIDE["铺垫"])
    word_target = cfg("novel", "chapter_word_target", 3500)
    half_min = word_min // 2
    half_max = word_max // 2
    hard_rules = _format_rule_block("硬约束（必须满足）", WRITER_HARD_CONSTRAINTS)
    forbidden_rules = _format_rule_block("禁止项（必须避免）", WRITER_FORBIDDEN_RULES)

    return f"""这章的任务：{plot_goal}
这章的感觉：{emotion_tag}——{emotion_guide}

本章节拍计划（后半段以收束本章节点为主）：
{beat_plan or "未提供"}

{hard_rules}

{forbidden_rules}

【前半段结尾（从这里接着写，不要重复这段内容）】
...{last_part}

接着写后半段，{half_min}-{half_max}字，把这章写完。

【结尾铁律（必须遵守）】
1. 章节结尾禁止出现模板化总结句（"才刚刚开始"、"无论前方"、"她知道这条路"类）
2. 禁止在结尾出现直接心理陈述（"她决定了"、"她知道该怎么做了"），改用具体行为展示
3. 禁止复用上一章结尾的句子、核心意象或情绪表达
4. 结尾应通过具体行动、新信息揭示或设置一个未答的问题形成阅读驱动力
5. 最后一句话要有画面感或声音感——读者看完这句，眼前要有一个具体的画面

{NEGATIVE_EXAMPLES}"""

def clean_content(text: str) -> str:
    text = re.sub(r'^\s*【[^】]*】.*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*-{3,}\s*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'\*([^*\n]+)\*', r'\1', text)
    text = re.sub(r'\n{3,}', '\n\n', text)

    template_patterns = [
        r'他[知感明白]道[，,][^。]{2,60}[。]?',
        r'她[知感明白]道[，,][^。]{2,60}[。]?',
        r'才刚开始',
        r'这一切才刚开始',
        r'这[一那]切.{0,3}开始[，。 ]?',
        r'前[方之]的?路[还]?[很]?长[，。]',
        r'无论前方有多少困难[，。]',
        r'他[们]*必须勇敢地走下去[，。]',
        r'她决定了[，。]',
        r'她知道该怎么做了[，。]',
        r'她感到很紧张',
        r'他感到很紧张',
        r'她心中涌起',
        r'他心中涌起',
        r'她知道[，,][^。]{2,50}[。]',
    ]
    for pattern in template_patterns:
        filtered = re.sub(pattern, '', text, flags=re.MULTILINE)
        if filtered != text:
            text = filtered

    return text.strip()


# ==================== 主写作函数 ====================

def write_chapter(novel_name: str, chapter_num: int,
                  plot_goal: str, emotion_tag: str = "铺垫") -> str:
    mm = MemoryManager(novel_name)
    ctx = mm.load_context(chapter_num)

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
- 用行动和感知来传达情绪，不用情绪标签直接陈述
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

    # 节拍规划
    print(f"  正在规划第{chapter_num}章节拍...")
    beat_plan = _plan_chapter_beats(ctx, chapter_num, plot_goal, emotion_tag)
    if beat_plan:
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
            word_min=word_min, word_max=word_max
        )

        full_content = call_author_api(
            system_prompt=system_prompt,
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
            word_min=word_min, word_max=word_max
        )
        first_half = call_author_api(
            system_prompt=system_prompt,
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
            word_min=word_min, word_max=word_max
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
                f"请在结尾处自然延伸，补充约{shortage}字的正文内容。"
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
    full_content = _self_check_and_revise(
        system_prompt=system_prompt,
        chapter_num=chapter_num,
        plot_goal=plot_goal,
        emotion_tag=emotion_tag,
        full_content=full_content,
        beat_plan=beat_plan,
        max_tokens=max_tokens_cfg,
    )
    full_content = re.sub(r'\n{3,}', '\n\n', full_content)

    total = len(full_content)

    hard_limit = int(word_max * 1.2)
    if total > hard_limit:
        original_total = total
        paragraphs = full_content.split('\n\n')
        truncated = []
        current_len = 0
        for para in paragraphs:
            if current_len + len(para) > hard_limit:
                break
            truncated.append(para)
            current_len += len(para)
        if truncated:
            full_content = '\n\n'.join(truncated)
            total = len(full_content)
            print(f"  [裁剪] 字数{original_total}超过硬上限{hard_limit}，已裁剪至{total}字")

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
    choice = input("请输入编号（默认1）：").strip() or "1"
    if choice not in AUTHOR_STYLES:
        choice = "1"
    selected = AUTHOR_STYLES[choice]
    print(f"\n[OK] 已选择风格：{selected['name']}\n")
    return choice
