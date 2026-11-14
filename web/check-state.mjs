import fs from 'node:fs'
import vm from 'node:vm'
import assert from 'node:assert/strict'
import { ref, computed, watch, nextTick } from 'vue'
const source = fs.readFileSync(new URL('./src/App.vue', import.meta.url), 'utf8').split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/m, '')
function setup() {
  const requests = []
  const context = vm.createContext({ ref, computed, watch, onMounted() {}, onBeforeUnmount() {},
    localStorage: { getItem() {}, setItem() {} }, window: {}, URLSearchParams, TextDecoder,
    fetch(url, options) { return new Promise(resolve => requests.push({ url, options, done: data => resolve({ ok: true, json: async () => data }) })) },
  })
  const state = vm.runInContext(source + '\n;({ selected, chapter, checkpoint, checkpointNumber, book, view, dialog, styleForm, modelForm, openChapter, loadCheckpoint, loadBook, form, submit })', context)
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
console.log('通过：章节乱序响应、切章清空、刷新与选择竞争、断点输入一致性、切书弹窗与草稿隔离')
