import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import re
import json
from core.api_client import call_api
from core.memory_manager import MemoryManager
from core.config_loader import get as cfg

WORLD_PROMPT = """你是一位专业的中文网络小说策划师。
请根据用户提供的大纲和主要角色信息，生成一份详细的世界观设定。

要求：
1. 世界观必须与大纲内容高度契合，不自由发挥偏离大纲
2. 参考角色背景，让世界观能合理容纳这些角色
3. 必须包含：地理环境、力量体系（或核心设定）、社会结构、核心规则
4. 语言简洁，总字数500字以内
5. 直接输出设定内容，不加任何前缀"""

CHARACTER_PROMPT = """你是一位专业的中文网络小说策划师。
请根据用户提供的大纲和角色名单，为每个角色生成详细档案。

要求：
1. 人物性格和背景必须与大纲情节逻辑一致
2. 每个角色必须包含：外貌、性格核心、隐藏秘密、致命弱点、初始位置、初始状态
3. 角色之间的关系要服务于大纲的核心冲突
4. 严格按以下JSON格式输出，不要加任何其他内容：

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


def _read_block_input() -> str:
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip().upper() == "END":
            break
        lines.append(line)
    return "\n".join(lines).strip()


def _choose_draft_review_mode() -> bool:
    print("\n" + "=" * 50)
    print("  策划结果确认模式")
    print("=" * 50)
    print("  1. 关闭（默认，直接采用AI结果）")
    print("  2. 开启（世界观/人物档案/任务卡先看草稿再确认）")
    print()
    choice = input("请选择（默认1）：").strip() or "1"
    enabled = (choice == "2")
    if enabled:
        print("  [OK] 已开启 AI草稿后人工编辑确认模式")
    return enabled


def _confirm_text_draft(step_name: str, draft_text: str) -> str:
    print(f"\n【{step_name} AI草稿预览】")
    preview = (draft_text or "").strip()
    if len(preview) > 300:
        print(preview[:300] + "...")
    else:
        print(preview or "（空）")
    print()
    print("  1. 直接采用草稿（默认）")
    print("  2. 手动编辑后采用")
    choice = input("请选择：").strip() or "1"
    if choice != "2":
        return draft_text

    print("\n  请粘贴修改后的完整内容。")
    print("  输入 END 单独一行结束。")
    print("-" * 50)
    edited = _read_block_input()
    if not edited:
        print("  [提示] 未输入内容，继续使用AI草稿")
        return draft_text
    print(f"  [OK] 已采用手动编辑版，共 {len(edited)} 字")
    return edited


def _confirm_json_draft(step_name: str, draft_obj, preview_items: int = 3):
    print(f"\n【{step_name} AI草稿预览】")
    preview_obj = draft_obj
    total_items = None
    if isinstance(draft_obj, list):
        total_items = len(draft_obj)
        if len(draft_obj) > preview_items:
            preview_obj = draft_obj[:preview_items]
    try:
        print(json.dumps(preview_obj, ensure_ascii=False, indent=2))
    except Exception:
        print(str(preview_obj))
    if total_items is not None and total_items > preview_items:
        print(f"\n  （仅预览前{preview_items}条，共{total_items}条）")
    print()
    print("  1. 直接采用草稿（默认）")
    print("  2. 手动编辑JSON后采用")
    choice = input("请选择：").strip() or "1"
    if choice != "2":
        return draft_obj

    while True:
        print("\n  请粘贴修改后的完整JSON。")
        print("  输入 END 单独一行结束。")
        print("-" * 50)
        edited_raw = _read_block_input()
        if not edited_raw:
            print("  [提示] 未输入内容，继续使用AI草稿")
            return draft_obj
        try:
            edited_obj = json.loads(edited_raw)
        except Exception as e:
            print(f"  [错误] JSON解析失败：{e}")
            retry = input("  输入 r 重试，其他键使用AI草稿：").strip().lower()
            if retry == "r":
                continue
            return draft_obj
        print("  [OK] 已采用手动编辑JSON")
        return edited_obj


# ==================== Step 1：大纲 ====================

def get_outline_choice(genre: str, keywords: str, novel_name: str) -> str:
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
        print("  [提示] 未收到内容，改为AI生成")

    print("\n  正在生成总大纲...")
    outline = call_api(
        system_prompt=OUTLINE_GEN_PROMPT,
        user_message=f"小说名：{novel_name}\n类型：{genre}\n关键词：{keywords}",
        temperature=0.9,
    )
    print("  [OK] 总大纲已生成")
    return outline


def _get_outline_from_user() -> str:
    print()
    print("  请将大纲内容粘贴到下方。")
    print("  粘贴完成后，在新的一行单独输入 END 并回车结束。")
    print("-" * 50)
    outline = _read_block_input()
    if outline:
        print(f"  [OK] 已接收大纲，共 {len(outline)} 字")
    return outline


# ==================== Step 2：角色 ====================

def get_characters_choice(outline: str) -> list:
    print("\n" + "=" * 50)
    print("  第二步：确定角色名单")
    print("=" * 50)
    print("  1. AI从大纲中自动提取角色")
    print("  2. 我自己输入角色名字")
    print()
    choice = input("请选择（默认1）：").strip() or "1"

    if choice == "2":
        return _manual_input_characters()

    print("\n  正在从大纲提取角色...")
    raw = call_api(
        system_prompt=CHARACTER_EXTRACT_PROMPT,
        user_message=f"大纲内容：\n{outline}",
        temperature=0.5,
        max_tokens=300,
    )

    names = []
    for line in raw.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        match = re.match(r'^[\d\.\s]*([^\s（(【]+)', line)
        if match:
            name = match.group(1).strip()
            if name and len(name) <= 10:
                names.append(name)

    if names:
        print(f"  [OK] 从大纲提取到 {len(names)} 个角色：{' / '.join(names)}")
        confirm = input(
            "  确认使用以上角色？(回车确认 / 输入n重新手动输入)："
        ).strip()
        if confirm.lower() == "n":
            return _manual_input_characters()
        return names

    print("  [警告] 未能提取到角色，请手动输入")
    return _manual_input_characters()


def _manual_input_characters() -> list:
    names_input = input("  请输入角色名字，用逗号分隔：").strip()
    names = [
        n.strip() for n in names_input.replace(",", "，").split("，")
        if n.strip()
    ]
    if not names:
        names = ["主角"]
    print(f"  [OK] 已设置 {len(names)} 个角色：{' / '.join(names)}")
    return names


# ==================== Step 3：世界观（在角色之后）====================

def generate_world(novel_name: str, genre: str, outline: str,
                   character_names: list, mm: MemoryManager,
                   review_mode: bool = False) -> str:
    print("\n  正在根据大纲和角色生成世界观...")
    names_str = "、".join(character_names)
    world = call_api(
        system_prompt=WORLD_PROMPT,
        user_message=(
            f"小说类型：{genre}\n\n"
            f"主要角色：{names_str}\n\n"
            f"大纲内容：\n{outline}"
        ),
        temperature=0.85,
    )
    if review_mode:
        world = _confirm_text_draft("世界观", world)
    mm.save_world_settings(world)
    print("  [OK] 世界观已生成并保存")
    return world


# ==================== Step 4：人物档案 ====================

def generate_characters(character_names: list, outline: str,
                        world: str, mm: MemoryManager,
                        review_mode: bool = False) -> list:
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
        print(f"  [警告] 人物档案解析失败，使用基础档案")
        for name in character_names:
            mm.save_character(name, {
                "role": "待确认", "appearance": "", "personality": "",
                "secret": "", "weakness": "",
                "current_location": "", "current_status": "",
                "relationships": {}
            })
        return [{"name": n} for n in character_names]

    try:
        characters = json.loads(match.group())
    except Exception:
        print("  [警告] JSON解析失败，使用基础档案")
        for name in character_names:
            mm.save_character(name, {
                "role": "待确认", "appearance": "", "personality": "",
                "secret": "", "weakness": "",
                "current_location": "", "current_status": "",
                "relationships": {}
            })
        return [{"name": n} for n in character_names]

    if review_mode:
        characters = _confirm_json_draft("人物档案JSON", characters, 2)

    if not isinstance(characters, list):
        characters = []

    normalized = []
    used_names = set()
    fallback_names = iter(character_names)

    for char in characters:
        if not isinstance(char, dict):
            continue

        name = str(char.get("name", "")).strip()
        if not name:
            while True:
                try:
                    candidate = str(next(fallback_names)).strip()
                except StopIteration:
                    candidate = ""
                if candidate and candidate not in used_names:
                    name = candidate
                    break
                if not candidate:
                    break
        if not name or name in used_names:
            continue
        used_names.add(name)

        relationships = char.get("relationships", {})
        if not isinstance(relationships, dict):
            relationships = {}

        cleaned = {
            "role": str(char.get("role", "待确认") or "待确认"),
            "appearance": str(char.get("appearance", "") or ""),
            "personality": str(char.get("personality", "") or ""),
            "secret": str(char.get("secret", "") or ""),
            "weakness": str(char.get("weakness", "") or ""),
            "current_location": str(char.get("current_location", "") or ""),
            "current_status": str(char.get("current_status", "") or ""),
            "relationships": relationships,
        }
        mm.save_character(name, cleaned)
        normalized.append({"name": name, **cleaned})

    if not normalized:
        print("  [警告] 人物档案缺少有效姓名，使用基础档案")
        for name in character_names:
            mm.save_character(name, {
                "role": "待确认", "appearance": "", "personality": "",
                "secret": "", "weakness": "",
                "current_location": "", "current_status": "",
                "relationships": {}
            })
        return [{"name": n} for n in character_names]

    print(f"  [OK] {len(normalized)} 个人物档案已生成并保存")
    return normalized


# ==================== Step 5：风格 ====================

def get_style_choice() -> str:
    from core.writer import AUTHOR_STYLES

    print("\n" + "=" * 50)
    print("  第五步：选择写作风格")
    print("=" * 50)
    for key, style in AUTHOR_STYLES.items():
        print(f"  {key}. {style['name']:<12} {style['desc']}")
    print("  7. 自定义风格（自己描述写作风格）")
    print()

    choice = input("请输入编号（默认1）：").strip() or "1"

    if choice == "7":
        print()
        custom_desc = input("  请描述你想要的写作风格：").strip()
        if custom_desc:
            print("  [OK] 已设置自定义风格")
            return f"custom:{custom_desc}"
        print("  [提示] 未输入描述，使用默认风格")
        return "1"

    if choice not in AUTHOR_STYLES:
        choice = "1"

    print(f"\n  [OK] 已选择风格：{AUTHOR_STYLES[choice]['name']}\n")
    return choice


# ==================== Step 6：任务卡 ====================

def split_outline_to_tasks(outline: str, novel_name: str,
                           review_mode: bool = False):
    from core.db import get_connection
    total = cfg("novel", "pre_split_chapters", 50)
    print(f"\n  正在将大纲拆分为前{total}章任务卡...")

    raw = call_api(
        system_prompt="你是小说策划师，将大纲拆解为章节任务。只输出JSON。",
        user_message=f"""根据以下小说大纲，生成前{total}章的章节任务卡。

大纲：
{outline}

严格按JSON格式输出：
[
  {{
    "chapter_num": 1,
    "plot_goal": "本章情节目标（50字以内）",
    "emotion_tag": "铺垫"
  }}
]

情绪标签只能从：铺垫/冲突/爽点/低谷/反转 中选一个""",
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

    if review_mode:
        tasks = _confirm_json_draft("任务卡JSON", tasks, 5)
    if not isinstance(tasks, list):
        print("  [警告] 任务卡不是列表结构，将使用实时分析模式")
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
    from core.db import get_connection
    from core.config_loader import get as cfg
    outline_path = (
        __import__("pathlib").Path(
            cfg("paths", "data_dir", "data")
        ) / novel_name / "master_outline.md"
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


# ==================== 主入口 ====================

def run_planner(novel_name: str, genre: str, keywords: str) -> tuple:
    """
    正确策划顺序：
    大纲 → 角色名单 → 世界观（含角色信息）→ 人物档案 → 风格 → 任务卡
    """
    print(f"\n开始策划《{novel_name}》...")
    print("=" * 50)

    mm = MemoryManager(novel_name)

    # Step 1：大纲
    outline = get_outline_choice(genre, keywords, novel_name)
    (mm.data_dir / "master_outline.md").write_text(
        f"# 总大纲\n\n{outline}", encoding="utf-8"
    )
    print("  [OK] 大纲已保存")

    # Step 2：角色名单
    character_names = get_characters_choice(outline)
    review_mode = _choose_draft_review_mode()

    # Step 3：世界观（参考大纲+角色）
    world = generate_world(
        novel_name, genre, outline, character_names, mm,
        review_mode=review_mode
    )

    # Step 4：人物档案（参考大纲+世界观）
    generate_characters(
        character_names, outline, world, mm,
        review_mode=review_mode
    )

    # Step 5：写作风格
    style_key = get_style_choice()

    # Step 6：任务卡
    split_outline_to_tasks(outline, novel_name, review_mode=review_mode)

    print("\n" + "=" * 50)
    print(f"策划完成！文件已保存到 data/{novel_name}/")
    print(f"\n【世界观预览】\n{world[:150]}...")
    print(f"\n【大纲预览】\n{outline[:150]}...")

    return character_names, style_key
