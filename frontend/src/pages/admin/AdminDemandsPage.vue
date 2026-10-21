<script setup>
import { onMounted, reactive, ref } from 'vue'
import { adminFetch } from '../../adminApi.js'

const statusFilter = ref('')
const page = ref(1)
const pageSize = ref(20)
const items = ref([])
const total = ref(0)
const totalPages = ref(1)
const counts = ref({})
const loading = ref(false)
const error = ref('')
const notice = ref('')
const drafts = reactive({})
const commentState = reactive({})
const replyBusy = reactive({})

const statusOptions = [
  { value: 'pending', label: '待处理' },
  { value: 'resolved', label: '已解决' },
  { value: 'rejected', label: '已驳回' },
]

const statusMap = Object.fromEntries(statusOptions.map(x => [x.value, x.label]))

function statusLabel(s) { return statusMap[s] || s }

function formatDate(value) {
  if (!value) return '—'
  return String(value).substring(0, 16).replace('T', ' ')
}

function showNotice(msg) {
  notice.value = msg
  setTimeout(() => { notice.value = '' }, 2200)
}

function ensureCommentState(id) {
  if (!commentState[id]) {
    commentState[id] = {
      open: false,
      loading: false,
      error: '',
      items: [],
      reply: '',
      includeHidden: true,
    }
  }
  return commentState[id]
}

function initDrafts(rows) {
  for (const key of Object.keys(drafts)) delete drafts[key]
  for (const item of rows) {
    drafts[item.id] = {
      status: item.status || 'pending',
      note: item.status === 'resolved'
        ? (item.resolve_note || '')
        : (item.status === 'rejected' ? (item.reject_reason || '') : ''),
    }
    ensureCommentState(item.id)
  }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const params = new URLSearchParams({
      status: statusFilter.value,
      page: String(page.value),
      page_size: String(pageSize.value),
    })
    const data = await adminFetch('/admin/demands?' + params.toString())
    items.value = data.items || []
    total.value = data.total || 0
    totalPages.value = data.total_pages || 1
    counts.value = data.counts || {}
    initDrafts(items.value)
  } catch (e) {
    error.value = e.message || '需求列表加载失败'
  } finally {
    loading.value = false
  }
}

function setFilter(value) {
  statusFilter.value = value
  page.value = 1
  load()
}

function goPage(p) {
  if (loading.value || p < 1 || p > totalPages.value) return
  page.value = p
  load()
}

async function save(item, okMessage = '') {
  const draft = drafts[item.id]
  if (!draft) return
  try {
    await adminFetch(`/admin/demands/${item.id}`, {
      method: 'PUT',
      body: {
        status: draft.status,
        resolve_note: draft.status === 'resolved' ? draft.note : '',
        reject_reason: draft.status === 'rejected' ? draft.note : '',
      },
    })
    showNotice(okMessage || `需求 #${item.id} 已更新`)
    await load()
  } catch (e) {
    error.value = e.message || '更新失败'
  }
}

async function quickResolve(item) {
  const draft = drafts[item.id]
  if (!draft) return
  draft.status = 'resolved'
  await save(item, `需求 #${item.id} 已标记为已解决`)
}

async function toggleComments(item) {
  const state = ensureCommentState(item.id)
  state.open = !state.open
  if (state.open) await loadComments(item.id)
}

async function loadComments(demandId) {
  const state = ensureCommentState(demandId)
  state.loading = true
  state.error = ''
  try {
    const data = await adminFetch(
      `/admin/demands/${demandId}/comments?include_hidden=${state.includeHidden ? '1' : '0'}`
    )
    state.items = data.items || []
    const row = items.value.find(x => x.id === demandId)
    if (row) {
      row.comment_count = state.items.filter(c => c.status === 'visible').length
    }
  } catch (e) {
    state.error = e.message || '评论加载失败'
  } finally {
    state.loading = false
  }
}

async function setCommentStatus(comment, status) {
  try {
    await adminFetch(`/admin/comments/${comment.id}`, {
      method: 'PUT',
      body: { status },
    })
    showNotice(status === 'hidden' ? '评论已隐藏' : '评论已恢复显示')
    await loadComments(comment.demand_id)
  } catch (e) {
    error.value = e.message || '操作失败'
  }
}

async function deleteComment(comment) {
  if (!window.confirm(`确认删除这条评论？\n「${comment.content.slice(0, 40)}」`)) return
  try {
    await adminFetch(`/admin/comments/${comment.id}`, { method: 'DELETE' })
    showNotice('评论已删除')
    await loadComments(comment.demand_id)
  } catch (e) {
    error.value = e.message || '删除失败'
  }
}

async function replyComment(item) {
  const state = ensureCommentState(item.id)
  const content = state.reply.trim()
  if (!content || replyBusy[item.id]) return
  replyBusy[item.id] = true
  try {
    await adminFetch(`/admin/demands/${item.id}/comments`, {
      method: 'POST',
      body: { content },
    })
    state.reply = ''
    showNotice('站长回复已发布')
    await loadComments(item.id)
  } catch (e) {
    state.error = e.message || '回复失败'
  } finally {
    replyBusy[item.id] = false
  }
}

async function remove(item) {
  if (!window.confirm(`确认删除需求 #${item.id}「${item.title}」？`)) return
  try {
    await adminFetch(`/admin/demands/${item.id}`, { method: 'DELETE' })
    showNotice(`需求 #${item.id} 已删除`)
    await load()
  } catch (e) {
    error.value = e.message || '删除失败'
  }
}

onMounted(load)
</script>

<template>
  <div class="admin-demands">
    <div class="admin-page-head">
      <div>
        <h2>需求管理</h2>
        <p>审核用户提交的需求；联系方式仅在本页展示。</p>
      </div>
      <button class="btn sm" :disabled="loading" @click="load">刷新</button>
    </div>

    <div class="admin-tabs">
      <button class="admin-tab" :class="{ active: statusFilter === '' }"
              @click="setFilter('')">全部 {{ counts.total || 0 }}</button>
      <button class="admin-tab" :class="{ active: statusFilter === 'pending' }"
              @click="setFilter('pending')">待处理 {{ counts.pending || 0 }}</button>
      <button class="admin-tab" :class="{ active: statusFilter === 'resolved' }"
              @click="setFilter('resolved')">已解决 {{ counts.resolved || 0 }}</button>
      <button class="admin-tab" :class="{ active: statusFilter === 'rejected' }"
              @click="setFilter('rejected')">已驳回 {{ counts.rejected || 0 }}</button>
    </div>

    <div v-if="notice" class="admin-notice">{{ notice }}</div>
    <div v-if="error" class="error-msg">{{ error }}</div>
    <div v-if="loading" class="loading"><span class="spinner"></span>加载中…</div>
    <div v-else-if="!items.length" class="admin-empty">没有匹配的需求</div>

    <div v-else class="admin-demand-list">
      <article v-for="item in items" :key="item.id" class="admin-demand-card">
        <div class="admin-demand-head">
          <h3>#{{ item.id }} {{ item.title }}</h3>
          <div class="admin-demand-head-actions">
            <button v-if="item.status !== 'resolved'" class="btn primary sm"
                    @click="quickResolve(item)">✓ 一键已解决</button>
            <span class="admin-badge"
                  :class="item.status === 'resolved' ? 'is-ok' : (item.status === 'rejected' ? 'is-bad' : 'is-warn')">
              {{ statusLabel(item.status) }}
            </span>
          </div>
        </div>

        <div class="admin-demand-meta">
          <span v-for="p in item.platforms" :key="p" class="demand-platform">{{ p }}</span>
          <span v-if="item.is_paid" class="demand-paid">愿意付费</span>
          <span>想要 {{ item.want_count }}</span>
          <span>评论 {{ item.comment_count || 0 }}</span>
          <span>{{ formatDate(item.created_at) }}</span>
        </div>

        <div class="admin-demand-body">
          <p v-if="item.remark"><b>备注：</b>{{ item.remark }}</p>
          <p v-if="item.play_url">
            <b>地址：</b>
            <a :href="item.play_url" target="_blank" rel="noopener">{{ item.play_url }}</a>
          </p>
          <p class="admin-contact"><b>联系方式：</b>{{ item.contact || '—' }}</p>
          <p v-if="item.resolve_note" class="text-ok"><b>解决说明：</b>{{ item.resolve_note }}</p>
          <p v-if="item.reject_reason" class="text-bad"><b>驳回原因：</b>{{ item.reject_reason }}</p>
        </div>

        <div class="admin-demand-review" v-if="drafts[item.id]">
          <select v-model="drafts[item.id].status">
            <option v-for="opt in statusOptions" :key="opt.value" :value="opt.value">
              {{ opt.label }}
            </option>
          </select>
          <input v-model="drafts[item.id].note" maxlength="300"
                 :placeholder="drafts[item.id].status === 'resolved'
                   ? '解决说明（可选）'
                   : (drafts[item.id].status === 'rejected' ? '驳回原因（可选）' : '待处理无需填写')" />
          <button class="btn primary sm" @click="save(item)">保存</button>
          <button class="btn danger sm" @click="remove(item)">删除</button>
        </div>

        <div class="admin-comment-section">
          <button class="admin-comment-toggle" type="button"
                  @click="toggleComments(item)">
            <span>💬 评论 {{ item.comment_count || 0 }}</span>
            <span class="admin-comment-caret">{{ commentState[item.id]?.open ? '收起' : '管理 / 回复' }}</span>
          </button>

          <div v-if="commentState[item.id]?.open" class="admin-comment-panel">
            <label class="admin-comment-filter">
              <input type="checkbox" v-model="commentState[item.id].includeHidden"
                     @change="loadComments(item.id)" />
              显示已隐藏评论
            </label>

            <div v-if="commentState[item.id].loading" class="admin-comment-loading">
              <span class="spinner"></span>评论加载中…
            </div>
            <div v-else-if="commentState[item.id].error" class="error-msg">
              {{ commentState[item.id].error }}
            </div>
            <div v-else-if="!commentState[item.id].items.length" class="admin-comment-empty">
              还没有评论
            </div>
            <div v-else class="admin-comment-list">
              <div v-for="c in commentState[item.id].items" :key="c.id"
                   class="admin-comment" :class="{ 'is-hidden': c.status === 'hidden' }">
                <div class="admin-comment-head">
                  <strong>{{ c.nickname }}</strong>
                  <span v-if="c.is_admin" class="admin-badge is-ok">站长</span>
                  <span v-if="c.status === 'hidden'" class="admin-badge is-bad">已隐藏</span>
                  <span class="admin-comment-time">{{ formatDate(c.created_at) }}</span>
                </div>
                <p>{{ c.content }}</p>
                <div class="admin-comment-actions">
                  <button class="btn sm"
                          @click="setCommentStatus(c, c.status === 'visible' ? 'hidden' : 'visible')">
                    {{ c.status === 'visible' ? '隐藏' : '恢复显示' }}
                  </button>
                  <button class="btn danger sm" @click="deleteComment(c)">删除</button>
                </div>
              </div>
            </div>

            <div class="admin-comment-reply">
              <input v-model="commentState[item.id].reply" maxlength="500"
                     placeholder="以站长身份回复这条需求…"
                     @keyup.enter="replyComment(item)" />
              <button class="btn primary sm" :disabled="replyBusy[item.id]"
                      @click="replyComment(item)">
                {{ replyBusy[item.id] ? '发送中…' : '回复' }}
              </button>
            </div>
          </div>
        </div>
      </article>
    </div>

    <div class="admin-pager" v-if="totalPages > 1">
      <button class="btn sm" :disabled="page <= 1" @click="goPage(page - 1)">上一页</button>
      <span>第 {{ page }} / {{ totalPages }} 页 · 共 {{ total }} 条</span>
      <button class="btn sm" :disabled="page >= totalPages" @click="goPage(page + 1)">下一页</button>
    </div>
  </div>
</template>
