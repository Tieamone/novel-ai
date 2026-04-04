# 作者风格增强 + 全书任务卡 + 文本导入 + 状态修复 Spec

## Why
用户提出4个优化需求：
1. **作者风格模仿知名作者**（如刘慈欣写科幻）- 提升写作质量和风格辨识度
2. **任务卡一次性生成全书** - 当前只生成前50章，用户希望150章一次性拆解完成
3. **从文本文件导入新建小说** - 用户希望从外部AI获取灵感后通过 `newbook.txt` 导入，跳过策划流程
4. **中文状态识别失败** - 终端显示 `pending` 而非 `待处理`，原因是 `planner.py` 中仍有硬编码的英文状态值

## What Changes
- **修复**: planner.py 中2处硬编码 `'pending'` → 使用中文状态
- **新增**: writer.py 中增加"知名作者模仿"风格选项（刘慈欣/金庸/古龙等）
- **修改**: planner.py 的 split_outline_to_tasks() 支持一次性生成全书任务卡
- **新增**: main.py 新建小说菜单增加"从文本文件导入"选项（读取 newbook.txt）

## Impact
- Affected code:
  - [core/planner.py](file:///d:/novel-ai/core/planner.py) - 修复硬编码 + 全书拆解逻辑
  - [core/writer.py](file:///d:/novel-ai/core/writer.py) - 新增作者模仿风格
  - [main.py](file:///d:/novel-ai/main.py) - 新增导入功能 + 修复提示信息
  - `newbook.txt` (根目录) - 新增：文本导入模板

---

## ADDED Requirements

### Requirement 1: 知名作者模仿风格
系统 SHALL 在写作风格系统中提供"知名作者模仿"选项，让用户可以选择特定作者的写作风格。

#### Scenario: 用户选择"刘慈欣科幻风"
- **WHEN** 用户在新建小说或更换风格时选择"8. 刘慈欣（硬科幻）"
- **THEN** 系统使用刘慈欣风格的 system_prompt，包含：
  - 宏大叙事视角 + 技术细节精确
  - 冷静客观的叙述语调
  - 善用科学概念构建世界观
  - 对话简洁有力，富有哲理

#### Scenario: 用户选择"金庸武侠风"
- **WHEN** 用户选择"9. 金庸（武侠）"
- **THEN** 系统使用金庸风格的 prompt，包含：
  - 历史文化底蕴深厚
  - 武打场面描写细腻
  - 人物性格立体复杂
  - 情节跌宕起伏，伏笔千里

### Requirement 2: 任务卡全书一次性生成
系统 SHALL 支持在策划阶段一次性生成整本书的任务卡（而非分批50章）。

#### Scenario: 目标150章的小说
- **WHEN** 用户创建目标为150章的小说并完成大纲
- **THEN** 系统询问"是否一次性生成全部150章任务卡？"
  - 选择"是": 调用AI一次性输出150章的任务卡JSON
  - 选择"否": 保持原有行为（先生成前50章）

#### Scenario: AI返回超长任务卡
- **WHEN** 一次API调用无法返回全部任务卡（如200章）
- **THEN** 系统自动分批请求，但对用户透明（用户感知为一次性完成）

### Requirement 3: 从文本文件导入新建小说
系统 SHALL 支持从 `newbook.txt` 导入小说基础信息，跳过交互式策划流程。

#### Scenario: 标准导入流程
- **WHEN** 用户在主菜单选择新建小说 → 选择"5. 从文本文件导入"
- **THEN** 系统：
  1. 读取根目录下的 `newbook.txt`
  2. 解析文件内容（自动识别：书名、类型、大纲、角色、世界观等）
  3. 显示解析结果供用户确认
  4. 如果缺少必要信息（如时间线、核心冲突），**主动询问用户补充**
  5. 创建数据库并保存所有信息
  6. 生成全书任务卡（一次性）
  7. 直接进入章节写作菜单

#### Scenario: newbook.txt 格式示例
```
书名：时光当铺
类型：都市奇幻 / 悬疑
目标章数：150

【大纲】
林深是一名心理咨询师，事业受挫后偶然发现一家神秘当铺"拾光"...
（完整大纲内容）

【主要角色】
1. 林深（主角）- 心理咨询师，30岁...
2. 顾念（配角）- 当铺掌柜，年龄不详...

【世界观设定】
拾光当铺：一家存在于时间缝隙中的神秘店铺...
（世界观细节）

【时间线】
- 故事开始：2025年冬
- 核心事件：林深发现当铺
- 高潮：当铺面临关闭危机
- 结局：林深成为新任掌柜

【核心冲突】
时间 vs 命运 - 主角能否改变过去的悲剧？
```

### Requirement 4: 中文状态值一致性修复
系统 SHALL 确保所有写入数据库的状态值都是中文。

#### Scenario: 修复 planner.py 硬编码
- **WHEN** 系统调用 split_outline_to_tasks() 或 extend_tasks()
- **THEN** 写入数据库的状态值为 `'待处理'` 而非 `'pending'`

---

## MODIFIED Requirements

### Requirement: split_outline_to_tasks() 函数重构
修改 `core/planner.py` 的任务卡拆分逻辑：

```python
def split_outline_to_tasks(outline, novel_name, review_mode=False, target_chapters=0):
    # 新增参数: full_batch (bool) - 是否一次性生成全书
    # 修改逻辑:
    #   if full_batch or target_chapters <= pre_split:
    #       first_batch = target_chapters  # 一次性生成全部
    #   else:
    #       first_batch = min(target_chapters, pre_split)  # 分批（原逻辑）
```

### Requirement: main.py 新建小说菜单扩展
在主菜单的新建选项中增加：

```python
print("1. 交互式创建（向导模式）")
print("2. 从文本文件导入（newbook.txt）")  # 新增
print("3. 手动输入基本信息")
```

---

## REMOVED REQUIREMENTS
无（纯增量优化）

---

## 技术实现方案

### 1. 修复中文状态（P0 - 立即修复）

**文件**: [core/planner.py](file:///d:/novel-ai/core/planner.py)

**第595行**:
```python
# ❌ 旧代码
VALUES (?, ?, ?, 'pending')
# ✅ 新代码
VALUES (?, ?, ?, '待处理')
```

**第658行**: 同上

**或者更好的做法**: 从 main.py 导入常量
```python
from main import TASK_PENDING  # 在文件顶部添加
# 然后使用:
VALUES (?, ?, ?, TASK_PENDING)
```

### 2. 知名作者风格（P1 - 新增功能）

**文件**: [core/writer.py](file:///d:/novel-ai/core/writer.py) 的 `AUTHOR_STYLES` 字典

新增以下选项：

```python
"8": {
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

你写的每一章都应该让读者感受到：人类在浩瀚宇宙中的渺小与伟大并存。"""
},

"9": {
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

你写的不仅是武侠，更是人性的江湖画卷。"""
},

"10": {
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

你的文字应该让读者感受到：最致命的武器不是刀，而是人心。"""
},
```

### 3. 全书任务卡一次性生成（P1 - 功能增强）

**文件**: [core/planner.py](file:///d:/novel-ai/core/planner.py) 的 `split_outline_to_tasks()`

**关键改动**:

```python
def split_outline_to_tasks(outline, novel_name, review_mode=False, target_chapters=0,
                          full_batch=False):  # 新增参数
    from core.db import get_connection
    pre_split = cfg("novel", "pre_split_chapters", 50)

    if target_chapters > 0:
        if full_batch:
            # ✨ 新逻辑：一次性生成全书
            first_batch = target_chapters
            print(f"\n  正在一次性生成全部{first_batch}章任务卡...")
        else:
            # 原逻辑：分批
            first_batch = min(target_chapters, pre_split)
        full_target = target_chapters
    else:
        first_batch = pre_split
        full_target = pre_split
    
    # ... 后续逻辑不变，但需要处理大批量输出的情况
```

**AI Prompt 调整**（针对大批量）:

```python
if first_batch > 100:
    # 超过100章时，提示AI精简输出
    system_prompt += "\n注意：章节数较多，请确保JSON格式正确，不要省略任何章节。"
    max_tokens = 8000  # 给更多空间
else:
    max_tokens = 4000
```

**调用处修改** ([main.py run_planner()](file:///d:/novel-ai/main.py)):

```python
# Step 6: 任务卡
print("\n任务卡生成方式：")
print(f"  1. 分批生成（每次{pre_split}章，推荐用于长篇小说）")
print(f"  2. 一次性生成全部{target_chapters}章")
batch_choice = input("请选择（默认2）：").strip() or "2"

split_outline_to_tasks(
    outline, novel_name,
    review_mode=review_mode,
    target_chapters=target_chapters,
    full_batch=(batch_choice == "2"),
)
```

### 4. 从文本文件导入（P2 - 新功能）

**文件**: [main.py](file:///d:/novel-ai/main.py) 新增函数 `_import_from_text_file()`

**核心逻辑**:

```python
def _import_from_text_file():
    """从 newbook.txt 导入小说信息"""
    import re
    from pathlib import Path
    
    txt_path = Path("newbook.txt")
    if not txt_path.exists():
        print("[错误] 未找到 newbook.txt，请先将文件放在项目根目录")
        return None
    
    content = txt_path.read_text(encoding="utf-8")
    
    # 解析基本信息
    novel_name = _extract_field(content, "书名")
    genre = _extract_field(content, "类型")
    target_chapters_str = _extract_field(content, "目标章数")
    
    try:
        target_chapters = int(target_chapters_str) if target_chapters_str else 100
    except:
        target_chapters = 100
        print("[提示] 未检测到目标章数，默认100章")
    
    outline = _extract_section(content, "【大纲】")
    characters_text = _extract_section(content, "【主要角色】")
    world_setting = _extract_section(content, "【世界观设定】")
    timeline = _extract_section(content, "【时间线】")
    core_conflict = _extract_section(content, "【核心冲突】")
    
    # 显示解析结果
    print(f"\n{'='*50}")
    print(f"  解析结果预览")
    print(f"{'='*50}")
    print(f"  书名：{novel_name}")
    print(f"  类型：{genre}")
    print(f"  目标章数：{target_chapters}")
    print(f"  大纲：{(outline[:100] + '...') if len(outline) > 100 else outline}")
    print(f"  角色：{(characters_text[:100] + '...') if len(characters_text) > 100 else characters_text}")
    
    # 检查缺失项
    missing = []
    if not timeline:
        missing.append("时间线")
    if not core_conflict:
        missing.append("核心冲突")
    
    if missing:
        print(f"\n  ⚠️ 检测到缺少以下信息：{', '.join(missing)}")
        supplement = input("  是否现在补充？（y/n）：").strip().lower()
        if supplement == 'y':
            if "时间线" in missing:
                timeline = input("  请输入时间线信息：\n").strip()
            if "核心冲突" in missing:
                core_conflict = input("  请输入核心冲突：\n").strip()
    
    confirm = input(f"\n  确认导入《{novel_name}》？（yes/NO）：").strip()
    if confirm.lower() != 'yes':
        print("  已取消导入")
        return None
    
    return {
        "novel_name": novel_name,
        "genre": genre,
        "target_chapters": target_chapters,
        "outline": outline,
        "characters_text": characters_text,
        "world_setting": world_setting,
        "timeline": timeline,
        "core_conflict": core_conflict,
    }
```

**主菜单集成**:

```python
# main() 函数中
if choice == "1":
    print("\n新建小说方式：")
    print("  1. 交互式创建（向导模式）")
    print("  2. 从文本文件导入（newbook.txt）")
    
    mode = input("请选择（默认1）：").strip() or "1"
    
    if mode == "2":
        import_data = _import_from_text_file()
        if not import_data:
            continue
        
        novel_name = import_data["novel_name"]
        genre = import_data["genre"]
        
        init_database(novel_name)
        _write_novel_info(novel_name, genre)
        
        mm = MemoryManager(novel_name)
        
        # 保存各部分信息
        if import_data["outline"]:
            (mm.data_dir / "master_outline.md").write_text(
                f"# 总大纲\n\n{import_data['outline']}", encoding="utf-8"
            )
        if import_data["world_setting"]:
            mm.save_world_settings(import_data["world_setting"])
        if import_data["characters_text"]:
            # 解析角色并保存
            _parse_and_save_characters(mm, import_data["characters_text"])
        
        # 选择风格
        style_key = get_style_choice()
        (mm.data_dir / "style.txt").write_text(style_key, encoding="utf-8")
        
        # 一次性生成全部任务卡
        split_outline_to_tasks(
            import_data["outline"], novel_name,
            target_chapters=import_data["target_chapters"],
            full_batch=True,
        )
        
        print(f"\n✅ 导入完成！《{novel_name}》已就绪，共{import_data['target_chapters']}章任务卡")
        chapters_menu(novel_name)
    else:
        # 原有的交互式创建流程
        novel_name, genre, keywords = setup_novel()
        # ...
```

---

## 验证标准

1. **状态修复验证**:
   - 运行系统，新建小说 → 生成任务卡 → 查看任务卡状态应为"待处理"
   - 终端提示不应再出现英文 "pending"

2. **作者风格验证**:
   - 更换写作风格时应能看到"8. 刘慈欣""9. 金庸""10. 古龙"选项
   - 选择后生成的章节应具有对应作者的风格特征

3. **全书任务卡验证**:
   - 创建150章小说时，应能一次性生成150章任务卡
   - 数据库 chapter_tasks 表应有150条记录，状态均为"待处理"

4. **文本导入验证**:
   - 在根目录创建 newbook.txt 并填写内容
   - 选择"从文本文件导入"应能正确解析并创建小说
   - 缺少信息时应主动提示补充
