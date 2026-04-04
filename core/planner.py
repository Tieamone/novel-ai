import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import re
import json
from core.api_client import call_author_api
from core.memory_manager import MemoryManager
from core.config_loader import get as cfg

# ==================== 篇幅选项 ====================

NOVEL_LENGTH_OPTIONS = {
    "1": {"name": "短篇",   "chapters": 40,  "desc": "30-50章，故事紧凑，单一主线"},
    "2": {"name": "中篇",   "chapters": 80,  "desc": "60-100章，标准网文，情节均衡"},
    "3": {"name": "长篇",   "chapters": 150, "desc": "100-200章，多条支线，慢热叙事"},
    "4": {"name": "超长篇", "chapters": 300, "desc": "200章以上，史诗规模，多弧线"},
    "5": {"name": "自定义", "chapters": None, "desc": "自己输入目标章数"},
}

# ==================== 策划提示词 ====================

WORLD_PROMPT = """你是一位专业的中文网络小说策划师，擅长构建让读者沉浸其中的世界。

请根据用户提供的大纲和主要角色，生成一份详细且有质感的世界观设定。

要求：
1. 世界观必须与大纲内容高度契合，服务于故事的核心冲突
2. 参考角色背景，让世界观能合理容纳这些角色的存在和行动
3. 必须包含四个维度：
   - 地理/物理环境：这个世界长什么样，有什么独特的感官特征（光线、气味、声音）
   - 力量体系/核心规则：这个世界运作的底层逻辑，有什么代价和限制
   - 社会结构与权力格局：谁掌握权力，谁被压迫，普通人过着什么样的生活
   - 世界的伤口：这个世界出了什么问题，或者曾经发生过什么影响至今的事
4. 写出这个世界独有的"气质"——是阴暗压抑、是奇异梦幻、是热血残酷，让读者一眼就感受到
5. 语言简洁但要有细节，总字数500字以内
6. 直接输出设定内容，不加前缀"""

CHARACTER_PROMPT = """你是一位专业的中文网络小说策划师，擅长塑造让读者过目不忘的人物。

请根据用户提供的大纲、世界观和角色名单，为每个角色生成详细档案。

要求：
1. 性格和背景必须与大纲情节逻辑一致，人物是为了推动冲突而存在的
2. 每个角色必须有内在矛盾：他们渴望的东西和他们恐惧的东西之间存在张力
3. 角色之间的关系要有层次：不只是"朋友/敌人"，而是有具体的历史、误解和潜在冲突
4. 每个角色要有独特的说话方式或行为习惯（1-2个具体细节），让读者能辨认出他是谁
5. 每个角色必须包含：
   - 外貌（有特征，不要通用模板）
   - 核心性格（用具体行为而非标签描述，例如"遇到危险会先观察出口"比"谨慎"更有信息量）
   - 隐藏秘密（与主线冲突相关）
   - 致命弱点（真实的，不能靠降智来体现）
   - 初始位置与状态
   - 与其他角色的关系（有具体张力，不只是情感标签）
6. 严格按以下JSON格式输出，不要加任何其他内容：

[
  {
    "name": "角色名",
    "role": "主角/配角/反派",
    "appearance": "外貌（包含1-2个辨识度高的特征）",
    "personality": "核心性格（用行为描述，包含内在矛盾：渴望X但恐惧Y）",
    "secret": "隐藏秘密（与主线冲突相关）",
    "weakness": "致命弱点（真实的，非降智）",
    "current_location": "初始位置",
    "current_status": "初始状态（包含行动和情绪）",
    "relationships": {"其他角色名": "具体关系（包含历史和当前张力）"}
  }
]"""

CHARACTER_EXTRACT_PROMPT = """你是一位专业的小说策划师。
请根据以下大纲，提取或推断出主要角色名单。

要求：
1. 列出大纲中明确提到的角色
2. 如有需要可补充1-2个大纲中暗示但未命名的关键配角（但要在名字后注明"推断"）
3. 每个角色一行，格式：角色名（角色定位）
4. 只输出角色列表，不要其他内容"""


def _build_outline_prompt(target_chapters: int) -> str:
    act1_end = max(10, target_chapters // 5)
    act2_end = max(act1_end + 10, int(target_chapters * 0.75))
    return f"""你是一位专业的中文网络小说策划师，擅长设计让读者停不下来的故事结构。

请根据用户提供的小说基本信息，生成一份完整的总大纲。

要求：
1. 包含三幕结构，与{target_chapters}章的总篇幅严格对应：
   - 开局（1-{act1_end}章）：建立世界感和人物，植入核心悬念，让读者对主角有情感投入
   - 发展（{act1_end+1}-{act2_end}章）：主线冲突层层升级，每段都有新的转折，伏笔交织，支线推进
   - 高潮结局（{act2_end+1}-{target_chapters}章）：矛盾总爆发，伏笔全部兑现，给读者一个意外却合理的结局
2. 每幕列出3-5个关键转折点，注明大致发生在第几章
3. 指出主要伏笔的埋设位置和兑现位置（格式：埋于第X章→兑现于第Y章）
4. 结局要有反转，但反转必须是"读者翻回去看才发现线索一直都在"的那种
5. 节奏要与{target_chapters}章的篇幅匹配：短篇紧凑，长篇有铺陈，不人为压缩或注水
6. 必须给故事一个独特的核心主题句（一句话概括这个故事的灵魂）
7. 总字数800字以内，直接输出大纲内容"""


def _build_task_split_prompt(first_batch: int, full_target: int, outline: str,
                             start: int = 1) -> str:
    if start == 1:
        chapter_range = f"前{first_batch}章"
    else:
        chapter_range = f"第{start}章到第{start + first_batch - 1}章"

    return f"""根据以下小说大纲，生成{chapter_range}的章节任务卡。

全书目标总章数为{full_target}章，请据此控制每章推进幅度，不要赶进度。

任务卡规则：
1. 每章只安排一个核心情节节点（主角做了什么、发现了什么、与谁发生了什么）
2. plot_goal要具体到场景级别：不是"主角继续调查"，而是"主角在废弃图书馆找到一本残缺日记，发现其中有自己名字"
3. 情绪标签的分布要有节奏：不要连续超过3章都是同一个标签，要有起伏
4. 前5章的任务卡要重点建立世界感和人物关系，不要急着上大冲突
5. 每隔5-8章要安排一个"高潮/转折"节点（冲突/爽点/反转），保持读者追读动力

大纲：
{outline}

严格按JSON格式输出，不要任何其他内容：
[
  {{
    "chapter_num": {start},
    "plot_goal": "本章情节目标（40-80字，具体到场景和行动）",
    "emotion_tag": "铺垫"
  }}
]

情绪标签只能从以下5个中选1个：铺垫 / 冲突 / 爽点 / 低谷 / 反转"""


def _build_extend_task_prompt(from_chapter: int, end_chapter: int,
                              full_target: int, outline: str) -> str:
    return f"""根据以下大纲，生成第{from_chapter}章到第{end_chapter}章的任务卡。
全书目标总章数为{full_target}章，当前进行到中段，请据此控制推进节奏。

任务卡规则：
1. 每章只安排一个核心情节节点，具体到场景级别
2. 这一批任务卡处于故事中段，要有新的冲突升级，要有伏笔回收，要推进人物关系变化
3. 每隔5-8章安排一个高潮节点，保持追读驱动力
4. 情绪标签要有起伏，不要连续同一标签超过3章

大纲：
{outline}

JSON格式：
[
  {{
    "chapter_num": {from_chapter},
    "plot_goal": "情节目标（40-80字，具体到场景）",
    "emotion_tag": "铺垫"
  }}
]
情绪标签只能从：铺垫 / 冲突 / 爽点 / 低谷 / 反转 中选1个"""


# ==================== 交互工具 ====================

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


# ==================== Step 0：篇幅选择 ====================

def _choose_novel_length() -> int:
    print("\n" + "=" * 50)
    print("  第零步：确定小说目标篇幅")
    print("=" * 50)
    for k, v in NOVEL_LENGTH_OPTIONS.items():
        chapters_str = f"约{v['chapters']}章" if v["chapters"] else "自定义"
        print(f"  {k}. {v['name']:<6} （{chapters_str}）  {v['desc']}")
    print()
    choice = input("请选择（默认2）：").strip() or "2"
    if choice not in NOVEL_LENGTH_OPTIONS:
        choice = "2"

    opt = NOVEL_LENGTH_OPTIONS[choice]
    if opt["chapters"] is None:
        while True:
            try:
                n = int(input("  请输入目标章数（建议20-500）：").strip())
                if 10 <= n <= 1000:
                    target = n
                    break
                print("  [提示] 请输入10-1000之间的整数")
            except ValueError:
                print("  [错误] 请输入数字")
    else:
        target = opt["chapters"]

    name = opt["name"] if opt["chapters"] else "自定义"
    print(f"\n  [OK] 目标篇幅：{target}章（{name}）")
    return target


# ==================== Step 1：大纲 ====================

def get_outline_choice(genre: str, keywords: str, novel_name: str,
                       target_chapters: int = 100) -> str:
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

    print(f"\n  正在生成总大纲（目标篇幅：{target_chapters}章）...")
    outline = call_author_api(
        system_prompt=_build_outline_prompt(target_chapters),
        user_message=f"小说名：{novel_name}\n类型：{genre}\n关键词/基本设定：{keywords}",
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
    raw = call_author_api(
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


# ==================== Step 3：世界观 ====================

def generate_world(novel_name: str, genre: str, outline: str,
                   character_names: list, mm: MemoryManager,
                   review_mode: bool = False) -> str:
    print("\n  正在根据大纲和角色生成世界观...")
    names_str = "、".join(character_names)
    world = call_author_api(
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
    raw = call_author_api(
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
        print("  [警告] 人物档案解析失败，使用基础档案")
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
                           review_mode: bool = False,
                           target_chapters: int = 0,
                           full_batch: bool = False):
    """
    将大纲拆分为章节任务卡。
    target_chapters: 小说总目标章数（0表示使用config默认值）。
                     第一批次任务卡数量 = min(target_chapters, pre_split_chapters)。
    full_batch: 是否一次性生成全书任务卡（默认False保持分批行为）。
    """
    from core.db import get_connection
    pre_split = cfg("novel", "pre_split_chapters", 50)

    if target_chapters > 0:
        if full_batch:
            first_batch = target_chapters
        else:
            first_batch = min(target_chapters, pre_split)
        full_target = target_chapters
    else:
        first_batch = pre_split
        full_target = pre_split

    if full_batch:
        print(f"\n  正在一次性生成全部{first_batch}章任务卡（全书目标：{full_target}章）...")
    else:
        print(f"\n  正在将大纲拆分为前{first_batch}章任务卡"
              f"（全书目标：{full_target}章）...")

    raw = call_author_api(
        system_prompt="你是小说策划师，将大纲拆解为章节任务。只输出JSON，不要任何其他内容。",
        user_message=_build_task_split_prompt(first_batch, full_target, outline),
        temperature=0.7,
        max_tokens=8000 if first_batch > 100 else 4000,
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
                VALUES (?, ?, ?, '待处理')
            """, (chapter_num, plot_goal, emotion_tag))
            saved += 1

    conn.commit()
    conn.close()
    print(f"  [OK] 已生成 {saved} 个章节任务卡")

    if full_batch and saved < first_batch:
        shortage = first_batch - saved
        print(f"\n  [提示] 任务卡数量不足（{saved}/{first_batch}），正在补充{shortage}章...")
        start_chapter = saved + 1

        supplement_raw = call_author_api(
            system_prompt="你是小说策划师，将大纲拆解为章节任务。只输出JSON，不要任何其他内容。",
            user_message=_build_task_split_prompt(shortage, shortage, outline, start=start_chapter),
            temperature=0.7,
            max_tokens=8000,
        )

        sup_match = re.search(r'\[.*\]', supplement_raw, re.DOTALL)
        if sup_match:
            try:
                sup_tasks = json.loads(sup_match.group())
                if isinstance(sup_tasks, list):
                    conn = get_connection(novel_name)
                    for task in sup_tasks:
                        chapter_num = task.get("chapter_num")
                        plot_goal = task.get("plot_goal", "").strip()
                        emotion_tag = task.get("emotion_tag", "铺垫").strip()
                        if emotion_tag not in valid_tags:
                            emotion_tag = "铺垫"
                        if chapter_num and plot_goal:
                            conn.execute("""
                                INSERT OR REPLACE INTO chapter_tasks
                                (chapter_num, plot_goal, emotion_tag, status)
                                VALUES (?, ?, ?, '待处理')
                            """, (chapter_num, plot_goal, emotion_tag))
                            saved += 1
                    conn.commit()
                    conn.close()
                    print(f"  [OK] 补充完成！总计 {saved} 个章节任务卡")
            except Exception as e:
                print(f"  [警告] 补充任务卡解析失败：{e}")
        else:
            print(f"  [警告] 补充任务卡返回格式错误")

    return saved


def extend_tasks(novel_name: str, from_chapter: int):
    from core.db import get_connection
    from core.config_loader import get as cfg
    from pathlib import Path

    data_dir = Path(cfg("paths", "data_dir", "data")) / novel_name
    outline_path = data_dir / "master_outline.md"
    if not outline_path.exists():
        return

    outline = outline_path.read_text(encoding="utf-8")
    pre_split = cfg("novel", "pre_split_chapters", 50)
    end_chapter = from_chapter + pre_split - 1

    target_path = data_dir / "target_chapters.txt"
    full_target = 0
    if target_path.exists():
        try:
            full_target = int(target_path.read_text(encoding="utf-8").strip())
        except Exception:
            pass

    print(f"  [扩展] 正在生成第{from_chapter}-{end_chapter}章任务卡...")

    raw = call_author_api(
        system_prompt="你是小说策划师，将大纲拆解为章节任务。只输出JSON，不要任何其他内容。",
        user_message=_build_extend_task_prompt(
            from_chapter, end_chapter, full_target or end_chapter, outline
        ),
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
                VALUES (?, ?, ?, '待处理')
            """, (chapter_num, plot_goal, emotion_tag))
    conn.commit()
    conn.close()
    print(f"  [OK] 任务卡已扩展至第{end_chapter}章")


# ==================== 主入口 ====================

def run_planner(novel_name: str, genre: str, keywords: str) -> tuple:
    """
    策划顺序：
    篇幅选择 → 大纲 → 角色名单 → 世界观（含角色信息）→ 人物档案 → 风格 → 任务卡
    """
    print(f"\n开始策划《{novel_name}》...")
    print("=" * 50)

    mm = MemoryManager(novel_name)

    # Step 0：篇幅选择
    target_chapters = _choose_novel_length()
    (mm.data_dir / "target_chapters.txt").write_text(
        str(target_chapters), encoding="utf-8"
    )

    # Step 1：大纲
    outline = get_outline_choice(genre, keywords, novel_name, target_chapters)
    (mm.data_dir / "master_outline.md").write_text(
        f"# 总大纲\n\n{outline}", encoding="utf-8"
    )
    print("  [OK] 大纲已保存")

    # Step 2：角色名单
    character_names = get_characters_choice(outline)
    review_mode = _choose_draft_review_mode()

    # Step 3：世界观
    world = generate_world(
        novel_name, genre, outline, character_names, mm,
        review_mode=review_mode
    )

    # Step 4：人物档案
    generate_characters(
        character_names, outline, world, mm,
        review_mode=review_mode
    )

    # Step 5：写作风格
    style_key = get_style_choice()

    # Step 6：任务卡
    pre_split = cfg("novel", "pre_split_chapters", 50)
    if target_chapters > pre_split:
        print(f"\n任务卡生成方式：")
        print(f"  1. 分批生成（每次{pre_split}章，推荐用于超长篇小说）")
        print(f"  2. 一次性生成全部{target_chapters}章（推荐）")
        batch_choice = input(f"\n请选择（默认2）：").strip() or "2"
        full_batch = (batch_choice == "2")
    else:
        full_batch = True

    split_outline_to_tasks(
        outline, novel_name,
        review_mode=review_mode,
        target_chapters=target_chapters,
        full_batch=full_batch,
    )

    print("\n" + "=" * 50)
    print(f"策划完成！文件已保存到 data/{novel_name}/")
    print(f"目标篇幅：{target_chapters}章")
    print(f"\n【世界观预览】\n{world[:150]}...")
    print(f"\n【大纲预览】\n{outline[:150]}...")

    return character_names, style_key