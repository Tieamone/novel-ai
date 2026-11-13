<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const sections = [
  ['chapters', '章节与审稿', '▤'], ['tasks', '章节任务卡', '▦'], ['foreshadow', '伏笔管理', '◇'],
  ['writing', '创作资料', '✎'], ['checkpoints', '断点管理', '◷'], ['models', '模型与费用', '⚙'],
]
const labels = { planned: '待埋入', planted: '已埋入', resolved: '已兑现', writing: '写作中', pending_review: '待重审', pending: '待处理', draft: '草稿' }
const stateLabels = { running: '执行中', waiting: '等待确认', stopping: '正在停止', completed: '已结束', cancelled: '已停止', failed: '未完成' }
const token = ref('')
const novels = ref([])
const selected = ref('')
const book = ref(null)
const view = ref('chapters')
const chapter = ref(null)
const query = ref('')
const filter = ref('')
const sectionError = ref('')
const notice = ref('')
const busy = ref(false)
const starting = ref(false)
const dialog = ref(null)
const job = ref(null)
const jobOpen = ref(false)
const jobLog = ref([])
const answer = ref('')
const sendingAnswer = ref(false)
const jobError = ref('')
const models = ref(null)
const modelForm = ref({})
const modelBusy = ref(false)
const modelJob = ref(null)
const modelLog = ref([])
const startingKind = ref('')
const readAt = ref('')
const styleForm = ref({ style: '1', custom: '' })
const intentKey = ref('intent')
const intentDrafts = ref({})
const intentText = computed({
  get: () => intentDrafts.value[intentKey.value] ?? book.value?.intent[intentKey.value] ?? '',
  set: value => { intentDrafts.value[intentKey.value] = value },
})
const checkpointNumber = ref(1)
const checkpoint = ref(null)
const checkpointLoaded = ref(null)
const checkpointActions = [
  ['reset', '重置为待处理', '该任务会恢复为待处理，节拍内存缓存将清除。'],
  ['rewrite', '重置正文，保留摘要', '章节正文和审稿评分会清除，摘要保留。'],
  ['clear_beats', '清除节拍缓存', '清除该章节的已存节拍，下次写作会重新规划。'],
]
const healthGroups = [['due_soon', '即将到期'], ['overdue', '严重超期'], ['macro', '宏观悬念']]
const approval = ['已审核', '强制通过']
let timer
let bookRequest = 0
let chapterRequest = 0
let checkpointRequest = 0
let modelRequest = 0

const activeJob = computed(() => ['running', 'waiting', 'stopping'].includes(job.value?.status))
const verifying = computed(() => startingKind.value === 'models' ||
  (job.value?.kind === 'models' && activeJob.value))
const submittingAnswer = computed(() => sendingAnswer.value === job.value?.prompt?.id)
const filteredChapters = computed(() => (book.value?.chapters || []).filter(row => {
  const word = query.value.trim().toLowerCase()
  return (!filter.value || row.status === filter.value) &&
    (!word || [row.title, row.chapter_num, row.emotion_tag].some(item => String(item ?? '').toLowerCase().includes(word)))
}))
const filteredTasks = computed(() => (book.value?.tasks || []).filter(row => {
  const word = query.value.trim().toLowerCase()
  return !word || [row.chapter_num, row.plot_goal, row.emotion_tag, row.status]
    .some(item => String(item ?? '').toLowerCase().includes(word))
}))
const approved = computed(() => book.value?.progress.approved || 0)
const staleTasks = computed(() => (book.value?.tasks || []).filter(row => row.stale))
const percent = computed(() => Math.min(100, Math.round(100 * approved.value / (book.value?.target || 1))))
const chapterStatuses = computed(() => [...new Set((book.value?.chapters || []).map(row => row.status).filter(Boolean))])
const params = novel => new URLSearchParams({ novel }).toString()
const download = (name, novel) => '/api/download/' + encodeURIComponent(name) + '?' + params(novel)
const statusText = value => labels[value] || value || '待处理'
const size = value => Number(value || 0).toLocaleString('zh-CN')
const money = value => '¥' + Number(value || 0).toFixed(4)
const providerName = value => ({ dashscope: '阿里云百炼', mimo: '小米 MiMo' })[value] || value
const verifyStatus = { passed: '服务商已回应', failed: '请求失败', skipped: '未发出请求' }
const styleFields = value => value.startsWith('custom:')
  ? { style: 'custom', custom: value.slice(7) } : { style: value, custom: '' }

async function api(path, body) {
  const options = body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Workbench-Token': token.value },
    body: JSON.stringify(body),
  }
  const response = await fetch('/api' + path, { ...options, cache: 'no-store' })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    const error = Error(data.error || '无法连接工作台，请稍后重试')
    error.status = response.status
    throw error
  }
  return data
}

async function loadLibrary() {
  const data = await api('/bootstrap')
  token.value = data.token
  novels.value = data.novels
  if (!selected.value || !data.novels.some(row => row.name === selected.value)) {
    const saved = localStorage.getItem('novel-workbench-book')
    selected.value = data.novels.find(row => row.name === saved)?.name || data.novels[0]?.name || ''
  }
  return data.job
}

async function loadBook() {
  const name = selected.value
  const version = ++bookRequest
  if (!name) { book.value = null; return }
  busy.value = true
  sectionError.value = ''
  try {
    const data = await api('/book?' + params(name))
    if (version !== bookRequest) return
    const previous = book.value
    if (!previous || JSON.stringify(styleForm.value) === JSON.stringify(styleFields(previous.style)) ||
      JSON.stringify(styleForm.value) === JSON.stringify(styleFields(data.style))) {
      styleForm.value = styleFields(data.style)
    }
    for (const key of Object.keys(data.intent)) {
      if (intentDrafts.value[key] === previous?.intent[key] || intentDrafts.value[key] === data.intent[key]) {
        delete intentDrafts.value[key]
      }
    }
    book.value = data
    readAt.value = new Date().toLocaleTimeString('zh-CN')
    const current = data.chapters.find(row => row.chapter_num === chapter.value?.chapter_num)
    if (view.value === 'chapters') await openChapter((current || data.chapters.at(-1))?.chapter_num)
  } catch (error) {
    if (version === bookRequest) sectionError.value = error.message
  } finally {
    if (version === bookRequest) busy.value = false
  }
}

async function openChapter(num) {
  const version = ++chapterRequest
  if (!num) { chapter.value = null; return }
  try {
    const data = await api('/chapter/' + num + '?' + params(selected.value))
    if (version === chapterRequest) chapter.value = data
  } catch (error) {
    if (version === chapterRequest) sectionError.value = error.message
  }
}

async function loadModels() {
  const version = ++modelRequest
  modelBusy.value = true
  try {
    const data = await api('/models')
    if (version !== modelRequest) return
    modelForm.value = Object.fromEntries(Object.entries(data.roles).map(([key, role]) => {
      const draft = modelForm.value[key]
      const dirty = draft && draft !== models.value?.roles[key]?.current
      return [key, dirty && role.options.some(item => item.id === draft) ? draft : role.current]
    }))
    models.value = data
  } catch (error) {
    if (version === modelRequest && view.value === 'models') sectionError.value = error.message
  } finally {
    if (version === modelRequest) modelBusy.value = false
  }
}

async function loadCheckpoint(num = checkpointNumber.value) {
  const version = ++checkpointRequest
  checkpointNumber.value = num
  checkpoint.value = null
  checkpointLoaded.value = null
  if (!num || !selected.value) return
  try {
    const data = await api('/checkpoint/' + num + '?' + params(selected.value))
    if (version === checkpointRequest) {
      checkpoint.value = data
      checkpointLoaded.value = num
    }
  } catch (error) {
    if (version === checkpointRequest) sectionError.value = error.message
  }
}

function refreshBook() {
  loadBook()
  if (view.value === 'checkpoints') loadCheckpoint()
}

watch(selected, name => {
  ++bookRequest
  localStorage.setItem('novel-workbench-book', name)
  book.value = chapter.value = null
  intentDrafts.value = {}
  readAt.value = ''
  ++chapterRequest
  ++checkpointRequest
  busy.value = false
  checkpoint.value = null
  checkpointLoaded.value = null
  query.value = filter.value = ''
})
watch([view, selected], ([page]) => {
  query.value = filter.value = sectionError.value = ''
  if (page === 'models') {
    loadModels()
  } else {
    refreshBook()
  }
})

function receive(next) {
  if (next.kind === 'models') {
    if (modelJob.value?.id !== next.id) modelLog.value = []
    modelLog.value = [...modelLog.value, ...next.log].slice(-12)
    modelJob.value = next
  }
  if (next.id !== job.value?.id) {
    jobLog.value = []
    jobError.value = ''
  }
  if (next.prompt?.id !== job.value?.prompt?.id || next.id !== job.value?.id) {
    answer.value = ''
    sendingAnswer.value = false
  }
  jobLog.value = [...jobLog.value, ...next.log].slice(-350)
  job.value = next
}

async function start(kind, fields = {}, target = selected.value) {
  if (starting.value || activeJob.value) return
  starting.value = true
  startingKind.value = kind
  sectionError.value = notice.value = ''
  try {
    const next = await api('/jobs', { ...fields, kind, novel: target })
    dialog.value = null
    receive(next)
    jobOpen.value = true
    if (!activeJob.value) await finished()
  } catch (error) {
    if (dialog.value) dialog.value.error = error.message
    else sectionError.value = error.message
  } finally {
    starting.value = false
    startingKind.value = ''
  }
}

async function finished() {
  const result = job.value.result
  notice.value = result?.message || ''
  if (job.value.kind === 'models') {
    if (view.value === 'models') await loadModels()
    return
  }
  try {
    await loadLibrary()
    if (result?.novel) {
      selected.value = result.novel
      view.value = 'chapters'
    }
    if (selected.value) {
      await loadBook()
      if (view.value === 'chapters' && result?.chapter && job.value.novel === selected.value) {
        await openChapter(result.chapter)
      }
      if (view.value === 'checkpoints' && checkpointLoaded.value) await loadCheckpoint()
    }
    if (view.value === 'models') await loadModels()
  } catch (error) { sectionError.value = error.message }
}

async function poll() {
  try {
    if (activeJob.value) {
      const next = await api('/jobs/' + job.value.id + '?after=' + job.value.sequence)
      receive(next)
      if (!activeJob.value) await finished()
    } else if (!starting.value) {
      const current = await api('/jobs/current?known=' + (job.value?.id || ''))
      if (current.job && !starting.value && current.job.id !== job.value?.id) {
        receive(current.job)
        if (activeJob.value) jobOpen.value = true
        else await finished()
      }
    }
  } catch (error) {
    if (error.status === 404 && activeJob.value) {
      job.value = null
      await loadLibrary().catch(() => {})
    }
    sectionError.value = error.message
  }
  timer = window.setTimeout(poll, activeJob.value ? 700 : 1500)
}

async function respond(value) {
  if (!job.value?.prompt || submittingAnswer.value) return
  sendingAnswer.value = job.value.prompt.id
  jobError.value = ''
  try {
    await api('/jobs/' + job.value.id + '/answer', { prompt_id: job.value.prompt.id, value })
  } catch (error) {
    jobError.value = error.message
    sendingAnswer.value = false
  }
}

async function stop() {
  try { await api('/jobs/' + job.value.id + '/cancel', {}) }
  catch (error) { jobError.value = error.message }
}

function form(kind, title, fields = {}) {
  dialog.value = { kind, title, fields, error: '' }
}

function editForeshadow(row = {}) {
  form('foreshadow', row.fid ? '编辑大纲伏笔' : '新增大纲伏笔', {
    description: '', category: '情节伏笔', status: 'planned', plant_chapter: 1,
    resolve_chapter: 2, importance: 3, notes: '', ...row,
    importance: row.importance || 3, notes: row.notes || '',
  })
}

function confirmCheckpoint(kind, title, message) {
  if (checkpointLoaded.value !== checkpointNumber.value) return
  form(kind, title, { chapter: checkpointNumber.value, confirm: '', message })
}

async function readFile(event, key) {
  const file = event.target.files[0]
  if (!file) return
  const current = dialog.value
  try {
    if (file.size > 3 * 1024 * 1024) throw Error('请选择 3 MB 以内的文本文件')
    const text = new TextDecoder('utf-8', { fatal: true }).decode(await file.arrayBuffer())
    if (dialog.value === current) current.fields[key] = text
  } catch {
    if (dialog.value === current) current.error = '请使用 3 MB 以内的 UTF-8 文本文件'
  }
}

function submit() {
  const { kind, fields } = dialog.value
  start(kind, fields, kind === 'create' ? fields.name.trim() : selected.value)
}

onMounted(async () => {
  try {
    const initialJob = await loadLibrary()
    if (initialJob) { receive(initialJob); jobOpen.value = activeJob.value }
  } catch (error) { sectionError.value = error.message }
  poll()
})
onBeforeUnmount(() => window.clearTimeout(timer))
</script>

<template>
  <div class="shell">
    <aside class="sidebar">
      <div class="brand"><span class="brand-icon">文</span><span>小说工作台<small>本地创作空间</small></span></div>
      <div class="library">
        <label for="book-select">小说书库</label>
        <select id="book-select" v-model="selected">
          <option value="" disabled>选择小说</option>
          <option v-for="item in novels" :key="item.name" :value="item.name">{{ item.name }}</option>
        </select>
        <div class="library-actions">
          <button @click="form('create', '新建小说', { name: '', genre: '', keywords: '' })">＋ 新建</button>
          <button @click="form('import', '导入小说', { content: '' })">↑ 导入</button>
        </div>
      </div>
      <nav aria-label="工作台导航">
        <button v-for="[key, title, icon] in sections" :key="key" :class="{ selected: view === key }"
          :aria-current="view === key ? 'page' : undefined" @click="view = key">
          <span class="nav-icon" aria-hidden="true">{{ icon }}</span>{{ title }}
        </button>
      </nav>
      <div class="sidebar-foot">
        <button v-if="selected" class="quiet danger-text" :disabled="activeJob"
          @click="form('delete_book', '删除小说', { name_confirmation: '', confirm: '' })">删除当前小说</button>
        <span class="local"><i></i> 本机运行</span>
      </div>
    </aside>

    <main>
      <header class="topbar">
        <span class="crumb">工作台 <span>/</span> {{ sections.find(item => item[0] === view)[1] }}</span>
        <button v-if="job" class="job-link" @click="jobOpen = !jobOpen">
          <i :class="{ live: activeJob }"></i>{{ activeJob ? stateLabels[job.status] : '最近操作' }}
        </button>
      </header>

      <section class="workspace">
        <div v-if="sectionError" class="feedback error" role="alert">{{ sectionError }} <button @click="sectionError = ''">关闭</button></div>
        <div v-else-if="notice && (view !== 'models' || job?.kind !== 'models')" class="feedback" role="status">{{ notice }} <button @click="notice = ''">关闭</button></div>

        <template v-if="view === 'models'">
          <div class="heading"><div><p class="kicker">会话配置</p><h1>模型与费用</h1></div><button :disabled="modelBusy" @click="loadModels">{{ modelBusy ? '读取中…' : '刷新' }}</button></div>
          <div v-if="models" class="stack">
            <div class="metrics">
              <div><span>本次费用</span><strong>{{ money(models.usage.total_cost_yuan) }}</strong></div>
              <div><span>成功调用</span><strong>{{ size(models.usage.total_calls) }}</strong></div>
              <div><span>输入 / 输出 Token</span><strong>{{ size(models.usage.total_input_tokens) }} / {{ size(models.usage.total_output_tokens) }}</strong></div>
            </div>
            <form class="panel padded stack" @submit.prevent="start('models', modelForm)">
              <h2>角色模型</h2>
              <div class="field-grid">
                <label v-for="(role, key) in models.roles" :key="key">{{ role.label }}
                  <select v-model="modelForm[key]" :disabled="verifying || modelBusy">
                    <option v-for="option in role.options" :key="option.id" :value="option.id" :disabled="!option.configured && option.id !== role.current">
                      {{ option.name }}{{ option.configured ? '' : '（缺少密钥）' }}
                    </option>
                  </select>
                </label>
              </div>
              <div><button class="primary" :disabled="activeJob || starting || modelBusy">{{ verifying ? '验证中…' : '验证并保存' }}</button></div>
              <p v-if="verifying" class="muted" role="status">{{ modelLog.at(-1)?.text || '正在验证所选角色模型，等待服务商响应。' }}</p>
              <p v-if="modelJob?.error" class="error" role="alert">{{ modelJob.error }}</p>
              <div v-if="!verifying && modelJob?.result?.verification" class="stack">
                <p role="status">{{ modelJob.result.message }}</p>
                <div class="table-wrap"><table>
                  <thead><tr><th>服务商与模型</th><th>使用角色</th><th>验证结果</th><th>耗时</th><th>回复或原因</th></tr></thead>
                  <tbody><tr v-for="item in modelJob.result.verification" :key="item.provider + '/' + item.model">
                    <td>{{ providerName(item.provider) }} · {{ item.model }}</td><td>{{ item.roles.join('、') }}</td>
                    <td><span class="badge" :class="{ good: item.status === 'passed' }">{{ verifyStatus[item.status] }}</span></td>
                    <td>{{ item.elapsed_ms == null ? '—' : item.elapsed_ms + ' 毫秒' }}</td><td>{{ item.detail }}</td>
                  </tr></tbody>
                </table></div>
              </div>
            </form>
            <div class="two-up">
              <div class="panel padded stack">
                <h2>模型费用明细</h2>
                <div class="table-wrap"><table><thead><tr><th>模型</th><th>成功调用</th><th>费用</th></tr></thead><tbody>
                  <tr v-for="(stats, name) in models.usage.by_model" :key="name"><td>{{ name }}</td><td>{{ stats.calls }}</td><td>{{ money(stats.cost_yuan) }}</td></tr>
                </tbody></table></div>
                <p v-if="!Object.keys(models.usage.by_model || {}).length" class="muted">本次会话暂无模型调用。</p>
              </div>
              <div class="panel padded stack"><h2>失败统计与切换记录</h2>
                <div class="row small muted">写作 {{ models.failures.author_failures }} · 审稿 {{ models.failures.reviewer_failures }} · 读者 {{ models.failures.reader_reviewer_failures }}</div>
                <div v-for="item in models.history.slice().reverse()" :key="item.type + item.timestamp" class="event">
                  <span>{{ models.roles[item.type]?.label || item.type }} · {{ new Date(item.timestamp * 1000).toLocaleString('zh-CN') }}</span>
                  <strong>{{ item.old_model }} → {{ item.new_model }}</strong>
                </div>
                <p v-if="!models.history.length" class="muted">暂无切换记录。</p>
              </div>
            </div>
          </div>
          <p v-else-if="modelBusy" class="empty">正在读取模型信息…</p>
        </template>

        <template v-else-if="book">
          <p v-if="book.legacy" class="feedback">旧版小说库已按只读方式打开。首次执行需要更新数据库的操作时，工作台会请你确认。</p>
          <div class="heading">
            <div><p class="kicker">{{ novels.find(item => item.name === selected)?.genre || '当前小说' }}</p><h1>{{ selected }}</h1></div>
            <div class="actions">
              <span v-if="readAt" class="small muted" aria-live="polite">读取于 {{ readAt }}</span>
              <button :disabled="busy" @click="refreshBook">{{ busy ? '读取中…' : '刷新' }}</button>
              <template v-if="view === 'chapters'">
              <button :disabled="activeJob || starting" @click="form('generate', '批量生成章节', { count: 3, confirm: '' })">批量生成</button>
              <button class="primary" :disabled="activeJob || starting" @click="start('generate', { count: 1 })">＋ 生成下一章</button>
              </template>
            </div>
          </div>

          <template v-if="view === 'chapters'">
            <div class="metrics">
              <div><span>审核通过 / 目标</span><strong>{{ approved }} <small>/ {{ book.target }}</small></strong><div class="progress"><i :style="{ width: percent + '%' }"></i></div></div>
              <div><span>正文字数</span><strong>{{ size(book.progress.total_chars) }}</strong><span>草稿 {{ book.progress.draft }} 章</span></div>
              <div><span>待处理任务</span><strong>{{ book.progress.task_status['待处理'] }}</strong><span>共 {{ book.progress.task_count }} 张任务卡</span></div>
            </div>
            <details class="panel padded" style="margin-bottom: 1rem">
              <summary>审稿趋势与任务状态</summary>
              <p v-if="book.progress.quality_count">
                最近 {{ book.progress.quality_count }} 章均分 {{ book.progress.avg_total.toFixed(1) }}/100 ·
                L1 {{ book.progress.avg_l1.toFixed(1) }}/45 · L2 {{ book.progress.avg_l2.toFixed(1) }}/25 ·
                L3 {{ book.progress.avg_l3.toFixed(1) }}/30 · 否决 {{ book.progress.veto_hits }} 章
              </p>
              <p v-else class="muted">最近 {{ book.progress.review_window }} 章暂无审稿评分。</p>
              <p>进行中 {{ book.progress.task_status['进行中'] }} · 已完成 {{ book.progress.task_status['已完成'] }} ·
                审稿失败 {{ book.progress.task_status['审稿失败'] }} · 未兑现伏笔 {{ book.progress.foreshadow_count }}</p>
            </details>
            <div class="chapter-grid">
              <div class="panel chapter-list">
                <div class="panel-head"><h2>章节目录</h2><span class="count">{{ book.chapters.length }}</span></div>
                <div class="chapter-filters">
                  <input v-model="query" type="search" placeholder="搜索章节" aria-label="搜索章节">
                  <select v-model="filter" aria-label="筛选章节状态"><option value="">全部状态</option><option v-for="s in chapterStatuses" :key="s" :value="s">{{ statusText(s) }}</option></select>
                </div>
                <div class="chapter-scroll">
                  <button v-for="row in filteredChapters" :key="row.chapter_num" class="chapter-item"
                    :class="{ current: chapter?.chapter_num === row.chapter_num }" @click="openChapter(row.chapter_num)">
                    <span class="chapter-num">{{ String(row.chapter_num).padStart(2, '0') }}</span>
                    <span class="chapter-summary"><b>{{ row.title || '第' + row.chapter_num + '章' }}</b>
                      <small>{{ statusText(row.status) }} · {{ size(row.chars) }} 字</small>
                    </span>
                  </button>
                  <p v-if="!filteredChapters.length" class="empty">没有符合条件的章节。</p>
                </div>
                <div class="list-footer"><button :disabled="activeJob || !approved" @click="start('export')">导出已审核章节</button>
                  <button class="danger-text" :disabled="activeJob || !book.chapters.length"
                    @click="form('delete_chapters', '批量删除章节', { first: 1, last: 1, confirm: '' })">批量删除</button></div>
              </div>
              <article v-if="chapter" class="panel chapter-paper">
                <div class="paper-top">
                  <div><p class="kicker">第 {{ chapter.chapter_num }} 章 <span class="badge" :class="{ good: approval.includes(chapter.status) }">{{ statusText(chapter.status) }}</span></p>
                    <h2>{{ chapter.title || '未命名章节' }}</h2></div>
                  <div class="actions">
                    <button v-if="chapter.status === '审稿失败'" class="primary" :disabled="activeJob"
                      @click="form('recover', '恢复审稿失败', { chapter: chapter.chapter_num, method: '1' })">恢复审稿</button>
                    <button :disabled="activeJob || !chapter.content" @click="start('export', { chapter: chapter.chapter_num })">导出 TXT</button>
                    <button class="danger-text" :disabled="activeJob"
                      @click="form('delete_chapters', '删除本章', { first: chapter.chapter_num, last: chapter.chapter_num, confirm: '', single: true })">删除</button>
                  </div>
                </div>
                <div class="paper-meta">
                  <span>{{ chapter.emotion_tag || '情绪未设置' }}</span><span>编辑评分 {{ chapter.review_score_total ?? '—' }}</span><span>读者评分 {{ chapter.reader_review_score ?? '—' }}</span>
                  <span>重试 {{ chapter.retry_count || 0 }} 次</span>
                </div>
                <details v-if="chapter.plot_goal || chapter.summary || chapter.review_veto_items || chapter.reader_review_issues" class="chapter-details">
                  <summary>任务目标、摘要与审稿意见</summary>
                  <div v-if="chapter.plot_goal"><b>任务目标</b><p>{{ chapter.plot_goal }}</p></div>
                  <div v-if="chapter.summary"><b>章节摘要</b><p>{{ chapter.summary }}</p></div>
                  <div v-if="chapter.review_veto_items"><b>编辑审稿意见</b><p>{{ chapter.review_veto_items }}</p></div>
                  <div v-if="chapter.reader_review_issues"><b>读者审稿意见</b><p>{{ chapter.reader_review_issues }}</p></div>
                </details>
                <div class="prose">{{ chapter.content || '本章暂无正文。' }}</div>
              </article>
              <article v-else class="panel blank-paper"><span aria-hidden="true">文</span><h2>从下一章开始</h2><p>生成后，正文和审稿结果会出现在这里。</p></article>
            </div>
          </template>

          <div v-else-if="view === 'tasks'" class="panel">
            <div class="panel-head"><h2>章节任务卡 <span class="count">{{ book.tasks.length }}</span></h2><input v-model="query" type="search" placeholder="搜索章节或情节" aria-label="搜索任务卡"></div>
            <div class="table-wrap"><table><thead><tr><th>章节</th><th>情节目标</th><th>情绪</th><th>状态</th><th>操作</th></tr></thead>
              <tbody><tr v-for="row in filteredTasks" :key="row.chapter_num">
                <td>第{{ row.chapter_num }}章</td><td class="wide-cell">{{ row.plot_goal || '—' }}<div v-if="row.rewrite_count" class="muted small">已重写 {{ row.rewrite_count }} 次</div></td><td>{{ row.emotion_tag || '—' }}</td>
                <td><span class="badge">{{ statusText(row.status) }}</span></td>
                <td><button :disabled="activeJob || row.status === '进行中'"
                  @click="form('task', '编辑第' + row.chapter_num + '章任务卡', { chapter: row.chapter_num, plot_goal: row.plot_goal || '', emotion_tag: row.emotion_tag || '铺垫', original: row.original_plot_goal })">编辑</button></td>
              </tr></tbody></table></div>
            <p v-if="!filteredTasks.length" class="empty">暂无符合条件的任务卡。</p>
          </div>

          <div v-else-if="view === 'foreshadow'" class="stack">
            <div class="panel">
              <div class="panel-head"><h2>大纲伏笔 <span class="count">{{ book.foreshadow.length }}</span></h2>
                <div class="actions"><button :disabled="activeJob" @click="start('suggest')">生成伏笔建议</button><button class="primary" :disabled="activeJob" @click="editForeshadow()">＋ 新增伏笔</button></div>
              </div>
              <div class="table-wrap"><table><thead><tr><th>编号</th><th>描述</th><th>分类</th><th>埋入 / 兑现</th><th>状态</th><th>操作</th></tr></thead>
                <tbody><tr v-for="row in book.foreshadow" :key="row.fid">
                  <td>{{ row.fid }}</td><td class="wide-cell">{{ row.description }}</td><td>{{ row.category }}</td>
                  <td>{{ row.plant_chapter || '—' }} / {{ row.resolve_chapter || '—' }}</td><td><span class="badge">{{ statusText(row.status) }}</span></td>
                  <td><div class="actions"><button :disabled="activeJob" @click="editForeshadow(row)">编辑</button>
                    <button class="danger-text" :disabled="activeJob" @click="form('delete_foreshadow', '删除伏笔 ' + row.fid, { fid: row.fid, confirm: '' })">删除</button></div></td>
                </tr></tbody></table></div>
              <p v-if="!book.foreshadow.length" class="empty">暂无大纲伏笔。</p>
            </div>
            <div class="panel padded stack"><h2>伏笔健康度</h2>
              <template v-if="book.health">
                <p class="muted">未兑现 {{ book.health.total_active }} · 近期新增 {{ book.health.recent_added }} · 近期兑现 {{ book.health.recent_redeemed }} · 每章净增 {{ book.health.trend }}</p>
                <div class="field-grid"><div v-for="[key, title] in healthGroups" :key="key">
                  <h3>{{ title }} <span class="count">{{ book.health[key].length }}</span></h3>
                  <p v-for="item in book.health[key]" :key="item.fid" class="event"><strong>{{ item.description }}</strong><span>{{ item.fid }} · 第{{ item.plant_chapter }}章埋入 · 计划 {{ item.expected_redeem }}<template v-if="item.age"> · 已沉睡 {{ item.age }} 章</template></span></p>
                  <p v-if="!book.health[key].length" class="muted">暂无</p>
                </div></div>
              </template>
              <p v-else class="muted">写作后可查看伏笔健康度。</p>
            </div>
          </div>

          <div v-else-if="view === 'writing'" class="two-up">
            <form class="panel padded stack" @submit.prevent="start('intent', { field: intentKey, content: intentText })">
              <h2>创作意图</h2>
              <label>资料<select v-model="intentKey"><option value="intent">长期创作意图</option><option value="focus">当前焦点</option></select></label>
              <label>正文 <textarea v-model="intentText" rows="14" placeholder="支持 Markdown"></textarea></label>
              <div><button class="primary" :disabled="activeJob || starting">保存资料</button></div>
            </form>
            <div class="panel padded stack"><h2>写作风格</h2>
              <form class="stack" @submit.prevent="start('style', styleForm)">
                <label>风格<select v-model="styleForm.style"><option v-for="(style, key) in book.styles" :key="key" :value="key">{{ style.name }}</option><option value="custom">自定义</option></select></label>
                <p v-if="book.styles[styleForm.style]" class="muted">{{ book.styles[styleForm.style].desc }}</p>
                <label v-if="styleForm.style === 'custom'">风格描述 <textarea v-model="styleForm.custom" required rows="5"></textarea></label>
                <div><button class="primary" :disabled="activeJob || starting">保存风格</button></div>
              </form>
              <div class="rule"></div><h3>文法指纹</h3>
              <p class="muted">上传或粘贴至少 200 字参考文本，生成写作时使用的风格指纹。</p>
              <div><button :disabled="activeJob" @click="form('fingerprint', '生成文法指纹', { content: '' })">添加参考文本</button></div>
            </div>
          </div>

          <div v-else-if="view === 'checkpoints'" class="stack">
            <div class="panel padded stack">
              <h2>异常任务 <span class="count">{{ staleTasks.length }}</span></h2>
              <div v-for="row in staleTasks" :key="row.chapter_num" class="event row between"><span>第{{ row.chapter_num }}章 · {{ row.updated_at }}</span>
                <button @click="loadCheckpoint(row.chapter_num)">检查断点</button></div>
              <p v-if="!staleTasks.length" class="muted">没有超过 30 分钟的进行中任务。</p>
            </div>
            <div class="panel padded stack">
              <form class="row align-end" @submit.prevent="loadCheckpoint()">
                <label>章节号<input v-model.number="checkpointNumber" type="number" min="1" max="1000000" required></label><button>查看断点</button>
              </form>
              <template v-if="checkpoint && checkpointLoaded === checkpointNumber">
                <div class="actions"><button v-for="[kind, title, message] in checkpointActions" :key="kind" :disabled="activeJob"
                  @click="confirmCheckpoint(kind, title, message)">{{ title }}</button></div>
                <h3>写作历史</h3>
                <div v-for="row in checkpoint.history" :key="row.attempt" class="event">
                  <strong>第 {{ row.attempt }} 次 · {{ row.end_reason || '未结束' }} · {{ row.word_count ?? '—' }} 字 · {{ row.review_score ?? '—' }} 分</strong>
                  <span>{{ row.started_at }} → {{ row.ended_at || '—' }}</span>
                </div>
                <p v-if="!checkpoint.history.length" class="muted">该章节暂无历史。</p>
                <h3>节拍缓存</h3><p class="muted">{{ checkpoint.cache?.created_at || '暂无缓存' }}</p>
                <div v-if="checkpoint.cache" class="text-preview">{{ checkpoint.cache.preview }}</div>
              </template>
            </div>
          </div>
        </template>

        <p v-else-if="busy" class="empty">正在读取小说…</p>
        <div v-else class="panel blank-paper"><span aria-hidden="true">文</span><h1>打开一本小说</h1><p>从书库选择，或开始一本新书。</p>
          <button class="primary" @click="form('create', '新建小说', { name: '', genre: '', keywords: '' })">＋ 新建小说</button></div>
      </section>
    </main>

    <div v-if="dialog" class="overlay" @click.self="dialog = null" @keydown.esc="dialog = null">
      <form class="dialog panel stack" role="dialog" aria-modal="true" aria-labelledby="dialog-title" @submit.prevent="submit">
        <header class="row between"><h2 id="dialog-title">{{ dialog.title }}</h2><button type="button" class="quiet" @click="dialog = null" aria-label="关闭">×</button></header>
        <template v-if="dialog.kind === 'create'">
          <label>书名<input v-model="dialog.fields.name" maxlength="64" required autofocus></label>
          <label>类型<input v-model="dialog.fields.genre" placeholder="例如：都市悬疑" maxlength="100"></label>
          <label>关键词与基本设定<textarea v-model="dialog.fields.keywords" rows="4" maxlength="3000"></textarea></label>
        </template>
        <template v-else-if="['import', 'fingerprint'].includes(dialog.kind)">
          <label>上传 UTF-8 文本<input type="file" accept=".txt,text/plain" @change="readFile($event, 'content')"></label>
          <label>文本内容<textarea v-model="dialog.fields.content" rows="8" :required="dialog.kind === 'fingerprint'"
            :placeholder="dialog.kind === 'import' ? '留空时读取项目根目录中的 newbook.txt' : '至少 200 字'"></textarea></label>
          <a v-if="dialog.kind === 'import'" href="/api/template" download>下载导入模板</a>
        </template>
        <template v-else-if="dialog.kind === 'generate'">
          <label>生成章数<input v-model.number="dialog.fields.count" type="number" min="1" max="1000" required></label>
          <label v-if="dialog.fields.count > 50">超过 50 章，输入 YES 确认<input v-model="dialog.fields.confirm" required pattern="YES"></label>
        </template>
        <template v-else-if="dialog.kind === 'recover'">
          <p class="muted">处理第 {{ dialog.fields.chapter }} 章</p>
          <label>恢复方式<select v-model="dialog.fields.method"><option value="1">仅重试审稿</option><option value="2">重写并重审</option><option value="3">强制通过并导出</option></select></label>
        </template>
        <template v-else-if="dialog.kind === 'task'">
          <p v-if="dialog.fields.original" class="muted">原始目标 {{ dialog.fields.original }}</p>
          <label>情节目标<textarea v-model="dialog.fields.plot_goal" rows="6"></textarea></label>
          <label>情绪标签<select v-model="dialog.fields.emotion_tag"><option v-for="tag in ['铺垫', '冲突', '爽点', '低谷', '反转']" :key="tag">{{ tag }}</option></select></label>
        </template>
        <template v-else-if="dialog.kind === 'foreshadow'">
          <label>伏笔描述<textarea v-model="dialog.fields.description" rows="3" required></textarea></label>
          <div class="field-grid"><label>分类<select v-model="dialog.fields.category"><option v-for="item in book.categories" :key="item">{{ item }}</option></select></label>
            <label v-if="dialog.fields.fid">状态<select v-model="dialog.fields.status"><option v-for="item in ['planned', 'planted', 'resolved']" :key="item" :value="item">{{ labels[item] }}</option></select></label>
            <label>重要度<input v-model.number="dialog.fields.importance" type="number" min="1" max="5" required></label></div>
          <div class="field-grid"><label>计划埋入章<input v-model.number="dialog.fields.plant_chapter" type="number" :min="dialog.fields.fid ? 0 : 1" :required="!dialog.fields.fid"></label>
            <label>计划兑现章<input v-model.number="dialog.fields.resolve_chapter" type="number" :min="dialog.fields.fid ? 0 : 1" :required="!dialog.fields.fid"></label></div>
          <label>备注<textarea v-model="dialog.fields.notes" rows="2"></textarea></label>
        </template>
        <template v-else>
          <p v-if="dialog.kind === 'delete_book'">将删除《{{ selected }}》及其导出文件。请先核对书名。</p>
          <label v-if="dialog.kind === 'delete_book'">输入完整书名<input v-model="dialog.fields.name_confirmation" required></label>
          <div v-if="dialog.kind === 'delete_chapters' && !dialog.fields.single" class="field-grid">
            <label>起始章节<input v-model.number="dialog.fields.first" type="number" min="1" required></label>
            <label>结束章节<input v-model.number="dialog.fields.last" type="number" :min="dialog.fields.first || 1" required></label>
          </div>
          <p v-if="dialog.kind === 'delete_chapters'" class="muted">正文、摘要和导出文件将删除，任务卡恢复为待处理。已提取伏笔保留。</p>
          <p v-if="dialog.fields.message">第 {{ dialog.fields.chapter }} 章。{{ dialog.fields.message }}</p>
          <label>输入 YES 确认<input v-model="dialog.fields.confirm" required pattern="YES"></label>
        </template>
        <p v-if="dialog.error" class="error" role="alert">{{ dialog.error }}</p>
        <footer class="actions"><button type="button" @click="dialog = null">取消</button>
          <button :class="dialog.kind.startsWith('delete') ? 'danger' : 'primary'" :disabled="activeJob || starting">确认</button></footer>
      </form>
    </div>

    <section v-if="job && jobOpen" class="panel job-panel" aria-label="任务进度">
      <header class="row between">
        <div><h2>{{ job.title }}</h2><span class="small muted" aria-live="polite">{{ stateLabels[job.status] }}{{ job.novel ? ' · ' + job.novel : '' }}</span></div>
        <div class="actions"><button v-if="activeJob" :disabled="job.status === 'stopping'" @click="stop">停止任务</button><button class="quiet" @click="jobOpen = false" aria-label="收起任务进度">×</button></div>
      </header>
      <div v-if="job.prompt" class="prompt stack">
        <h3>{{ job.prompt.text }}</h3>
        <details v-if="job.prompt.context" open><summary>当前步骤与预览</summary><div class="text-preview">{{ job.prompt.context }}</div></details>
        <div v-if="job.prompt.options.length" class="actions prompt-actions">
          <button v-for="[value, label] in job.prompt.options" :key="value" :disabled="submittingAnswer" @click="respond(value)">{{ label }}</button>
        </div>
        <form v-else class="stack" @submit.prevent="respond(answer)">
          <textarea v-if="job.prompt.multiline" v-model="answer" rows="5" aria-label="填写任务所需内容"></textarea>
          <input v-else v-model="answer" aria-label="填写任务所需内容">
          <div><button class="primary" :disabled="submittingAnswer">提交</button></div>
        </form>
      </div>
      <p v-if="job.error || jobError" class="error" role="alert">{{ job.error || jobError }}</p>
      <p v-if="job.result?.message" role="status">{{ job.result.message }}</p>
      <div v-if="job.result?.downloads?.length" class="download-links">
        <a v-for="name in job.result.downloads" :key="name" :href="download(name, job.novel)" download>{{ name }}</a>
      </div>
      <details v-if="jobLog.length" :open="!job.prompt"><summary>任务记录</summary>
        <div class="log"><p v-for="row in jobLog.slice(-60)" :key="row.id">{{ row.text }}</p></div>
      </details>
    </section>
  </div>
</template>
