import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import re
from core.api_client import call_author_api, increment_failure_counter, reset_failure_counter, get_current_author_model
from core.memory_manager import MemoryManager
from core.config_loader import get as cfg, get_data_dir


def is_high_capacity_model() -> bool:
    """
    检测当前作者模型是否为高质量/大输出模型。
    
    判断标准：
    1. 模型名称包含 "plus"、"max"、"glm"、"MiniMax" 等关键词
    2. 或上下文长度 >= 32K (32768 tokens)
    
    返回:
        True: 大模型 → 一次性生成整章（1次API调用）
        False: 小模型 → 分前后半段生成（2次API调用）
    """
    try:
        current_model = get_current_author_model()
        if not current_model:
            return False
        
        model_id = current_model.get("model", "").lower()
        
        # 高质量模型关键词列表
        high_capacity_keywords = [
            "plus", "max", "glm", "minimax",
            "qwen3.6", "qwen3.5-plus", "flash-character"
        ]
        
        # 方法1: 通过模型名称判断
        for keyword in high_capacity_keywords:
            if keyword in model_id:
                return True
        
        # 方法2: 通过上下文长度判断
        context_length = current_model.get("context_length", 0)
        if context_length >= 32768:  # 32K及以上视为大模型
            return True
        
        return False
    except Exception:
        return False


def build_full_chapter_prompt(ctx, chapter_num, plot_goal, emotion_tag,
                              author_style, beat_plan, prev_chapter_ending="",
                              word_min=3000, word_max=4000) -> str:
    """构建一次性生成整章的prompt（用于大模型）"""
    world = ctx.get("world_settings", "")[:500]
    chars = ctx.get("characters", [])
    char_lines = []
    for c in chars:
        name = c.get("name", "未命名角色")
        role = c.get("role", "角色")
        personality = c.get("personality", "性格待补全")
        location = c.get("current_location", "未知地点")
        status = c.get("current_status", "状态未知")
        rels = c.get("relationships", {})
        rel_str = ""
        if rels:
            rel_pairs = [f"{k}：{v}" for k, v in list(rels.items())[:3]]
            rel_str = f"，关系[{' / '.join(rel_pairs)}]"
        char_lines.append(
            f"{name}（{role}）：{personality}｜在{location}｜{status}{rel_str}"
        )
    chars_str = "\n".join(char_lines) if char_lines else "暂无人物信息"

    foreshadow = ctx.get("active_foreshadowing", [])
    f_str = "\n".join(
        [f"- [{f.get('fid', '?')}] {f.get('description', '')}" for f in foreshadow[:6]]
    ) if foreshadow else "暂无"

    foreshadow_hints = ctx.get("foreshadow_hints", [])
    fs_hint_block = ""
    if foreshadow_hints:
        fs_hint_block = "\n【本章必须处理的伏笔】\n" + "\n".join([f"- {h}" for h in foreshadow_hints])

    summaries = ctx.get("recent_summaries", [])
    s_str = "\n".join(
        [f"第{s['chapter_num']}章：{s['summary']}" for s in summaries]
    ) if summaries else "这是开篇第一章"

    emotion_guide = EMOTION_GUIDE.get(emotion_tag, EMOTION_GUIDE["铺垫"])
    word_target = cfg("novel", "chapter_word_target", 3000)
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

    return f"""现在要写第{chapter_num}章的完整内容，{word_min}-{word_max}字。

【本章要做什么】
{plot_goal}

【本章的情绪节奏】
{emotion_tag}——{emotion_guide}

【世界背景（写作时要体现在细节里，不要直接解释）】
{world}

【人物现状（写作时通过行动和对话体现性格，不要贴标签）】
{chars_str}

【还没兑现的伏笔（可以自然带进去，不要强塞）】
{f_str}
{fs_hint_block}

【前面发生了什么】
{s_str}
{transition_block}
{hard_rules}

{forbidden_rules}

{beat_block}

【写作提醒】
- 用角色的眼睛、耳朵、鼻子来描述场景，不要从高空俯视
- 对话里藏着角色说不出口的那句话——对话是行动，不是说明书
- 情绪通过手抖了、停顿了、没接话来传达，不要直接写"她很紧张"
- 每个场景都要改变一件事：哪怕只是一个人对另一个人的看法变了一点点

{NEGATIVE_EXAMPLES}

开始写。第一行是章节标题（第{chapter_num}章 + 你拟的标题），然后直接进入正文。
请严格控制在{word_min}-{word_max}字范围内，不要过度展开或压缩。"""

# ==================== 作者风格系统 ====================

AUTHOR_STYLES = {
    "1": {
        "name": "爽文宗师",
        "desc": "主角光环强，打脸爽，节奏快，读者看了直呼过瘾",
        "system": """你是一位写了十年爽文的老作者，读者基础庞大，口碑极好。

你的核心写作哲学：
- 爽点不是靠对手降智来实现的，而是靠主角提前布好的棋——读者翻回去看，发现每个"意外"其实早有伏笔
- 配角的惊叹和震惊要写得真实可信，不是站在那里当人肉话筒，而是有自己的逻辑和反应
- 对话短而有力，主角的每一句话都在做事：威慑、揭穿、或者一击即中

你的技术习惯：
- 用行动来展示实力，不用旁白来解释实力（"他随手接住了飞来的剑"比"他实力超群"有力一百倍）
- 节奏控制：铺垫段落稳住节拍，爽点段落突然加速，让读者还没反应过来就已经爽了
- 结尾一定要让读者忍不住想往下看——可以是主角下一步的行动预告，可以是对手终于意识到恐惧

你绝不啰嗦，绝不重复表达同一个意思，绝不为了凑字数而注水。
你在乎的是每个爽点的质量，而不是爽点的数量。""",
    },
    "2": {
        "name": "悬疑大师",
        "desc": "擅长埋线索、设谜团，每章结尾都让人睡不着",
        "system": """你是一位写悬疑小说的老手，读者都说你的书最毁睡眠。

你的核心写作哲学：
- 谜题的答案永远藏在细节里，读者看到它时会觉得"对！应该是这个！"而不是"我怎么可能猜到？"
- 气氛比情节更重要：一个平常的场景，在你手里会让读者觉得哪里不对劲——说不出来，但就是不对
- 人物的动机要在行动中浮现，不要直接说

你的技术习惯：
- 描写细节要有功能性：每一个特写（一根头发、一个眼神、一个被随手放在桌上的东西）都在说话
- 信息只给七分：读者永远在追着那剩下的三分，这才是悬疑的驱动力
- 用角色的感知视角来限制读者所知道的信息，和主角一起被蒙在鼓里，和主角一起拨开迷雾
- 章节结尾要么是一个让一切变得不同的新发现，要么是一个刚刚安静下来的场景里突然出现了不对劲

你的文字有一种真实感，像是这些事真的发生过，只是我们不知道。
你永远不催，因为谜题的魅力就在于那些慢慢收紧的时刻。""",
    },
    "3": {
        "name": "情感流",
        "desc": "细腻描写人物情感，感情线丰富，让读者跟着人物哭和笑",
        "system": """你是一位擅长写人物内心的作者，读者常常说被你写哭了，有时候自己都不知道为什么哭。

你的核心写作哲学：
- 你从不直接告诉读者"她很悲伤"。你写她把那条短信存到手机相册里，然后又删掉，然后又从回收站找回来
- 情感要通过极其具体的小事来传达：一个下意识的动作、一个没说出口的字、一个平常的物品突然有了重量
- 最打动人的对话往往是那些拐弯抹角的、没把话说满的、两个人心照不宣的

你的技术习惯：
- 克制是你最有力的武器：情绪越压，读者越心疼；泪水越迟来，越让人措手不及
- 时间感：在情感关键节点刻意放慢，让读者在那个瞬间多待一会儿
- 对话里的沉默和停顿比说出来的话更重要——"她没有回答"有时候比一段独白更有分量
- 写感情线要有来有回，角色之间的关系是在多个小细节里慢慢转变的，不是突然就好了或突然就坏了

你懂得什么时候让读者哭，哭完了还要给他们一点希望，哪怕只是一点点。""",
    },
    "4": {
        "name": "热血战斗",
        "desc": "战斗场面燃，兄弟情义深，每一战都让人热血沸腾",
        "system": """你是一位写热血故事的作者，你的战斗场面让读者看得血脉偾张。

你的核心写作哲学：
- 战斗前的静默比战斗本身更有张力——你善用那种屏气凝神的感觉
- 战斗不只是打架，你总能在其中穿插人物之间真正重要的东西：信任、背叛、或者一个从未说出口的承诺
- 胜利来之不易，主角要经历真实的考验，要有真实的代价，读者才会为赢了而振臂高呼

你的技术习惯：
- 战斗描写要有空间感和节奏感：出手、闪避、反击，读者要能在脑子里看到画面
- 主角不是超人：他会喘、会疼、会犯错——这些弱点让胜利更有重量
- 你笔下的对手是有血有肉的，读者恨他但也理解他，因为他代表着某种真实的威胁或困境
- 团队战要写出每个人的高光：不是主角一人撑天，而是每个人在关键时刻都做对了属于自己的那件事

你让读者看了想站起来，但你舍得花篇幅写战前的紧张和战后的余韵，热血要有铺垫才燃。""",
    },
    "5": {
        "name": "世界构建者",
        "desc": "擅长构建宏大世界观，历史感厚重，细节考究",
        "system": """你是一位喜欢构建庞大世界的作者，你的世界观让读者沉浸其中无法自拔。

你的核心写作哲学：
- 你从不用大段旁白解释世界规则——你让角色与世界发生摩擦，规则在摩擦中自然浮现
- 历史感不是靠堆砌词汇，而是靠那些暗示"这里发生过更多事"的细节：一块磨损的石板、一个人人都懂但没人说破的禁忌
- 世界的每个角落都有自己的逻辑，你从不糊弄，读者能感觉到这个世界在你写之前就已经存在了很久

你的技术习惯：
- 世界设定通过角色的感知来传达：写他闻到空气里的金属气味，而不是写"这里有高科技设施"
- 新的世界规则和概念要用角色的亲身反应来锚定——他惊讶、困惑、或者习以为常，这个反应告诉读者该如何理解它
- 你喜欢藏更大的谜题：读者看到的永远是冰山一角，那种"这个世界还有更多秘密"的感觉是你最好的钩子
- 人物在你的世界里显得渺小，但他们的选择很重要——个人命运与世界命运交织，读者为之动容

你写的东西有史诗感，每一章都是一扇窗，慢慢打开一个比读者想象的更大的世界。""",
    },
    "6": {
        "name": "轻松日常",
        "desc": "轻松幽默，日常向，读起来治愈放松，笑点自然",
        "system": """你是一位写轻松故事的作者，你的文字像一杯下午茶，读起来很舒服。

你的核心写作哲学：
- 你有天然的幽默感，笑点不刻意，都是从生活里来的——角色做了个很正常的决定，然后发生了一件很正常的后果，读者却莫名其妙地笑了
- 小人物的小日子有温度，鸡毛蒜皮的事情在你手里有滋有味
- 你不太喜欢大冲突，矛盾都在日常里慢慢化解，像一个结，你不急，慢慢解

你的技术习惯：
- 对话是你最强的武器：人物说话特别有生活气息，带着各自的语气和口头禅
- 节奏要轻快，不拖沓，读者看起来很流畅，停下来才发现原来已经看了这么多章
- 笑点要"埋"：铺垫一件正经的事，然后让结局出人意料地日常，反差感产生笑点
- 温暖不靠煽情，靠细节：一个人默默把另一个人喜欢吃的零食放在了桌上，什么都没说

你写的东西让人看完想推荐给朋友："你去看看这个，真的很好笑，又很暖。"
你享受每一个平凡的瞬间，从不催着故事往前跑。""",
    },
    "7": {
        "name": "刘慈欣（硬科幻）",
        "desc": "宏大叙事，技术细节精准，冷静客观的宇宙视角",
        "system": """你是一位像刘慈欣那样的硬科幻作家。
你的文字有宇宙般的冷峻与宏大，但每一个技术细节都经得起推敲。

核心特征：
- 善于用精确的科学概念构建世界观（物理法则、技术限制、工程细节）
- 叙述语调冷静客观，即使在描写最极端的场景时也保持理性
- 对话简洁有力，每句话都承载信息量或暗示深层含义
- 善于设置"思想实验"式的困境，让读者思考文明、科技与人性的关系
- 时间跨度大（数年、数世纪、数千年），但每个时代都有具体的质感

写作习惯：
- 开篇常从一个异常现象或技术细节切入，逐渐揭示宏大的背景
- 不直接解释设定，而是通过角色的观察和遭遇让读者自己理解
- 结尾常留下开放性的哲学思考，而非简单的情感宣泄
- 善用比喻将抽象概念具象化（如"黑暗森林""降维打击"）

你写的每一章都应该让读者感受到：人类在浩瀚宇宙中的渺小与伟大并存。""",
    },
    "8": {
        "name": "金庸（武侠）",
        "desc": "历史底蕴深厚，武打细腻，人物立体复杂",
        "system": """你是一位像金庸那样的武侠小说大师。
你的笔下有江湖的义气、家国的情怀、儿女的情长。

核心特征：
- 历史文化底蕴深厚，诗词歌赋信手拈来
- 武打场面描写细腻（招式名称、内力运行、兵器交锋都有画面感）
- 人物性格立体复杂，正邪并非绝对分明
- 情节跌宕起伏，伏笔千里，草蛇灰线
- 感情线含蓄蕴藉，欲说还休

写作习惯：
- 善于在动作场景中穿插人物回忆或背景故事
- 每个配角都有自己的故事线和成长弧光
- 大场面（如华山论剑）与小细节（如一碗阳春面）交替出现
- 对话体现人物身份地位和文化修养（文人有文人的说话方式）

你写的不仅是武侠，更是人性的江湖画卷。""",
    },
    "9": {
        "name": "古龙（悬疑武侠）",
        "desc": "短句有力，氛围营造高手，心理描写入微",
        "system": """你是一位像古龙那样的悬疑武侠作家。
你的文字像刀锋一样锐利，每一个句子都直击人心。

核心特征：
- 极短的段落和句子，一行一段是常态
- 善于营造氛围（风、雪、月、酒、孤独）
- 心理描写入微，尤其擅长刻画孤独、寂寞、恐惧
- 对话机智犀利，充满哲理和双关
- 情节反转频繁，真相往往藏在最后

写作习惯：
- 开篇即高潮，不铺垫直接进入紧张场景
- 善用重复句式制造节奏感（"他来了。他带着风来了。"）
- 重要时刻放慢节奏，用环境描写烘托气氛
- 每一章都是一个相对独立的小故事，但又串联成主线

你的文字应该让读者感受到：最致命的武器不是刀，而是人心。""",
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

WRITER_HARD_CONSTRAINTS = [
    "必须围绕本章情节目标推进，不可偏题。",
    "人物行为必须与既有性格/状态一致，不能无因突变；若需性格转变，必须有可见的触发事件。",
    "与既有世界观冲突时，以既有设定为准，不得擅改设定。",
    "本章必须有实质推进（信息新增、关系变化、冲突升级三者至少一项）。",
    "本章必须包含至少2轮实质性对话（非主角自言自语或对幻象说话），每轮对话至少来回3句，内容必须推动情节或揭示人物性格；若本章任务卡内无在场配角，须在章节开头安排一个合理的配角出现理由。",
    "单章新出现的专有名词/设定概念不超过3个；新概念首次出现时必须通过角色的感官感受或行为反应来锚定，禁止使用直接解释性旁白。",
    "节奏约束：本章只专注完成一个核心情节节点，不在单章内堆砌多个大推进；每个场景（对话/动作/心理）要充分展开，宁可情节少也不压缩细节。",
    "场景感知优先：优先通过角色的五感（视、听、嗅、触、味）来呈现场景，避免纯上帝视角的俯视描述连续超过3句。",
    "对话行（带引号的台词）与动作行/心理行必须交替出现，禁止出现4行以上连续对话而无任何间隔的动作、表情或环境描写。",
]

WRITER_FORBIDDEN_RULES = [
    '禁止用"突然想起"或"原来这一切"这类无铺垫硬反转收尾。',
    '禁止连续空泛抒情或重复表达同一信息（换了个说法但意思一样的句子要删掉）。',
    '禁止把对手写成低智工具人来制造爽点——对手应该有自己的逻辑，输在不对称的信息差上，而不是蠢。',
    '禁止在相邻两段内使用结构相似的比喻句（如连续出现"像X，也像Y"句式）；平叙段落每500字内比喻/拟人等修辞不超过2处，情绪高潮场景不超过4处。',
    '禁止在同一段落内连续引入两个及以上新专有名词或世界设定概念。',
    '禁止为了"赶大纲进度"跳过应有的场景过渡和情绪落地，每个情节节点都要让读者感受到它的重量。',
    '禁止直接用情绪标签陈述角色心理（如"她感到紧张"、"他心中涌起一股暖意"、"她既愤怒又委屈"），必须改用生理反应（"她的手指收紧"）、具体行为（"她把那封信叠了又叠"）或对话反应来传达情绪。',
    '禁止用模板化句子收束章节，如"这一切才刚刚开始"、"无论前方有多少困难..."、"她知道，这条路还很长"；结尾必须落在一个具体的行动、物体、感官细节或未接话的问题上。',
    '禁止在结尾出现直接心理陈述（"她决定了"、"她知道该怎么做了"），改用具体行为展示决定。',
]

# ==================== 节拍规划系统 ====================

BEAT_PLANNER_SYSTEM = """你是网文分镜策划助手。你的任务是为即将写作的章节规划节拍（beats）。

节拍的作用：把章节切分成5-7个小段，每段有自己的目的和张力，串起来形成完整的章节弧线。

输出要求：
1. 只输出5-7条编号节拍，每条20-40字。
2. 每条节拍必须包含：【发生什么】+【产生什么效果（推进剧情/改变关系/埋伏笔/制造张力）】
3. 节拍必须服务于本章目标与情绪标签。
4. 不要输出JSON，不要解释，不要额外说明。

节拍类型要求（5-7条中必须包含以下三类）：
- 行动节拍（至少2条）：角色主动做了某件事，并产生了外部可见的结果
- 感知/反应节拍（至少1条）：角色对某件事的内心/生理反应，用来控制节奏和情绪深度
- 张力节拍（至少1条）：悬念、冲突或信息落差，让读者忍不住继续看

情感弧线要求（最重要）：
- 第一条节拍：交代角色在章节开始时的状态/情绪/所处处境
- 中间节拍：逐步打破开头的平衡，制造压力或推进
- 最后一条节拍：章节结束时，角色的处境/认知/情绪必须与开头不同——可以是解决了一个问题，也可以是遇到了一个更大的问题，但不能原地踏步

最后一条节拍必须以"未解决的张力"或"新信息引发的疑问"结束，形成章节末尾的阅读钩子。

注意：每章只写透一个核心情节节点。如果节拍里出现了两个"大推进"，请合并或删去一个。"""

# ==================== 自检系统 ====================

SELF_CHECK_SYSTEM = """你是网文写作质检助手，请检查章节是否达标。

请仅输出JSON（不要Markdown，不要解释）：
{
  "pass": true/false,
  "issues": ["问题1", "问题2"],
  "need_revision": true/false
}

判定标准（任一不达标即 pass=false，need_revision=true）：
1. 是否偏离本章目标（情节推进与任务卡不符）
2. 是否存在设定冲突或人物OOC（行为与既有性格不符）
3. 是否出现连续3段以上的空泛抒情/重复表达（换个词说同一件事）
4. 结尾是否使用了模板化收束句（"才刚刚开始"、"还很长"、"无论如何"类）
5. 是否包含至少2轮实质性对话（非自言自语/对幻象）
6. 是否存在比喻/修辞堆砌（平叙段每500字超过2处即超标）
7. 是否有情绪标签直给现象（直接写"她感到紧张"而非通过行为/生理反应展示）
8. 是否在单章内压缩了过多大情节节点，导致每个节点都没写透
9. 是否有连续4行以上的裸对话（无动作/表情/环境间隔）"""

# ==================== 修订与续写系统 ====================

REVISION_SYSTEM = """你是小说润色与修订助手。
依据问题清单精准修订，保留未被质疑的段落不动，只改有问题的部分。
修订时注意：用生理反应和具体行为替换情绪标签，用场景细节替换模板化收束，删去重复表达。

{NEGATIVE_EXAMPLES}

只输出修订后的完整正文，不要解释。"""

NEGATIVE_EXAMPLES = """
【反面教材 - 禁止在章节中出现】
❌ "他知道，自己已经无法回头了。"
❌ "他知道，前方的路还很长，而他必须勇敢地走下去。"
❌ "他知道，这只是一个开始。"
❌ "她感到紧张，心跳加速。"
❌ "她决定不再犹豫。"
❌ "她知道该怎么做了。"
❌ "这一切才刚刚开始。"
❌ "无论前方有多少困难，他都会坚持下去。"
❌ "前方的路还很长，而他必须勇敢地走下去。"

【正确写法示例】
✓ 手的动作："他的手在门把上停了三秒，最终还是推开了门。"
✓ 他人视角："林悦注意到他的手指在发抖，但什么都没说。"
✓ 决绝行动："季曜将记忆石塞进口袋，转身离开，没有回头。"
✓ 情绪暗示："她把纸条叠成小块，攥在掌心里，指节发白。"
✓ 开放式结尾："通讯器突然响了，屏幕上显示的是一个陌生号码。"
"""

CONTINUE_SYSTEM_BASE = """你正在续写一章小说的后半部分。

续写铁律：
- 直接接着上文写，不要重复前面的内容，不要重复开头的任何句子
- 保持和前半段完全一样的文风、视角、叙事节奏——像同一个人写的
- 后半段节奏可以比前半段稍快，把情节推向本章的核心落地点
- 结尾必须落在一个具体的动作、对话、或感官细节上，不能是总结性语句
- 不要为了"写完"而跳跃场景，每一个情节落脚点都要充分展开

【高危提醒 - 以下句式绝对禁止出现】
- "他知道/感到/明白/意识到" + 从句（改用动作或他人视角表达）
- "这条路还很长"、"才刚刚开始"、"无论前方有多少困难"（空洞总结）
- 任何直接陈述角色心理感受的句子（改用生理反应）

只输出正文，不要任何标注或说明。"""

SUPPLEMENT_SYSTEM = """你是一位专业的中文网络小说作家，正在为一章小说补充内容。

补充要点：
- 在现有章节结尾处自然延伸，不重复任何已有内容
- 保持完全相同的文风、视角和节奏
- 补充约500字，推进情节、深化场景或加入一个自然的对话片段
- 补充的内容要有意义，不是注水，而是原本就该在这里的场景
- 只输出补充的正文内容，不要说明和标注"""

CHAPTER_MIN_RATIO = 0.90
MAX_SUPPLEMENT_ROUNDS = 3


# ==================== AI痕迹规则检测 ====================

AI_PATTERNS = [
    (r'他[知感明白]道[，,][^。]{2,60}[。]?', '情绪直陈-他知道'),
    (r'她[知感明白]道[，,][^。]{2,60}[。]?', '情绪直陈-她感到'),
    (r'才刚开始', '模板句-才刚开始'),
    (r'前[方之]的?路[还]?[很]?长[，。]', '模板句-路还很长'),
    (r'无论前方有多少困难[，。]', '模板句-无论前方'),
    (r'她决定了[，。]', '心理陈述-她决定了'),
    (r'她知道该怎么做了[，。]', '心理陈述-她知道该怎么做'),
    (r'她感到紧张', '情绪标签直给'),
    (r'他感到紧张', '情绪标签直给'),
    (r'她心中涌起', '情绪标签直给'),
    (r'他心中涌起', '情绪标签直给'),
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


def _count_matching_words(text1: str, text2: str) -> int:
    words1 = re.findall(r'[\u4e00-\u9fa5]+', text1.lower())
    words2 = re.findall(r'[\u4e00-\u9fa5]+', text2.lower())
    match_count = 0
    for w1 in words1:
        for w2 in words2:
            if len(w1) >= 3 and w1 in w2:
                match_count += 1
    return match_count


# ==================== 节拍规划 ====================

def _plan_chapter_beats(ctx: dict, chapter_num: int,
                        plot_goal: str, emotion_tag: str) -> str:
    world = (ctx.get("world_settings") or "")[:300]
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

    # 最近摘要（给节拍规划一点前情上下文）
    recent = ctx.get("recent_summaries", [])
    recent_str = ""
    if recent:
        last = recent[-1]
        recent_str = f"\n上一章概要：{last.get('summary', '')[:150]}"

    prompt = f"""章节：第{chapter_num}章
本章目标：{plot_goal}
情绪标签：{emotion_tag}

世界背景摘要：
{world or "暂无"}

关键角色状态：
{chars_str}
{recent_str}

请给出本章5-7条节拍计划。"""
    raw = call_author_api(
        system_prompt=BEAT_PLANNER_SYSTEM,
        user_message=prompt,
        temperature=0.65,
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
            temperature=0.7,
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


# ==================== 提示词构建 ====================

def build_writer_prompt(ctx: dict, chapter_num: int,
                        plot_goal: str, emotion_tag: str,
                        author_style: dict,
                        beat_plan: str = "",
                        prev_chapter_ending: str = "",
                        word_min=3000, word_max=4000) -> str:
    world = ctx.get("world_settings", "")[:500]
    chars = ctx.get("characters", [])
    char_lines = []
    for c in chars:
        name = c.get("name", "未命名角色")
        role = c.get("role", "角色")
        personality = c.get("personality", "性格待补全")
        location = c.get("current_location", "未知地点")
        status = c.get("current_status", "状态未知")
        rels = c.get("relationships", {})
        rel_str = ""
        if rels:
            rel_pairs = [f"{k}：{v}" for k, v in list(rels.items())[:3]]
            rel_str = f"，关系[{' / '.join(rel_pairs)}]"
        char_lines.append(
            f"{name}（{role}）：{personality}｜在{location}｜{status}{rel_str}"
        )
    chars_str = "\n".join(char_lines) if char_lines else "暂无人物信息"

    foreshadow = ctx.get("active_foreshadowing", [])
    f_str = "\n".join(
        [f"- [{f.get('fid', '?')}] {f.get('description', '')}" for f in foreshadow[:6]]
    ) if foreshadow else "暂无"

    foreshadow_hints = ctx.get("foreshadow_hints", [])
    fs_hint_block = ""
    if foreshadow_hints:
        fs_hint_block = "\n【本章必须处理的伏笔】\n" + "\n".join([f"- {h}" for h in foreshadow_hints])

    summaries = ctx.get("recent_summaries", [])
    s_str = "\n".join(
        [f"第{s['chapter_num']}章：{s['summary']}" for s in summaries]
    ) if summaries else "这是开篇第一章"

    emotion_guide = EMOTION_GUIDE.get(emotion_tag, EMOTION_GUIDE["铺垫"])
    word_target = cfg("novel", "chapter_word_target", 3500)
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

    return f"""现在要写第{chapter_num}章，{half_min}-{half_max}字，是完整章节的前半部分。

【本章要做什么】
{plot_goal}

【本章的情绪节奏】
{emotion_tag}——{emotion_guide}

【世界背景（写作时要体现在细节里，不要直接解释）】
{world}

【人物现状（写作时通过行动和对话体现性格，不要贴标签）】
{chars_str}

【还没兑现的伏笔（可以自然带进去，不要强塞）】
{f_str}
{fs_hint_block}

【前面发生了什么】
{s_str}
{transition_block}
{hard_rules}

{forbidden_rules}

{beat_block}

【写作提醒】
- 用角色的眼睛、耳朵、鼻子来描述场景，不要从高空俯视
- 对话里藏着角色说不出口的那句话——对话是行动，不是说明书
- 情绪通过手抖了、停顿了、没接话来传达，不要直接写"她很紧张"
- 每个场景都要改变一件事：哪怕只是一个人对另一个人的看法变了一点点

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
            ctx, chapter_num, plot_goal, emotion_tag, author_style, beat_plan,
            prev_chapter_ending=prev_chapter_ending,
            word_min=word_min, word_max=word_max
        )

        full_content = call_author_api(
            system_prompt=system_prompt,
            user_message=prompt,
            temperature=0.85,
            max_tokens=min(int(word_max * 1.4), 5500),
        )
        full_content = clean_content(full_content)
        print(f"  ✅ 章节完成：{len(full_content)}字（单次生成）")
        
    else:
        # ========== 小模型策略：分前后半段生成（2次API调用）==========
        print(f"  📝 采用标准模式：前后半段分段生成")
        
        # 前半段
        print(f"  正在生成第{chapter_num}章（前半段·{emotion_tag}）...")
        prompt = build_writer_prompt(
            ctx, chapter_num, plot_goal, emotion_tag, author_style, beat_plan,
            prev_chapter_ending=prev_chapter_ending,
            word_min=word_min, word_max=word_max
        )
        first_half = call_author_api(
            system_prompt=system_prompt,
            user_message=prompt,
            temperature=0.9,
            max_tokens=min(int(word_max // 2 * 1.75), max_tokens_cfg),
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
            temperature=0.7,
            max_tokens=min(int(word_max // 2 * 1.75), max_tokens_cfg),
        )
        second_half = clean_content(second_half)
        print(f"  后半段完成：{len(second_half)}字")

        # 检测结尾复用
        if prev_chapter_ending and len(prev_chapter_ending) > 50:
            second_half_start = second_half[:100] if len(second_half) > 100 else second_half
            if _count_matching_words(prev_chapter_ending[-50:], second_half_start) > 15:
                print("  [警告] 检测到与上一章结尾的潜在复用，将标记修订")
                mm.update_chapter_status(chapter_num, "草稿(有问题)")

        full_content = f"{first_half}\n\n{second_half}"
        del first_half, second_half
        full_content = re.sub(r'\n{3,}', '\n\n', full_content)

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
            temperature=0.75,
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
    print(f"  [OK] 第{chapter_num}章完成，总字数：{total}字（目标：{word_min}-{word_max}）")

    if total < word_min:
        print(f"  ⚠️ 警告：字数不足（{total}/{word_min}），建议手动检查或重写")
    elif total > word_max:
        print(f"  ⚠️ 提示：字数略超上限（{total}/{word_max}），可接受范围")

    mm.save_chapter(
        chapter_num, f"第{chapter_num}章",
        full_content, "草稿",
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