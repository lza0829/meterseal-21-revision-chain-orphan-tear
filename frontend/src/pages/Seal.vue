<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'
import { 批状态, 账期状态, 标 } from '../labels'
const periods = ref([])
const batches = ref([])
const periodId = ref('2026-10')
const batchId = ref('')
const msg = ref('')
const err = ref('')
const selected = ref(null)

async function refresh() {
  ;[periods.value, batches.value] = await Promise.all([api.periods(), api.batches(periodId.value)])
  const open = batches.value.find(b => b.status === 'open')
  batchId.value = open?.batch_id || batches.value[0]?.batch_id || ''
  await showSnapshot()
}

async function showSnapshot() {
  selected.value = null
  if (!batchId.value) return
  try { selected.value = await api.batchSnapshot(batchId.value) } catch { /* 批可能已被新批作废 */ }
}

async function seal() {
  err.value = ''; msg.value = ''
  try {
    const r = await api.seal({ period_id: periodId.value, batch_id: batchId.value || null })
    msg.value = `已封印 ${r.period_id}，绑定批 ${r.batch_id}，快照内估抄修订 ${r.revision_count} 次；该期估抄/实抄/修订转为只读`
    await refresh()
  } catch (e) { err.value = e.message }
}
onMounted(refresh)
</script>
<template>
  <div>
    <h1>账期封印</h1>
    <p class="sub">封印后该期估抄（含修订）、实抄、对齐、命运处置一律禁止写入</p>
    <div class="row">
      <label>账期<input v-model="periodId" @change="refresh" /></label>
      <label>对账批<input v-model="batchId" @change="showSnapshot" placeholder="可空取最新开放批" /></label>
      <button class="warn" @click="seal">封印账期</button>
      <button class="secondary" @click="refresh">刷新</button>
    </div>
    <p class="msg ok" v-if="msg">{{ msg }}</p>
    <p class="msg err" v-if="err">{{ err }}</p>

    <div class="panel" v-if="selected">
      <h3>待封印快照 · {{ selected.batch_id }}</h3>
      <p>
        <span class="tag" :class="selected.status">{{ 标(批状态, selected.status) }}</span>
        · 匹配 {{ selected.matched_count }} · 差额 {{ selected.variance_count }}
        · 估抄修订 {{ selected.revision_count }} 次
      </p>
      <p class="msg warn" v-if="selected.stale_meters && selected.stale_meters.length">
        快照后有估抄被修订（{{ selected.stale_meters.map(s => s.meter_id).join('、') }}），
        封印将被拒绝，请先重新对齐。
      </p>
      <table>
        <thead><tr><th>表号</th><th>估抄</th><th>版本</th><th>数量</th><th>修订次数</th></tr></thead>
        <tbody>
          <tr v-for="(v, mid) in (selected.estimate_snapshot || {})" :key="mid">
            <td>{{ mid }}</td>
            <td>{{ v.estimate_id }}</td>
            <td>第 {{ v.version_no }} 版</td>
            <td>{{ v.qty }}</td>
            <td>{{ v.revision_count }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="panel">
      <h3>账期状态</h3>
      <table>
        <thead><tr><th>账期</th><th>状态</th><th>封印时刻</th><th>绑定批</th></tr></thead>
        <tbody>
          <tr v-for="p in periods" :key="p.period_id">
            <td>{{ p.period_id }}</td>
            <td><span class="tag" :class="p.status">{{ 标(账期状态, p.status) }}</span></td>
            <td>{{ p.sealed_at || '—' }}</td>
            <td>{{ p.batch_no || '—' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
