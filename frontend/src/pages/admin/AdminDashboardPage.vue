<script setup>
import { computed, onMounted, ref } from 'vue'
import AppIcon from '../../components/AppIcon.vue'
import { adminFetch } from '../../adminApi.js'

const loading = ref(true)
const error = ref('')
const stats = ref(null)
const demandCounts = ref({})
const audit = ref([])

const index = computed(() => stats.value?.index || {})
const health = computed(() => stats.value?.analyzer?.health_overview || {})
const checker = computed(() => stats.value?.checker || {})
const harvester = computed(() => stats.value?.harvester || {})

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [s, d, a] = await Promise.all([
      adminFetch('/stats'),
      adminFetch('/admin/demands?page_size=1'),
      adminFetch('/admin/audit?limit=8'),
    ])
    stats.value = s
    demandCounts.value = d.counts || {}
    audit.value = a.items || []
  } catch (e) {
    error.value = e.message || '控制台数据加载失败'
  } finally {
    loading.value = false
  }
}

function formatTime(value) {
  if (!value) return '—'
  return String(value).substring(0, 16).replace('T', ' ')
}

onMounted(load)
</script>

<template>
  <div class="admin-dashboard">
    <div class="admin-page-head">
      <div>
        <h2>控制台</h2>
        <p>索引、巡检、需求与最近操作概览。</p>
      </div>
      <button class="btn sm" :disabled="loading" @click="load">刷新</button>
    </div>

    <div v-if="loading" class="loading"><span class="spinner"></span>加载中…</div>
    <div v-else-if="error" class="error-msg">{{ error }}</div>

    <template v-else>
      <div class="admin-stats">
        <div class="admin-stat">
          <span>索引总量</span>
          <strong>{{ index.total || 0 }}</strong>
        </div>
        <div class="admin-stat is-ok">
          <span>已检测</span>
          <strong>{{ index.checked || 0 }}</strong>
        </div>
        <div class="admin-stat is-bad">
          <span>确认失效</span>
          <strong>{{ index.dead || 0 }}</strong>
        </div>
        <div class="admin-stat">
          <span>未检测</span>
          <strong>{{ index.untested || 0 }}</strong>
        </div>
        <div class="admin-stat is-primary">
          <span>死链率（已检测）</span>
          <strong>{{ health.dead_rate || 0 }}%</strong>
        </div>
        <div class="admin-stat is-warn">
          <span>待处理需求</span>
          <strong>{{ demandCounts.pending || 0 }}</strong>
        </div>
      </div>

      <div class="admin-grid-2">
        <section class="admin-card">
          <h3>运行状态</h3>
          <div class="admin-kv">
            <span>巡检器</span>
            <b :class="checker.running ? 'text-ok' : 'text-bad'">
              {{ checker.running ? '运行中' : '已停止' }}
            </b>
          </div>
          <div class="admin-kv">
            <span>上次巡检</span>
            <b>{{ formatTime(checker.last_check_time) }}</b>
          </div>
          <div class="admin-kv">
            <span>收割器</span>
            <b :class="harvester.running ? 'text-ok' : 'text-bad'">
              {{ harvester.running ? '运行中' : '已停止' }}
            </b>
          </div>
          <div class="admin-kv">
            <span>累计收割</span>
            <b>{{ harvester.total_harvested || 0 }}</b>
          </div>
        </section>

        <section class="admin-card">
          <h3>需求概览</h3>
          <div class="admin-kv">
            <span>待处理</span>
            <b class="text-warn">{{ demandCounts.pending || 0 }}</b>
          </div>
          <div class="admin-kv">
            <span>已解决</span>
            <b class="text-ok">{{ demandCounts.resolved || 0 }}</b>
          </div>
          <div class="admin-kv">
            <span>已驳回</span>
            <b class="text-bad">{{ demandCounts.rejected || 0 }}</b>
          </div>
          <div class="admin-kv">
            <span>合计</span>
            <b>{{ demandCounts.total || 0 }}</b>
          </div>
          <router-link class="admin-inline-link" to="/admin/demands">
            去审核需求 →
          </router-link>
        </section>
      </div>

      <section class="admin-card">
        <div class="admin-card-head">
          <h3>快捷操作</h3>
        </div>
        <div class="admin-quick">
          <router-link to="/admin/resources" class="admin-quick-item">
            <AppIcon name="cloud" :size="18" /><span>管理资源索引</span>
          </router-link>
          <router-link to="/admin/demands" class="admin-quick-item">
            <AppIcon name="demand" :size="18" /><span>审核需求</span>
          </router-link>
          <router-link to="/admin/settings" class="admin-quick-item">
            <AppIcon name="tools" :size="18" /><span>修改站点设置</span>
          </router-link>
        </div>
      </section>

      <section class="admin-card">
        <h3>最近操作</h3>
        <div v-if="!audit.length" class="admin-empty">暂无审计记录</div>
        <div v-else class="admin-audit">
          <div v-for="row in audit" :key="row.id" class="admin-audit-row">
            <span class="admin-audit-action">{{ row.action }}</span>
            <span class="admin-audit-target">{{ row.target || '—' }}</span>
            <span class="admin-audit-user">{{ row.username || '—' }}</span>
            <span class="admin-audit-time">{{ formatTime(row.created_at) }}</span>
          </div>
        </div>
      </section>
    </template>
  </div>
</template>
