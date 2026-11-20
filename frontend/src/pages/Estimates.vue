<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import { 版本角色 } from '../labels'

const periodId = ref('2026-10')
const rows = ref([])
const periods = ref([])
const form = ref({ meter_id: 'M-W-101', period_id: '2026-10', qty: 10, doc_no: '' })
const msg = ref('')
const err = ref('')

// 修订
const revising = ref(null) // 正在修订的估抄行
const revForm = ref({ qty: 0, reason: '', revised_by: '' })
const revErr = ref('')

// 历史版本
const detail = ref(null)
const cmp = ref({ from_no: 1, to_no: 1, result: null, err: '' })

const periodStatus = computed(() => {
  const p = periods.value.find((x) => x.period_id === form.value.period_id)
  return p ? p.status : null
})
const listPeriodSealed = computed(() => {
  const p = periods.value.find((x) => x.period_id === periodId.value)
  return !!(p && p.status === 'sealed')
})

async function load() {
  err.value = ''
  try {
    ;[rows.value, periods.value] = await Promise.all([api.estimates(periodId.value), api.periods()])
  } catch (e) { err.value = e.message }
}

async function submit() {
  err.value = ''; msg.value = ''
  try {
    const body = { ...form.value, doc_no: form.value.doc_no || '' }
    if (body.doc_no === '') delete body.doc_no
    const r = await api.postEstimate(body)
    msg.value = `估抄已装载（${r.version_no} 版）`
    await load()
  } catch (e) { err.value = e.message }
}

function startRevise(r) {
  revising.value = r
  revForm.value = { qty: r.qty, reason: '', revised_by: '' }
  revErr.value = ''
}

async function submitRevise() {
  revErr.value = ''
  if (!revForm.value.reason.trim()) { revErr.value = '修订必须填写原因'; return }
  try {
    const r = await api.reviseEstimate(revising.value.estimate_id, {
      qty: revForm.value.qty,
      reason: revForm.value.reason.trim(),
      revised_by: revForm.value.revised_by || '操作员',
      expected_version_no: revising.value.version_no,
    })
    msg.value = `${revising.value.estimate_id} 已修订：${r.previous_qty} → ${r.qty}（第 ${r.version_no} 版，累计 ${r.revision_count} 次）`
    revising.value = null
    await load()
    if (detail.value && detail.value.estimate_id === r.estimate_id) await openDetail(r.estimate_id)
  } catch (e) { revErr.value = e.message }
}

async function openDetail(id) {
  cmp.value.result = null; cmp.value.err = ''
  try {
    detail.value = await api.estimateDetail(id)
    const vs = detail.value.versions
    cmp.value.from_no = vs.length > 1 ? vs[vs.length - 2].version_no : vs[0].version_no
    cmp.value.to_no = vs[vs.length - 1].version_no
  } catch (e) { err.value = e.message }
}

async function runCompare() {
  cmp.value.result = null; cmp.value.err = ''
  try {
    cmp.value.result = await api.estimateCompare(
      detail.value.estimate_id, cmp.value.from_no, cmp.value.to_no,
    )
  } catch (e) { cmp.value.err = e.message }
}

onMounted(load)
</script>
<template>
  <div>
    <h1>估抄</h1>
    <p class="sub">按账期装载估抄；修订插入新版本，列表只显示现行版本，历史可查</p>
    <div class="row">
      <label>账期筛选<input v-model="periodId" /></label>
      <button class="secondary" @click="load">刷新</button>
    </div>

    <div class="panel">
      <div class="row">
        <label>表号<input v-model="form.meter_id" /></label>
        <label>账期<input v-model="form.period_id" /></label>
        <label>数量<input type="number" step="0.1" v-model.number="form.qty" /></label>
        <label>单号<input v-model="form.doc_no" placeholder="可空自动生成" /></label>
        <button @click="submit" :disabled="periodStatus === 'sealed'">装载估抄</button>
      </div>
      <p class="msg warn" v-if="periodStatus === 'sealed'">账期 {{ form.period_id }} 已封印，估抄只读</p>
      <p class="msg ok" v-if="msg">{{ msg }}</p>
      <p class="msg err" v-if="err">{{ err }}</p>
      <table>
        <thead>
          <tr>
            <th>表号</th><th>账期</th><th>数量</th><th>单号</th>
            <th>版本</th><th>修订次数</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="r.estimate_id">
            <td>{{ r.meter_id }}</td>
            <td>{{ r.period_id }}</td>
            <td>{{ r.qty }}</td>
            <td>{{ r.doc_no }}</td>
            <td>第 {{ r.version_no }} 版<span class="tag cur">现行</span></td>
            <td>{{ r.revision_count }}</td>
            <td class="ops">
              <button class="secondary" @click="openDetail(r.estimate_id)">历史</button>
              <button
                v-if="!listPeriodSealed"
                class="warn"
                @click="startRevise(r)"
              >修订</button>
              <span v-else class="muted">已封印只读</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 修订入口 -->
    <div class="panel" v-if="revising">
      <h3>修订 {{ revising.estimate_id }} · 现行第 {{ revising.version_no }} 版</h3>
      <p class="sub">当前数量 {{ revising.qty }}；修订会保留旧版并插入新版本，对齐只吃新版本</p>
      <div class="row">
        <label>新数量<input type="number" step="0.1" v-model.number="revForm.qty" /></label>
        <label>修订原因<input v-model="revForm.reason" placeholder="必填" /></label>
        <label>修订人<input v-model="revForm.revised_by" placeholder="操作员" /></label>
        <button @click="submitRevise">提交修订</button>
        <button class="secondary" @click="revising = null">取消</button>
      </div>
      <p class="msg err" v-if="revErr">{{ revErr }}</p>
    </div>

    <!-- 历史版本链 -->
    <div class="panel" v-if="detail">
      <h3>
        {{ detail.estimate_id }} · {{ detail.meter_id }} · {{ detail.period_id }}
        共 {{ detail.revision_count }} 次修订
      </h3>
      <table>
        <thead>
          <tr><th>版本</th><th>数量</th><th>单号</th><th>生效时刻</th><th>状态</th><th>原因</th><th>修订人</th></tr>
        </thead>
        <tbody>
          <tr v-for="v in detail.versions" :key="v.version_id" :class="{ older: !v.is_current }">
            <td>第 {{ v.version_no }} 版</td>
            <td>{{ v.qty }}</td>
            <td>{{ v.doc_no }}</td>
            <td>{{ v.loaded_at }}</td>
            <td>
              <span class="tag" :class="v.is_current ? 'cur' : 'old'">{{ 版本角色(v) }}</span>
              <span v-if="v.superseded_at" class="muted">（{{ v.superseded_at }} 起失效）</span>
            </td>
            <td>{{ v.revision_reason || '—' }}</td>
            <td>{{ v.revised_by || '—' }}</td>
          </tr>
        </tbody>
      </table>

      <h3 style="margin-top: 1rem">版本对照</h3>
      <div class="row">
        <label>旧版
          <select v-model.number="cmp.from_no">
            <option v-for="v in detail.versions" :key="v.version_no" :value="v.version_no">第 {{ v.version_no }} 版</option>
          </select>
        </label>
        <label>新版
          <select v-model.number="cmp.to_no">
            <option v-for="v in detail.versions" :key="v.version_no" :value="v.version_no">第 {{ v.version_no }} 版</option>
          </select>
        </label>
        <button @click="runCompare">对照</button>
        <button class="secondary" @click="detail = null">关闭</button>
      </div>
      <p class="msg err" v-if="cmp.err">{{ cmp.err }}</p>
      <table v-if="cmp.result">
        <thead><tr><th>版本</th><th>数量</th><th>单号</th><th>生效/失效</th></tr></thead>
        <tbody>
          <tr>
            <td>第 {{ cmp.result.from.version_no }} 版</td>
            <td>{{ cmp.result.from.qty }}</td>
            <td>{{ cmp.result.from.doc_no }}</td>
            <td>{{ cmp.result.from.loaded_at }} / {{ cmp.result.from.superseded_at || '—' }}</td>
          </tr>
          <tr>
            <td>第 {{ cmp.result.to.version_no }} 版</td>
            <td>{{ cmp.result.to.qty }}</td>
            <td>{{ cmp.result.to.doc_no }}</td>
            <td>{{ cmp.result.to.loaded_at }} / {{ cmp.result.to.superseded_at || '—' }}</td>
          </tr>
        </tbody>
      </table>
      <p class="msg" v-if="cmp.result">数量差 <b>{{ cmp.result.delta }}</b>（新版 − 旧版）</p>
    </div>
  </div>
</template>
