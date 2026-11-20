<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import { 批状态, 命运, 标 } from '../labels'

const periodId = ref('2026-10')
const batch = ref(null)
const vars = ref([])
const periods = ref([])
const msg = ref('')
const err = ref('')

const sealed = computed(() => !!batch.value && batch.value.status === 'sealed')
const stale = computed(() => !!batch.value && batch.value.stale)

function snapshotRows(b) {
  const snap = b && b.estimate_snapshot ? b.estimate_snapshot : {}
  return Object.entries(snap).map(([meter_id, v]) => ({ meter_id, ...v }))
}

async function loadPeriod() {
  periods.value = await api.periods()
  const bs = await api.batches(periodId.value)
  batch.value = bs[0] || null
  vars.value = batch.value ? await api.variances({ batch_id: batch.value.batch_id }) : []
}

async function align() {
  err.value = ''; msg.value = ''
  try {
    batch.value = await api.align(periodId.value)
    vars.value = await api.variances({ batch_id: batch.value.batch_id })
    msg.value = `对齐完成：匹配 ${batch.value.matched_count}，差额 ${batch.value.variance_count}，估抄修订 ${batch.value.revision_count} 次`
    await loadPeriod()
  } catch (e) { err.value = e.message }
}

async function fate(id, f) {
  err.value = ''
  try {
    await api.setFate(id, f)
    vars.value = await api.variances({ batch_id: batch.value.batch_id })
  } catch (e) { err.value = e.message }
}

onMounted(async () => {
  try { await loadPeriod() } catch (e) { err.value = e.message }
})
</script>
<template>
  <div>
    <h1>对齐差额</h1>
    <p class="sub">按表号与账期对齐「现行」估抄与实抄；修订后须重新对齐</p>
    <div class="row">
      <label>账期<input v-model="periodId" @change="loadPeriod" /></label>
      <button @click="align" :disabled="sealed">跑对齐</button>
      <button class="secondary" @click="loadPeriod">刷新</button>
    </div>
    <p class="msg ok" v-if="msg">{{ msg }}</p>
    <p class="msg err" v-if="err">{{ err }}</p>

    <div class="panel" v-if="batch">
      <p>
        批号 {{ batch.batch_id }} · 指纹 {{ batch.fingerprint }} ·
        <span class="tag" :class="batch.status">{{ 标(批状态, batch.status) }}</span>
        · 估抄修订 {{ batch.revision_count }} 次
      </p>
      <p class="msg warn" v-if="stale">
        该批对齐后估抄已被修订，批内估抄值为旧版；请重新对齐后再封印。
      </p>

      <h3>估抄版本快照（对齐时锁定）</h3>
      <table>
        <thead><tr><th>表号</th><th>估抄</th><th>版本</th><th>数量</th><th>修订次数</th></tr></thead>
        <tbody>
          <tr v-for="s in snapshotRows(batch)" :key="s.meter_id">
            <td>{{ s.meter_id }}</td>
            <td>{{ s.estimate_id }}</td>
            <td>第 {{ s.version_no }} 版</td>
            <td>{{ s.qty }}</td>
            <td>{{ s.revision_count }}</td>
          </tr>
        </tbody>
      </table>

      <h3 style="margin-top: 1rem">差额</h3>
      <table>
        <thead>
          <tr><th>差额编号</th><th>表号</th><th>估抄</th><th>实抄</th><th>差值</th>
            <th>估抄版本</th><th>命运</th><th>处置</th></tr>
        </thead>
        <tbody>
          <tr v-for="v in vars" :key="v.variance_id">
            <td>{{ v.variance_id }}</td>
            <td>{{ v.meter_id }}</td>
            <td>{{ v.estimate_qty }}</td>
            <td>{{ v.actual_qty }}</td>
            <td>{{ v.delta }}</td>
            <td>{{ v.estimate_version_no != null ? `第 ${v.estimate_version_no} 版` : '—' }}</td>
            <td>{{ 标(命运, v.fate) }}</td>
            <td>
              <template v-if="!sealed">
                <button class="secondary" @click="fate(v.variance_id, 'accept_short')">认短</button>
                <button class="secondary" @click="fate(v.variance_id, 'accept_over')">认溢</button>
                <button class="warn" @click="fate(v.variance_id, 'quarantine')">隔离</button>
              </template>
              <span v-else class="muted">已封印只读</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
