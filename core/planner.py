import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import re
import json
from core.api_client import call_api
from core.memory_manager import MemoryManager
from core.config_loader import get as cfg

WORLD_PROMPT = """你是一位专业的中文网络小说策划师。
请根据用户提供的大纲和类型，生成一份详细的世界观设定。

要求：
1. 世界观必须与大纲内容高度契合，不能自己发挥偏离大纲
2. 必须包含：地理环境、力量体系（或核心设定）、社会结构、核心规则
3. 语言简洁，总字数500字以内
4. 直接输出设定内容，不要加任何前缀说明"""

CHARACTER_PROMPT = """你是一位专业的中文网络小说策划师。
请根据用户提供的大纲和角色名单，为每个角色生成详细档案。

要求：
1. 人物性格和背景必须与大纲情节逻辑一致
2. 每个角色必须包含：外貌、性格核心、隐藏秘密、致命弱点、初始位置、初始状态
3. 角色之间的关系要服务于大纲的核心冲突
4. 严格按照以下JSON格式输出，不要加任何其他内容：

[
  {
    "name": "角色名",
    "role": "主角/配角/反派",
    "appearance": "外貌描述",
    "personality": "性格描述",
    "secret": "隐藏秘密",
    "weakness": "致命弱点",
    "current_location": "初始位置",
    "current_status": "初始状态",
    "relationships": {"其他角色名": "关系描述"}
  }
]"""

OUTLINE_GEN_PROMPT = """你是一位专业的中文网络小说策划师。
请根据用户提供的小说基本信息，生成一份完整的总大纲。

要求：
1. 包含三幕结构：开局（1-30章）、发展（31-100章）、高潮结局（101章以后）
2. 每幕列出3-5个关键转折点
3. 指出主要伏笔的埋设和兑现位置
4. 结局要有反转，不能太俗套
5. 总字数600字以内，直接输出大纲内容"""

CHARACTER_EXTRACT_PROMPT = """你是一位专业的小说策划师。
请根据以下大纲，提取或推断出主要角色名单。

要求：
1. 列出大纲中明确提到的角色
2. 如有需要可补充1-2个大纲中暗示但未命名的关键配角
3. 每个角色一行，格式：角色名（角色定位）
4. 只输出角色列表，不要其他内容"""


def get_outline_choice(genre: str, keywords: str,
                       novel_name: str) -> str:
    """第一步：确定大纲——自己写或AI生成"""
    print("\n" + "=" * 50)
    print("  第一步：确定大纲")
    print("=" * 50)
    print("  1. 由AI根据类型和关键词自动生成")
    print("  2. 我自己提供大纲（粘贴文本）")
    print()
    choice = input("请选择（默认1）：").strip() or "1"

    if choice == "2":
        outline = _get_outline_from_user()
        if outline:
            return outline
        print("  [提示] 未收到大纲内容，改为AI生成")

    # AI生成大纲
    print("\n  正在生成总大纲...")
    outline = call_api(
        system_prompt=OUTLINE_GEN_PROMPT,
        user_message=(
            f"小说名：{novel_name}\n"
            f"类型：{genre}\n"
            f"关键词：{keywords}"
        ),
        temperature=0.9,
    )
    print("  [OK] 总大纲已生成")
    return outline


def _get_outline_from_user() -> str:
    """让用户在CMD里粘贴大纲"""
    print()
    print("  请将大纲内容粘贴到下方。")
    print("  粘贴完成后，在新的一行单独输入 END 并回车结束。")
    print("-" * 50)
    lines = []
    while True:
        try:
            line = input()
            if line.strip().upper() == "END":
                break
            lines.append(line)
        except EOFError:
            break
    outline = "\n".join(lines).strip()
    if outline:
        print(f"  [OK] 已接收大纲，共 {len(outline)} 字")
    return outline


def get_characters_choice(outline: str) -> list:
    """第二步：确定角色——AI从大纲提取或手动输入"""
    print("\n" + "=" * 50)
    print("  第二步：确定角色名单")
    print("=" * 50)
    print("  1. AI从大纲中自动提取角色")
    print("  2. 我自己输入角色名字")
    print()
    choice = input("请选择（默认1）：").strip() or "1"

    if choice == "2":
        print()
        names_input = input("  请输入角色名字，用逗号分隔：").strip()
        names = [
            n.strip() for n in names_input.replace(",", "，").split("，")
            if n.strip()
        ]
        if names:
            print(f"  [OK] 已输入 {len(names)} 个角色：{' / '.join(names)}")
            return names
        print("  [提示] 未输入角色，改为AI提取")

    # AI从大纲提取
    print("\n  正在从大纲提取角色...")
    raw = call_api(
        system_prompt=CHARACTER_EXTRACT_PROMPT,
        user_message=f"大纲内容：\n{outline}",
        temperature=0.5,
        max_tokens=300,
    )

    # 解析角色名（格式：角色名（定位））
    names = []
    for line in raw.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        # 提取括号前的名字
        match = re.match(r'^[\d\.\s]*([^\s（(]+)', line)
        if match:
            name = match.group(1).strip()
            if name and len(name) <= 10:
                names.append(name)

    if names:
        print(f"  [OK] 从大纲提取到 {len(names)} 个角色：{' / '.join(names)}")
        confirm = input("  确认使用以上角色？(回车确认 / 输入n重新手动输入)：").strip()
        if confirm.lower() == "n":
            names_input = input("  请输入角色名字，用逗号分隔：").strip()
            names = [
                n.strip() for n in names_input.replace(",", "，").split("，")
                if n.strip()
            ]
    else:
        print("  [警告] 未能提取角色，请手动输入")
        names_input = input("  请输入角色名字，用逗号分隔：").strip()
        names = [
            n.strip() for n in names_input.replace(",", "，").split("，")
            if n.strip()
        ]

    return names if names else ["主角"]


def get_style_choice() -> str:
    """第三步：选择写作风格（支持自定义）"""
    from core.writer import AUTHOR_STYLES, select_style_interactive

    print("\n" + "=" * 50)
    print("  第三步：选择写作风格")
    print("=" * 50)

    for key, style in AUTHOR_STYLES.items():
        print(f"  {key}. {style['name']:<12} {style['desc']}")
    print(f"  7. 自定义风格（自己描述写作风格）")
    print()

    choice = input("请输入编号（默认1）：").strip() or "1"

    if choice == "7":
        print()
        print("  请描述你想要的写作风格（例如：文风偏向古典，喜欢用短句，")
        print("  擅长写慢热感情，叙事节奏稳健不拖沓...）")
        custom_desc = input("  风格描述：").strip()
        if custom_desc:
            # 把自定义描述存为特殊key
            print(f"  [OK] 已设置自定义风格")
            return f"custom:{custom_desc}"
        print("  [提示] 未输入描述，使用默认风格")
        return "1"

    if choice not in AUTHOR_STYLES:
        choice = "1"

    selected = AUTHOR_STYLES[choice]
    print(f"\n  [OK] 已选择风格：{selected['name']}\n")
    return choice


def generate_world(novel_name: str, genre: str,
                   outline: str, mm: MemoryManager) -> str:
    """根据大纲生成世界观"""
    print("\n  正在根据大纲生成世界观...")
    world = call_api(
        system_prompt=WORLD_PROMPT,
        user_message=(
            f"小说类型：{genre}\n\n"
            f"大纲内容：\n{outline}"
        ),
        temperature=0.85,
    )
    mm.save_world_settings(world)
    print("  [OK] 世界观已生成并保存")
    return world


def generate_characters(character_names: list, outline: str,
                        world: str, mm: MemoryManager) -> list:
    """根据大纲和世界观生成人物档案"""
    print("\n  正在生成人物档案...")
    names_str = "、".join(character_names)
    raw = call_api(
        system_prompt=CHARACTER_PROMPT,
        user_message=(
            f"角色名单：{names_str}\n\n"
            f"大纲内容：\n{outline}\n\n"
            f"世界观：\n{world}"
        ),
        temperature=0.8,
    )

    match = re.search(r'\[.*\]', raw, re.DOTALL)
    if not match:
        raise ValueError(f"AI返回格式错误，无法解析人物JSON:\n{raw}")

    characters = json.loads(match.group())
    for char in characters:
        mm.save_character(char["name"], char)

    print(f"  [OK] {len(characters)} 个人物档案已生成并保存")
    return characters


def split_outline_to_tasks(outline: str, novel_name: str):
    """将大纲预拆分为章节任务卡，存入数据库"""
    from core.db import get_connection
    total = cfg("novel", "pre_split_chapters", 50)

    print(f"\n  正在将大纲拆分为前{total}章任务卡...")

    raw = call_api(
        system_prompt="你是小说策划师，将大纲拆解为章节任务。只输出JSON。",
        user_message=f"""根据以下小说大纲，生成前{total}章的章节任务卡。

大纲：
{outline}

严格按JSON格式输出，不要其他内容：
[
  {{
    "chapter_num": 1,
    "plot_goal": "本章情节目标（50字以内）",
    "emotion_tag": "铺垫"
  }}
]

情绪标签只能从以下选择：铺垫/冲突/爽点/低谷/反转""",
        temperature=0.7,
        max_tokens=4000,
    )

    match = re.search(r'\[.*\]', raw, re.DOTALL)
    if not match:
        print("  [警告] 任务卡解析失败，将使用实时分析模式")
        return 0

    try:
        tasks = json.loads(match.group())
    except Exception:
        print("  [警告] 任务卡JSON解析失败，将使用实时分析模式")
        return 0

    valid_tags = ["铺垫", "冲突", "爽点", "低谷", "反转"]
    conn = get_connection(novel_name)
    saved = 0
    for task in tasks:
        chapter_num = task.get("chapter_num")
        plot_goal = task.get("plot_goal", "").strip()
        emotion_tag = task.get("emotion_tag", "铺垫").strip()

        if emotion_tag not in valid_tags:
            emotion_tag = "铺垫"

        if chapter_num and plot_goal:
            conn.execute("""
                INSERT OR REPLACE INTO chapter_tasks
                (chapter_num, plot_goal, emotion_tag, status)
                VALUES (?, ?, ?, 'pending')
            """, (chapter_num, plot_goal, emotion_tag))
            saved += 1

    conn.commit()
    conn.close()
    print(f"  [OK] 已生成 {saved} 个章节任务卡")
    return saved


def extend_tasks(novel_name: str, from_chapter: int):
    """当任务卡不足时，继续拆分后续章节"""
    from core.db import get_connection
    outline_path = (
        __import__("pathlib").Path("data") / novel_name / "master_outline.md"
    )
    if not outline_path.exists():
        return

    outline = outline_path.read_text(encoding="utf-8")
    total = cfg("novel", "pre_split_chapters", 50)
    end_chapter = from_chapter + total - 1

    print(f"  [扩展] 正在生成第{from_chapter}-{end_chapter}章任务卡...")

    raw = call_api(
        system_prompt="你是小说策划师，将大纲拆解为章节任务。只输出JSON。",
        user_message=f"""根据以下大纲，生成第{from_chapter}章到第{end_chapter}章的任务卡。

大纲：
{outline}

JSON格式：
[
  {{
    "chapter_num": {from_chapter},
    "plot_goal": "情节目标（50字以内）",
    "emotion_tag": "铺垫"
  }}
]""",
        temperature=0.7,
        max_tokens=4000,
    )

    match = re.search(r'\[.*\]', raw, re.DOTALL)
    if not match:
        return

    try:
        tasks = json.loads(match.group())
    except Exception:
        return

    valid_tags = ["铺垫", "冲突", "爽点", "低谷", "反转"]
    conn = get_connection(novel_name)
    for task in tasks:
        chapter_num = task.get("chapter_num")
        plot_goal = task.get("plot_goal", "").strip()
        emotion_tag = task.get("emotion_tag", "铺垫").strip()
        if emotion_tag not in valid_tags:
            emotion_tag = "铺垫"
        if chapter_num and plot_goal:
            conn.execute("""
                INSERT OR IGNORE INTO chapter_tasks
                (chapter_num, plot_goal, emotion_tag, status)
                VALUES (?, ?, ?, 'pending')
            """, (chapter_num, plot_goal, emotion_tag))

    conn.commit()
    conn.close()
    print(f"  [OK] 任务卡已扩展至第{end_chapter}章")


def run_planner(novel_name: str, genre: str,
                keywords: str) -> tuple:
    """
    完整策划流程，返回 (character_names, style_key)
    流程：大纲 → 世界观 → 角色 → 风格
    """
    print(f"\n开始策划《{novel_name}》...")
    print("=" * 50)

    mm = MemoryManager(novel_name)

    # 第一步：大纲
    outline = get_outline_choice(genre, keywords, novel_name)

    # 保存大纲
    (mm.data_dir / "master_outline.md").write_text(
        f"# 总大纲\n\n{outline}", encoding="utf-8"
    )
    print("  [OK] 大纲已保存")

    # 第二步：根据大纲生成世界观
    world = generate_world(novel_name, genre, outline, mm)

    # 第三步：角色
    character_names = get_characters_choice(outline)
    characters = generate_characters(character_names, outline, world, mm)

    # 第四步：写作风格
    style_key = get_style_choice()

    # 第五步：预拆分任务卡
    split_outline_to_tasks(outline, novel_name)

    print("\n" + "=" * 50)
    print(f"策划完成！文件已保存到 data/{novel_name}/")
    print(f"\n【世界观预览】\n{world[:150]}...")
    print(f"\n【大纲预览】\n{outline[:150]}...")

    return character_names, style_key