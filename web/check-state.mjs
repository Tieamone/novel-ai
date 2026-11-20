import fs from 'node:fs'
import vm from 'node:vm'
import assert from 'node:assert/strict'
import { ref, computed, watch, nextTick } from 'vue'
const source = fs.readFileSync(new URL('./src/App.vue', import.meta.url), 'utf8').split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/m, '')
function setup() {
  const requests = []
  const context = vm.createContext({ ref, computed, watch, onMounted() {}, onBeforeUnmount() {},
    localStorage: { getItem() {}, setItem() {} }, window: { setTimeout() {} }, URLSearchParams, TextDecoder,
    fetch(url, options) { return new Promise(resolve => requests.push({ url, options, done: data => resolve({ ok: true, json: async () => data }) })) },
  })
  const state = vm.runInContext(source + `\n;({ selected, chapter, checkpoint, checkpointNumber, book, view, dialog,
    styleForm, modelForm, openChapter, loadCheckpoint, loadBook, form, submit, receive, finished,
    activeJob, jobOpen, dialogReady, respond })`, context)
  return { state, requests }
}
{
  const { state: s, requests: r } = setup()
  s.chapter.value = { chapter_num: 1 }
  const first = s.openChapter(2)
  assert.equal(s.chapter.value, null, '切章时必须立即清除旧正文及操作')
  const second = s.openChapter(3)
  r[1].done({ chapter_num: 3 }); await second
  r[0].done({ chapter_num: 2 }); await first
  assert.equal(s.chapter.value.chapter_num, 3, '旧响应不能覆盖新章节')
}
{
  const { state: s, requests: r } = setup()
  s.selected.value = '甲'; await nextTick()
  r.length = 0
  const pending = s.loadCheckpoint(1)
  s.checkpointNumber.value = 2
  r[0].done({ history: ['旧章'] }); await pending
  assert.equal(s.checkpoint.value, null, '断点结果必须匹配当前输入')
}
{
  const { state: s, requests: r } = setup()
  s.selected.value = '甲'; await nextTick(); r.length = 0
  const reading = s.loadBook()
  const choosing = s.openChapter(3)
  r[1].done({ chapter_num: 3 }); await choosing
  r[0].done({ style: '1', intent: {}, chapters: [{ chapter_num: 1 }] }); await reading
  assert.equal(s.chapter.value.chapter_num, 3, '刷新不能覆盖读取期间的手动选择')
  assert.equal(r.length, 2)
  s.form('task', '编辑旧书', { chapter: 1 })
  s.styleForm.value = { style: 'custom', custom: '甲的草稿' }
  s.selected.value = '乙'; await nextTick()
  assert.equal(s.dialog.value, null, '切书关闭旧书弹窗')
  assert.equal(s.styleForm.value.custom, '', '切书不能保留旧书风格草稿')
}
{
  const { state: s, requests: r } = setup()
  const running = { id: 'old', status: 'running', kind: 'generate', log: [] }
  s.receive(running)
  s.form('create', '新建小说', { name: '表单草稿' })
  const waiting = { ...running, status: 'waiting', prompt: { id: 1 } }
  s.receive(waiting)
  assert.equal(s.dialogReady.value, true, '后台任务等待时仍可查看新建表单')
  assert.equal(s.jobOpen.value, false, '后台任务的新提示不会盖住正在填写的表单')
  assert.equal(s.activeJob.value, true)
  s.submit()
  assert.equal(r.length, 0, '等待确认期间禁止并发提交写入任务')
  const answer = s.respond('继续')
  assert.equal(r[0].url, '/api/jobs/old/answer')
  r[0].done({ ok: true }); await answer
  assert.equal(s.dialog.value.fields.name, '表单草稿', '处理当前任务后保留填写的书名')
}
{
  const { state: s, requests: r } = setup()
  s.form('delete_chapters', '批量删除章节', { range: '5-2', confirm: 'YES' })
  s.submit()
  assert.match(s.dialog.value.error, /连续范围/)
  assert.equal(r.length, 0, '倒序范围不提交')
  s.dialog.value.fields.range = '2-5'
  const submitting = s.submit()
  const payload = JSON.parse(r[0].options.body)
  assert.equal(payload.first, 2)
  assert.equal(payload.last, 5)
  const job = { id: 'delete', kind: 'delete_chapters', status: 'running', log: [] }
  r[0].done(job); await submitting
  assert.equal(s.jobOpen.value, true)
  assert.equal(s.dialogReady.value, false, '已提交的弹窗让出任务面板')
  s.receive({ ...job, status: 'failed', error: '选定范围内有章节不存在' })
  const finishing = s.finished()
  assert.equal(s.jobOpen.value, false)
  assert.equal(s.dialog.value.fields.range, '2-5')
  assert.match(s.dialog.value.error, /不存在/)
  r[1].done({ token: '', novels: [] }); await finishing
}
console.log('通过：切书和乱序响应、断点输入一致性、任务阻塞时保留表单、删章范围与失败重试')
