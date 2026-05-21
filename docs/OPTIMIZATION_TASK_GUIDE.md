# 竞品优化任务书

> 创建日期：2026-05-21
> 来源：`docs/newidea.md` 竞品分析（inkos/autonovel/NovelForge）
> 当前分支：`feature`
> 状态：待实施

---

## 一、背景与目标

分析了 3 个竞品项目的核心优化点，筛选出 5 个与当前 Python CLI 技术栈兼容、能直接提升输出稳定性的优化。

**核心目标**：风格一致性 ↑、AI 痕迹 ↓、文字密度 ↑、Token 浪费 ↓、剧情连贯性 ↑

---

## 二、优化清单

### P0 第一梯队（1-2 天，改动最小收益最大）

---

#### 优化 A：平台期检测

**来源**：autonovel — "当分数稳定时停止循环"

**问题**：当前 `write_and_review` 固定 3 轮重试，可能"越改越差还不自知"，浪费 token。

**方案**：在重试循环中记录最近 N 轮评分，波动 < 阈值时提前终止→强制通过。

**改动文件**：`core/reviewer.py` 的 `write_and_review` 函数

**改动量**：~20 行

**实现要点**：
```
1. 在 for attempt in range(max_retry) 前加 scores_history = []
2. 每次 review_chapter 返回后追加 result.get("score_total", 0)
3. 在重试判断前检测：
   PLATEAU_THRESHOLD = 5  # 可配置
   if len(scores_history) >= 3:
       recent = scores_history[-3:]
       if max(recent) - min(recent) < PLATEAU_THRESHOLD:
           print("[平台期] 停止重试→强制通过")
           break
4. config.yaml 中加 novel.plateau_threshold: 5 和 novel.plateau_window: 3
```

**验证方式**：模拟连续 3 轮评分相近（72, 73, 72），确认系统提前终止。

---

#### 优化 B：输入治理控制面

**来源**：inkos — `author_intent.md` + `current_focus.md`

**问题**：每章只有 `plot_goal` + `emotion_tag`，章与章之间是孤立的。写到第 50 章可能忘了第 10 章埋下的基调。

**方案**：新增 2 个可编辑 .md 文件，注入到写作 prompt 中。

**改动文件**：
- `core/writer.py` — `build_full_chapter_prompt` 和 `build_writer_prompt` 读取并注入
- `main.py` — 创建小说时生成模板 + `chapters_menu` 新增"编辑创作意图"

**改动量**：~80 行

**实现要点**：
```
1. 创建新小说时生成两个模板文件：
   - data/{小说名}/author_intent.md（含引导问题）
     # 创作意图
     ## 这本书想成为什么
     （基调、主题、核心吸引力）
     ## 不想变成什么样
     （避免的风格、雷区）

   - data/{小说名}/current_focus.md（空白模板）
     # 当前焦点
     ## 最近 1-3 章的重点
     （当前弧线的核心冲突、需要推进的线索）

2. writer.py 的 build_full_chapter_prompt 中注入：
   if (data_dir / "author_intent.md").exists():
       prompt += f"\n=== 创作意图 ===\n{读取内容}\n"
   if (data_dir / "current_focus.md").exists():
       prompt += f"\n=== 当前焦点 ===\n{读取内容}\n"

3. chapters_menu 新增选项 "17. 编辑创作意图"
   子菜单：1. 编辑长期意图  2. 编辑当前焦点
   用 os.startfile() 或 subprocess 打开 .md 文件

4. _save_chapter_memory 完成后提示是否更新 current_focus.md
```

**验证方式**：创建新小说确认模板存在；编辑后写一章，确认 prompt 包含意图内容。

---

### P1 第二梯队（3-5 天）

---

#### 优化 C：Anti-Slop L1 扩展

**来源**：inkos 词汇疲劳词表 + autonovel 机械层

**问题**：现有 37 条正则只覆盖词级，无法检测结构性 AI 痕迹（如每章都以"XXX心想"结尾）。

**方案**：新增 15-20 条正则 + 跨章词频统计。

**改动文件**：`core/writer.py` 的 `AI_PATTERNS` 和 `_rule_based_ai_check`

**改动量**：~80 行

**实现要点**：
```
1. 新增正则模式（参考 inkos 禁用句式）：
   - 模板化情绪：r'心中涌起一股.*感'、r'不禁.*起来'、r'下意识地.*了'
   - 模板化动作：r'深吸一口气'、r'握紧拳头'、r'瞳孔一缩'、r'嘴角微微上扬'
   - 模板化收束：r'这一切.*才刚开始'、r'故事.*远未结束'、r'无论.*都.*'
   - 对称句式滥用：r'一方面.*另一方面'、r'不是.*而是.*而是'
   - 注水表达：r'某种.*感觉'、r'一种.*说不清道不明'

2. 新增 _check_cross_chapter_frequency(novel_name, current_chapter):
   - 从 memory_manager 加载最近 3 章摘要
   - 统计高频词出现次数
   - 同一词连续 3 章每章出现 >3 次 → 返回警告

3. review_chapter 调用链中传入最近章节文本
```

**验证方式**：用含 AI 痕迹的测试文本调用 `_rule_based_ai_check`，确认新增模式被检测。

---

#### 优化 D：文法指纹分析

**来源**：inkos 双层指纹架构（统计层 + 语义层）

**问题**：现有 9 种固定风格模板是泛化的，无法精确模仿特定作者的语感。

**方案**：用户上传参考文本 → AI 提取指纹 → 注入写手 prompt。提取一次，全书复用。

**改动文件**：
- **新建** `core/style_analyzer.py`（~250 行）
- `core/writer.py` — prompt 注入
- `main.py` — 菜单交互

**改动量**：~350 行

**实现要点**：
```
1. core/style_analyzer.py 核心函数：

   def analyze_style(reference_text: str) -> dict:
       """调用 call_author_api() 提取文法指纹"""
       # 统计层（不依赖 LLM）：
       #   - 句长分布（平均值、标准差、短句/长句比例）
       #   - 对话比例（对话行数 / 总行数）
       #   - 段落平均长度
       #   - 高频词 top20 / 低频特色词
       # 语义层（调用 LLM）：
       #   - 修辞偏好（比喻频率、排比使用、口语化程度）
       #   - 节奏特征（快节奏/慢节奏、紧张/舒缓交替模式）
       #   - 叙事视角偏好
       #   - 情感表达方式（直白/含蓄/通过动作暗示）
       return {"stats": {...}, "style_guide": "..."}

   def save_fingerprint(novel_name, fingerprint):
       """保存到 data/{小说名}/style_fingerprint.json"""

   def load_fingerprint(novel_name) -> dict | None:
       """从文件加载"""

   def build_style_prompt(fingerprint) -> str:
       """转换为可注入 prompt 的文本块"""

2. writer.py 的 build_full_chapter_prompt 中：
   from core.style_analyzer import load_fingerprint, build_style_prompt
   fp = load_fingerprint(novel_name)
   if fp:
       prompt += f"\n=== 文法指纹 ===\n{build_style_prompt(fp)}\n"

3. main.py chapters_menu 中：
   "更换写作风格" 新增子选项 "上传参考文本生成文法指纹"
   让用户输入文件路径 → 读取 → 调用 analyze_style → 保存
```

**验证方式**：上传一段参考文本，生成指纹后写一章，对比风格差异。

---

#### 优化 E：对抗性精简

**来源**：autonovel — "Cut 500 words" 精神

**问题**：AI 生成容易"水字数"，大段无用描写、重复心理活动、唠嗑式对话。

**方案**：审稿阶段新增冗余检测维度，AI 标注可裁切段落，用户选择性应用。

**改动文件**：`core/reviewer.py` 的 `REVIEWER_SYSTEM` prompt

**改动量**：~60 行

**实现要点**：
```
1. 在 REVIEWER_SYSTEM 的 L3 评分中新增冗余检测：
   → 冗余检测（redundancy）：
   - 标注可裁切的段落（给出段落首句和裁切理由）
   - 标注可精简的对话（哪些对话对情节无贡献）
   - 评分 0-10（10=无冗余，0=大量水字数）
   - 输出格式：cut_suggestions: [{"start": "首句", "reason": "理由"}]

2. _normalize_review_result 中解析 cut_suggestions 字段

3. write_and_review 审稿通过后：
   if result.get("cut_suggestions"):
       print(f"[精简建议] 发现 {len(cut_suggestions)} 处可裁切段落，是否查看？(y/n)")
       if input == "y":
           逐条展示，用户选择应用哪些
```

**验证方式**：写一章含冗余段落的文本，审稿后确认 `cut_suggestions` 被正确输出。

---

## 三、不采纳项（已排除）

| 优化点 | 来源 | 排除原因 |
|--------|------|----------|
| 每章运行时产物（intent/context/rule-stack/trace） | inkos | CLI 项目过重，维护成本高 |
| 双人格评审（文学评论家+小说教授） | autonovel | 已有责任编辑+读者视角双重审核，边际收益低 |
| 卡片式创作 UI | NovelForge | Electron+Vue3 架构完全不兼容 |
| @DSL 上下文注入系统 | NovelForge | 过度工程化，现有 prompt 注入已够用 |
| 五层共进架构+传播债务跟踪 | autonovel | 已有分层管理，过度设计 |
| EPUB 导出 | inkos/autonovel | 锦上添花，不提升输出质量 |
| 多模型路由细化 | inkos | 已有 3 种模型分离，进一步细化是成本优化非质量优化 |
| 章节反向导入 | inkos | 对稳定输出无直接帮助 |
| 守护进程+通知 | inkos | 对输出质量无帮助 |
| 多人格读者面板 | autonovel | 单人读者视角已够用 |
| 封面/有声书生成 | inkos/autonovel | 偏离核心写作目标 |

---

## 四、依赖关系

```
优化 A（平台期检测）── 独立，无依赖
优化 B（输入治理）  ── 独立，无依赖
优化 C（Anti-Slop L1）── 独立，无依赖
优化 D（文法指纹）  ── 独立，无依赖
优化 E（对抗性精简）── 独立，无依赖
```

5 个优化互相独立，可并行开发，也可任意顺序实施。

---

## 五、关键文件索引

| 文件 | 职责 | 涉及优化 |
|------|------|----------|
| `core/reviewer.py` | 审稿模块（伏笔评分、自动兑现、write_and_review） | A, E |
| `core/writer.py` | 写作 prompt 构建（build_full_chapter_prompt） | B, C, D |
| `main.py` | 主入口（菜单、流程控制、小说创建） | B, D |
| `core/style_analyzer.py` | **新建** — 文法指纹分析 | D |
| `core/config_loader.py` | 配置加载 | A |
| `config.yaml` | 配置文件 | A |

---

## 六、验证策略

每个优化完成后：
1. 写一章完整流程（写作→审稿→保存），确认无回归
2. 按各优化标注的具体验证方式逐项确认
3. 检查 prompt 输出（print 或日志），确认新注入内容存在

---

## 七、实施记录

| 日期 | 优化 | 状态 | 备注 |
|------|------|------|------|
| | A. 平台期检测 | 待实施 | |
| | B. 输入治理控制面 | 待实施 | |
| | C. Anti-Slop L1 扩展 | 待实施 | |
| | D. 文法指纹分析 | 待实施 | |
| | E. 对抗性精简 | 待实施 | |
