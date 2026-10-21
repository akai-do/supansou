<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { adminFetch } from '../../adminApi.js'
import { DISK_LABELS, DISK_TAG_CLASS } from '../../constants.js'

const kw = ref('')
const diskType = ref('')
const includeDead = ref(true)
const page = ref(1)
const pageSize = ref(50)
const rows = ref([])
const total = ref(0)
const totalPages = ref(1)
const loading = ref(false)
const error = ref('')
const notice = ref('')
const selected = ref({})

const form = reactive({
  url: '',
  title: '',
  disk_type: 'baidu',
  password: '',
  source: '',
})
const adding = ref(false)
const formError = ref('')

const importText = ref('')
const importing = ref(false)
const importResult = ref(null)

const diskOptions = Object.keys(DISK_LABELS)

const selectedUrls = computed(() =>
  Object.keys(selected.value).filter(url => selected.value[url])
)
const allSelected = computed(() =>
  rows.value.length > 0 && rows.value.every(row => selected.value[row.url])
)

function diskLabel(t) { return DISK_LABELS[t] || t || '其他' }
function tagClass(t) { return DISK_TAG_CLASS[t] || 'tag-other' }
function formatDate(value) {
  if (!value) return '—'
  return String(value).substring(0, 16).replace('T', ' ')
}
function validityText(row) {
  const v = row.validity || ''
  if (v === 'ok') return '有效'
  if (v === 'suspect') return '疑似失效'
  if (v === 'dead') return '已失效'
  return row.alive ? '未检测' : '已失效'
}
function validityClass(row) {
  const v = row.validity || ''
  if (v === 'ok') return 'is-ok'
  if (v === 'suspect') return 'is-warn'
  if (v === 'dead' || !row.alive) return 'is-bad'
  return 'is-dim'
}
function showNotice(msg) {
  notice.value = msg
  setTimeout(() => { notice.value = '' }, 2200)
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const params = new URLSearchParams({
      kw: kw.value.trim(),
      disk_type: diskType.value,
      include_dead: includeDead.value ? '1' : '0',
      page: String(page.value),
      page_size: String(pageSize.value),
    })
    const data = await adminFetch('/admin/resources?' + params.toString())
    rows.value = data.items || []
    total.value = data.total || 0
    totalPages.value = data.total_pages || 1
    selected.value = {}
  } catch (e) {
    error.value = e.message || '资源列表加载失败'
  } finally {
    loading.value = false
  }
}

function search() {
  page.value = 1
  load()
}

function resetFilters() {
  kw.value = ''
  diskType.value = ''
  includeDead.value = true
  page.value = 1
  load()
}

function goPage(p) {
  if (loading.value || p < 1 || p > totalPages.value) return
  page.value = p
  load()
}

function toggleOne(url) {
  selected.value = { ...selected.value, [url]: !selected.value[url] }
}

function toggleAll() {
  if (allSelected.value) {
    selected.value = {}
  } else {
    const next = {}
    for (const row of rows.value) next[row.url] = true
    selected.value = next
  }
}

async function addResource() {
  formError.value = ''
  if (!form.url.trim() || !form.title.trim()) {
    formError.value = '链接和标题不能为空'
    return
  }
  adding.value = true
  try {
    await adminFetch('/admin/resources', {
      method: 'POST',
      body: {
        url: form.url.trim(),
        title: form.title.trim(),
        disk_type: form.disk_type,
        password: form.password.trim(),
        source: form.source.trim() || 'admin',
      },
    })
    form.url = ''
    form.title = ''
    form.password = ''
    form.source = ''
    showNotice('资源已保存/更新')
    await load()
  } catch (e) {
    formError.value = e.message || '保存失败'
  } finally {
    adding.value = false
  }
}

async function importResources() {
  importResult.value = null
  let items
  try {
    items = JSON.parse(importText.value)
  } catch (e) {
    importResult.value = { error: 'JSON 解析失败，请检查格式' }
    return
  }
  if (!Array.isArray(items) || !items.length) {
    importResult.value = { error: '请提供非空 JSON 数组' }
    return
  }
  importing.value = true
  try {
    const data = await adminFetch('/admin/resources/import', {
      method: 'POST',
      body: { items },
    })
    importResult.value = data
    showNotice(`导入完成：处理 ${data.processed} 条`)
    await load()
  } catch (e) {
    importResult.value = { error: e.message || '导入失败' }
  } finally {
    importing.value = false
  }
}

async function deleteSelected() {
  const urls = selectedUrls.value
  if (!urls.length) return
  if (!window.confirm(`确认删除选中的 ${urls.length} 条索引？此操作会同步影响搜索结果。`)) {
    return
  }
  try {
    const data = await adminFetch('/admin/resources/batch-delete', {
      method: 'POST',
      body: { urls },
    })
    showNotice(`已删除 ${data.deleted} 条`)
    await load()
  } catch (e) {
    error.value = e.message || '批量删除失败'
  }
}

onMounted(load)
</script>

<template>
  <div class="admin-resources">
    <div class="admin-page-head">
      <div>
        <h2>资源管理</h2>
        <p>直接管理本地索引；新增/导入会走同一套 URL 去重与标题清洗。</p>
      </div>
      <button class="btn sm" :disabled="loading" @click="load">刷新</button>
    </div>

    <div v-if="notice" class="admin-notice">{{ notice }}</div>
    <div v-if="error" class="error-msg">{{ error }}</div>

    <section class="admin-card">
      <h3>新增 / 更新资源</h3>
      <form class="admin-form-grid" @submit.prevent="addResource">
        <label class="admin-field wide">
          <span>链接 *</span>
          <input v-model="form.url" placeholder="https://... 或 magnet:?..." />
        </label>
        <label class="admin-field wide">
          <span>标题 *</span>
          <input v-model="form.title" maxlength="120" placeholder="资源标题" />
        </label>
        <label class="admin-field">
          <span>网盘</span>
          <select v-model="form.disk_type">
            <option v-for="t in diskOptions" :key="t" :value="t">{{ diskLabel(t) }}</option>
          </select>
        </label>
        <label class="admin-field">
          <span>提取码</span>
          <input v-model="form.password" maxlength="40" placeholder="可选" />
        </label>
        <label class="admin-field">
          <span>来源</span>
          <input v-model="form.source" maxlength="80" placeholder="admin" />
        </label>
        <div class="admin-form-actions">
          <button class="btn primary" type="submit" :disabled="adding">
            {{ adding ? '保存中…' : '保存资源' }}
          </button>
        </div>
      </form>
      <div v-if="formError" class="error-msg">{{ formError }}</div>
    </section>

    <section class="admin-card">
      <h3>批量导入</h3>
      <p class="admin-hint">
        粘贴 JSON 数组，每项包含 url、title、disk_type、password、source；单次最多 500 条。
      </p>
      <textarea v-model="importText" class="admin-textarea" rows="5"
                placeholder='[{"url":"https://pan.baidu.com/s/...","title":"资源标题","disk_type":"baidu","password":"1234"}]'></textarea>
      <div class="admin-actions">
        <button class="btn" :disabled="importing" @click="importResources">
          {{ importing ? '导入中…' : '导入 JSON' }}
        </button>
        <span v-if="importResult?.error" class="text-bad">{{ importResult.error }}</span>
        <span v-else-if="importResult" class="text-ok">
          处理 {{ importResult.processed }} 条，跳过 {{ importResult.skipped }} 条
        </span>
      </div>
    </section>

    <div class="admin-toolbar">
      <input v-model="kw" class="admin-search" placeholder="搜索标题或 URL"
             @keydown.enter="search" />
      <select v-model="diskType">
        <option value="">全部网盘</option>
        <option v-for="t in diskOptions" :key="t" :value="t">{{ diskLabel(t) }}</option>
      </select>
      <label class="toggle-label">
        <input type="checkbox" v-model="includeDead" /> 包含失效
      </label>
      <button class="btn sm" @click="search">搜索</button>
      <button class="btn sm" @click="resetFilters">重置</button>
      <span class="admin-toolbar-spacer"></span>
      <button class="btn sm danger" :disabled="!selectedUrls.length"
              @click="deleteSelected">
        删除选中（{{ selectedUrls.length }}）
      </button>
    </div>

    <div v-if="loading" class="loading"><span class="spinner"></span>加载中…</div>
    <div v-else-if="!rows.length" class="admin-empty">没有匹配的资源</div>
    <div v-else class="admin-table-wrap">
      <table class="admin-table">
        <thead>
          <tr>
            <th class="admin-check-cell">
              <input type="checkbox" :checked="allSelected" @change="toggleAll" />
            </th>
            <th>标题</th>
            <th>网盘</th>
            <th>状态</th>
            <th>链接</th>
            <th>最后出现</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.url">
            <td class="admin-check-cell">
              <input type="checkbox" :checked="!!selected[row.url]"
                     @change="toggleOne(row.url)" />
            </td>
            <td class="admin-title-cell" :title="row.title">{{ row.title || '(无标题)' }}</td>
            <td><span class="disk-tag" :class="tagClass(row.disk_type)">{{ diskLabel(row.disk_type) }}</span></td>
            <td><span class="admin-badge" :class="validityClass(row)">{{ validityText(row) }}</span></td>
            <td class="admin-url-cell">
              <a :href="row.url" target="_blank" rel="noopener">{{ row.url }}</a>
            </td>
            <td class="admin-nowrap">{{ formatDate(row.last_seen) }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="admin-pager" v-if="totalPages > 1">
      <button class="btn sm" :disabled="page <= 1" @click="goPage(page - 1)">上一页</button>
      <span>第 {{ page }} / {{ totalPages }} 页 · 共 {{ total }} 条</span>
      <button class="btn sm" :disabled="page >= totalPages" @click="goPage(page + 1)">下一页</button>
    </div>
  </div>
</template>
