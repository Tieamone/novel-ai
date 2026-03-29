import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import re
from pathlib import Path
from core.api_client import call_api
from core.memory_manager import MemoryManager
from core.config_loader import get as cfg

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


def build_writer_prompt(ctx: dict, chapter_num: int,
                        plot_goal: str, emotion_tag: str,
                        author_style: dict) -> str:
    world = ctx.get("world_settings", "")[:400]
    chars = ctx.get("characters", [])
    char_lines = [
        f"{c['name']}（{c['role']}）：{c['personality']}，"
        f"目前在{c['current_location']}，{c['current_status']}"
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

开始写吧。第一行是章节标题（第{chapter_num}章 加上你想的标题），然后直接进入正文。
写到一个自然的停顿点就停，后半段另外写。"""


def build_continue_prompt(chapter_num: int, plot_goal: str,
                          emotion_tag: str, first_half: str) -> str:
    last_part = first_half[-500:] if len(first_half) > 500 else first_half
    emotion_guide = EMOTION_GUIDE.get(emotion_tag, EMOTION_GUIDE["铺垫"])
    word_target = cfg("novel", "chapter_word_target", 3000)
    half_target = word_target // 2

    return f"""这章要做的事：{plot_goal}
这章的感觉：{emotion_tag} —— {emotion_guide}

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
    style_path = Path("data") / novel_name / "style.txt"
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

    # 前半段
    print(f"  正在生成第{chapter_num}章（前半段·{emotion_tag}）...")
    prompt = build_writer_prompt(
        ctx, chapter_num, plot_goal, emotion_tag, author_style
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
        chapter_num, plot_goal, emotion_tag, first_half
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

    # 字数硬约束：不足时补写
    min_words = int(word_target * 0.8)
    if len(full_content) < min_words:
        shortage = min_words - len(full_content)
        print(f"  [补写] 字数不足（{len(full_content)}/{min_words}），"
              f"补充约{shortage}字...")
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
        full_content = full_content + "\n\n" + supplement
        full_content = re.sub(r'\n{3,}', '\n\n', full_content)
        print(f"  补写完成，当前总字数：{len(full_content)}字")

    total = len(full_content)
    print(f"  [OK] 第{chapter_num}章完成，总字数：{total}字")

    # 保存时写入 plot_goal 和 emotion_tag
    mm.save_chapter(
        chapter_num, f"第{chapter_num}章",
        full_content, "draft",
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