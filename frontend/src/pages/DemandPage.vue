<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import AppIcon from '../components/AppIcon.vue'
import { API } from '../constants.js'
import { siteState } from '../site.js'

const enabled = computed(() => !!siteState.settings.features?.demand_enabled)
const tab = ref('history')
const platformOptions = ['腾讯视频', '爱奇艺', '优酷', '芒果TV', '哔哩哔哩', '其他']

const form = reactive({
  title: '',
  platforms: [],
  play_url: '',
  remark: '',
  contact: '',
  is_paid: false,
})
const submitting = ref(false)
const formError = ref('')
const formOk = ref('')

const history = ref([])
const total = ref(0)
const page = ref(1)
const totalPages = ref(1)
const sort = ref('want')
const loading = ref(false)
const historyError = ref('')
const loadedHistory = ref(false)
const wantBusy = reactive({})
const toast = ref('')
let toastTimer = null

const NICK_KEY = 'dps-demand-nickname'
const nickname = ref('')
const commentOpen = reactive({})
const commentLoading = reactive({})
const commentError = reactive({})
const commentItems = reactive({})
const commentDraft = reactive({})
const commentBusy = reactive({})

const statusMap = {
  pending: { label: '待处理', cls: 'is-pending' },
  resolved: { label: '已解决', cls: 'is-resolved' },
  rejected: { label: '已驳回', cls: 'is-rejected' },
}

function statusLabel(s) { return statusMap[s]?.label || s }
function statusClass(s) { return statusMap[s]?.cls || 'is-pending' }

function togglePlatform(p) {
  const idx = form.platforms.indexOf(p)
  if (idx >= 0) form.platforms.splice(idx, 1)
  else form.platforms.push(p)
}

function validate() {
  const title = form.title.trim()
  if (title.length < 2) return '资源名称至少 2 个字'
  if (title.length > 80) return '资源名称最多 80 个字'
  if (!form.platforms.length) return '请至少选择一个播放平台'
  if (form.play_url && !/^https?:\/\//i.test(form.play_url.trim())) {
    return '播放地址只支持 http(s) 链接'
  }
  if (form.remark.length > 500) return '备注最多 500 个字'
  if (form.contact.length > 100) return '联系方式最多 100 个字'
  return ''
}

function resetForm() {
  form.title = ''
  form.platforms = []
  form.play_url = ''
  form.remark = ''
  form.contact = ''
  form.is_paid = false
}

function showToast(msg) {
  toast.value = msg
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => { toast.value = '' }, 1800)
}

async function submitDemand() {
  if (!enabled.value) return
  formError.value = ''
  formOk.value = ''
  const error = validate()
  if (error) { formError.value = error; return }
  submitting.value = true
  try {
    const resp = await fetch(`${API}/demand`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title: form.title.trim(),
        platforms: [...form.platforms],
        play_url: form.play_url.trim(),
        remark: form.remark.trim(),
        contact: form.contact.trim(),
        is_paid: form.is_paid,
      }),
    })
    const data = await resp.json().catch(() => ({}))
    if (!resp.ok) throw new Error(data.error || '提交失败')
    formOk.value = '需求已提交，站长审核后会显示在历史需求里'
    resetForm()
    await loadHistory(true)
    tab.value = 'history'
  } catch (e) {
    formError.value = e.message || '提交失败'
  } finally {
    submitting.value = false
  }
}

async function loadHistory(reset = false) {
  if (!enabled.value) return
  if (reset) {
    page.value = 1
    history.value = []
    loadedHistory.value = false
  }
  loading.value = true
  historyError.value = ''
  try {
    const params = new URLSearchParams({
      sort: sort.value,
      page: String(page.value),
      page_size: '20',
    })
    const resp = await fetch(`${API}/demands?${params.toString()}`)
    const data = await resp.json().catch(() => ({}))
    if (!resp.ok) throw new Error(data.error || '历史需求加载失败')
    if (data.enabled === false) {
      history.value = []
      total.value = 0
      totalPages.value = 1
      return
    }
    const items = data.items || []
    history.value = reset ? items : [...history.value, ...items]
    total.value = data.total || 0
    totalPages.value = data.total_pages || 1
    loadedHistory.value = true
  } catch (e) {
    historyError.value = e.message || '历史需求加载失败'
  } finally {
    loading.value = false
  }
}

function changeSort() {
  sort.value = sort.value === 'want' ? 'time' : 'want'
  loadHistory(true)
}

function switchTab(value) {
  tab.value = value
  if (value === 'history' && !loadedHistory.value) loadHistory(true)
}

function loadMore() {
  if (loading.value || page.value >= totalPages.value) return
  page.value += 1
  loadHistory(false)
}

async function toggleWant(item) {
  if (wantBusy[item.id]) return
  wantBusy[item.id] = true
  try {
    const resp = await fetch(`${API}/demand/${item.id}/want`, { method: 'POST' })
    const data = await resp.json().catch(() => ({}))
    if (!resp.ok) throw new Error(data.error || '操作失败')
    item.wanted = !!data.wanted
    item.want_count = data.want_count || 0
  } catch (e) {
    showToast(e.message || '操作失败')
  } finally {
    delete wantBusy[item.id]
  }
}

async function toggleComments(item) {
  const next = !commentOpen[item.id]
  commentOpen[item.id] = next
  if (next && !commentItems[item.id]) {
    await loadComments(item)
  }
}

async function loadComments(item) {
  commentLoading[item.id] = true
  commentError[item.id] = ''
  try {
    const resp = await fetch(`${API}/demand/${item.id}/comments`)
    const data = await resp.json().catch(() => ({}))
    if (!resp.ok) throw new Error(data.error || '评论加载失败')
    commentItems[item.id] = data.items || []
    item.comment_count = data.total || 0
  } catch (e) {
    commentError[item.id] = e.message || '评论加载失败'
  } finally {
    commentLoading[item.id] = false
  }
}

async function submitComment(item) {
  const content = (commentDraft[item.id] || '').trim()
  if (!content) {
    commentError[item.id] = '先写点内容再发送吧'
    return
  }
  if (content.length > 500) {
    commentError[item.id] = '评论最多 500 个字'
    return
  }
  commentBusy[item.id] = true
  commentError[item.id] = ''
  try {
    const resp = await fetch(`${API}/demand/${item.id}/comments`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        nickname: nickname.value.trim(),
        content,
      }),
    })
    const data = await resp.json().catch(() => ({}))
    if (!resp.ok) throw new Error(data.error || '评论失败')
    if (!commentItems[item.id]) commentItems[item.id] = []
    commentItems[item.id].push(data.item)
    item.comment_count = (item.comment_count || 0) + 1
    commentDraft[item.id] = ''
    commentOpen[item.id] = true
    const nick = nickname.value.trim()
    if (nick) localStorage.setItem(NICK_KEY, nick)
    else localStorage.removeItem(NICK_KEY)
  } catch (e) {
    commentError[item.id] = e.message || '评论失败'
  } finally {
    commentBusy[item.id] = false
  }
}

function formatListDate(value) {
  if (!value) return ''
  const raw = String(value).replace(' ', 'T')
  const date = new Date(raw)
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 10)
  return `${date.getFullYear()}/${date.getMonth() + 1}/${date.getDate()}`
}

function formatCommentDate(value) {
  if (!value) return ''
  const raw = String(value).replace(' ', 'T')
  const date = new Date(raw)
  if (Number.isNaN(date.getTime())) return String(value).slice(5, 16)
  const pad = (n) => String(n).padStart(2, '0')
  return `${date.getMonth() + 1}/${date.getDate()} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function rankClass(index) {
  if (index === 0) return 'is-1'
  if (index === 1) return 'is-2'
  if (index === 2) return 'is-3'
  return ''
}

function avatarClass(name) {
  const chars = Array.from(name || '热')
  const code = chars.reduce((sum, ch) => sum + (ch.codePointAt(0) || 0), 0)
  return `av-${code % 6}`
}

onMounted(() => {
  nickname.value = localStorage.getItem(NICK_KEY) || ''
  if (enabled.value) loadHistory(true)
})

watch(enabled, (value) => {
  if (value && !loadedHistory.value) loadHistory(true)
})
</script>

<template>
  <div class="page demand-page demand-page--community">
    <header class="demand-hero">
      <span class="demand-hero-icon"><AppIcon name="spark" :size="26" /></span>
      <h2>资源需求</h2>
      <p>找不到想要的资源？提交需求，我们会尽快为你寻找。</p>
      <p class="demand-hero-note">本站也提供有偿下载服务，欢迎咨询！</p>
    </header>

    <div v-if="!enabled" class="placeholder-card">
      <div class="ph-icon"><AppIcon name="demand" :size="28" /></div>
      <h3>需求板暂未开启</h3>
      <p>当前站点没有开启需求板。你仍然可以直接搜索站内资源。</p>
      <router-link class="btn primary" to="/">返回首页搜索</router-link>
    </div>

    <template v-else>
      <div class="demand-switch" role="tablist">
        <button class="demand-switch-btn is-submit" :class="{ active: tab === 'submit' }"
                @click="switchTab('submit')" role="tab"
                :aria-selected="tab === 'submit'">
          <span class="ds-icon"><AppIcon name="plus" :size="18" /></span>
          提交需求
        </button>
        <button class="demand-switch-btn is-history" :class="{ active: tab === 'history' }"
                @click="switchTab('history')" role="tab"
                :aria-selected="tab === 'history'">
          <span class="ds-icon"><AppIcon name="menu" :size="18" /></span>
          历史需求
          <span class="ds-count">({{ total }})</span>
        </button>
      </div>

      <!-- 提交 -->
      <section v-if="tab === 'submit'" class="demand-card demand-submit-card">
        <form class="demand-form" @submit.prevent="submitDemand">
          <label class="demand-field">
            <span>资源名称 <b>*</b></span>
            <input v-model="form.title" maxlength="80"
                   placeholder="例如：庆余年 第三季 / 某套课程 / 某本书" />
          </label>

          <div class="demand-field">
            <span>播放平台 <b>*</b></span>
            <div class="platform-chips">
              <button v-for="p in platformOptions" :key="p" type="button"
                      class="chip" :class="{ active: form.platforms.includes(p) }"
                      @click="togglePlatform(p)">{{ p }}</button>
            </div>
          </div>

          <label class="demand-field">
            <span>播放地址</span>
            <input v-model="form.play_url" maxlength="500"
                   placeholder="可选，http(s) 链接" />
          </label>

          <label class="demand-field">
            <span>备注</span>
            <textarea v-model="form.remark" rows="3" maxlength="500"
                      placeholder="例如：想要 4K 中字、只要第二季、已有网盘等"></textarea>
          </label>

          <label class="demand-field">
            <span>联系方式</span>
            <input v-model="form.contact" maxlength="100"
                   placeholder="可选；仅站长可见，不会公开展示" />
          </label>

          <label class="demand-check">
            <input type="checkbox" v-model="form.is_paid" />
            愿意为找到的资源付费
          </label>

          <p class="demand-privacy">
            提交内容会先进入待处理状态；联系方式只有站长能看到。
            请勿提交违法、色情、广告或与资源无关的内容。
          </p>

          <div v-if="formError" class="error-msg">{{ formError }}</div>
          <div v-if="formOk" class="demand-ok">{{ formOk }}</div>

          <button class="btn primary demand-submit" type="submit"
                  :disabled="submitting">
            {{ submitting ? '提交中…' : '提交需求' }}
          </button>
        </form>
      </section>

      <!-- 历史 -->
      <section v-else class="demand-board">
        <div class="demand-board-head">
          <span class="demand-board-count">共 {{ total }} 条需求</span>
          <button class="demand-sort-btn" type="button" @click="changeSort">
            <span class="ds-sort-icon">⇅</span>
            {{ sort === 'want' ? '按想要数排序' : '按时间排序' }}
          </button>
        </div>

        <div v-if="historyError" class="error-msg">{{ historyError }}</div>
        <div v-if="loading && !history.length" class="loading demand-loading">
          <span class="spinner"></span>加载中...
        </div>
        <div v-else-if="!history.length && !historyError" class="empty-state">
          <div class="empty-icon"><AppIcon name="demand" :size="38" /></div>
          <p class="empty-title">还没有历史需求</p>
          <p class="empty-text">去“提交需求”留下第一个吧。</p>
        </div>

        <div v-else class="demand-list">
          <article v-for="(item, idx) in history" :key="item.id" class="demand-item">
            <div class="demand-item-row">
              <div class="demand-rank" :class="rankClass(idx)">{{ idx + 1 }}</div>

              <div class="demand-main">
                <div class="demand-head">
                  <h3>{{ item.title }}</h3>
                  <span class="demand-status" :class="statusClass(item.status)">
                    <AppIcon v-if="item.status === 'resolved'" name="check" :size="12" />
                    {{ statusLabel(item.status) }}
                  </span>
                </div>

                <div class="demand-meta">
                  <span class="demand-date">
                    <AppIcon name="clock" :size="12" />
                    {{ formatListDate(item.created_at) }}
                  </span>
                  <span v-if="item.remark" class="demand-remark">{{ item.remark }}</span>
                  <span v-if="item.status === 'resolved' && item.resolve_note"
                        class="demand-note is-ok">
                    解决说明：{{ item.resolve_note }}
                  </span>
                  <span v-if="item.status === 'rejected' && item.reject_reason"
                        class="demand-note is-bad">
                    驳回原因：{{ item.reject_reason }}
                  </span>
                </div>

                <a v-if="item.play_url" :href="item.play_url" target="_blank"
                   rel="noopener" class="demand-link">查看播放地址</a>
              </div>

              <div class="demand-actions">
                <button class="demand-action demand-want" :class="{ active: item.wanted }"
                        :disabled="wantBusy[item.id]" @click="toggleWant(item)"
                        :aria-label="item.wanted ? '取消想要' : '想要'">
                  <AppIcon name="heart" :size="20" />
                  <span>{{ item.want_count }}</span>
                </button>
                <button class="demand-action demand-comment-btn"
                        :class="{ active: commentOpen[item.id] }"
                        @click="toggleComments(item)"
                        :aria-label="`查看 ${item.comment_count || 0} 条评论`">
                  <AppIcon name="chat" :size="18" />
                  <span>{{ item.comment_count || 0 }}</span>
                </button>
              </div>
            </div>

            <div v-if="commentOpen[item.id]" class="demand-comments">
              <div v-if="commentLoading[item.id]" class="demand-comments-loading">
                <span class="spinner"></span>评论加载中…
              </div>
              <template v-else>
                <div v-if="commentItems[item.id]?.length" class="demand-comment-list">
                  <div v-for="c in commentItems[item.id]" :key="c.id" class="demand-comment">
                    <span class="dc-avatar" :class="avatarClass(c.nickname)">
                      {{ (c.nickname || '热')[0] }}
                    </span>
                    <div class="dc-body">
                      <div class="dc-head">
                        <strong>{{ c.nickname }}</strong>
                        <span v-if="c.is_admin" class="dc-admin">站长</span>
                        <span class="dc-time">{{ formatCommentDate(c.created_at) }}</span>
                      </div>
                      <p>{{ c.content }}</p>
                    </div>
                  </div>
                </div>
                <p v-else class="demand-comments-empty">还没有评论，来说两句吧～</p>

                <div v-if="commentError[item.id]" class="error-msg">{{ commentError[item.id] }}</div>
                <div class="demand-comment-form">
                  <input v-model="nickname" class="dc-nick" maxlength="20"
                         placeholder="昵称（可选）" />
                  <input v-model="commentDraft[item.id]" class="dc-input" maxlength="500"
                         :placeholder="`给「${item.title}」留个评论…`"
                         @keyup.enter="submitComment(item)" />
                  <button class="dc-send" type="button" :disabled="commentBusy[item.id]"
                          @click="submitComment(item)">
                    {{ commentBusy[item.id] ? '发送中' : '发送' }}
                  </button>
                </div>
              </template>
            </div>
          </article>

          <div v-if="page < totalPages" class="demand-more">
            <button class="btn" :disabled="loading" @click="loadMore">
              {{ loading ? '加载中…' : '加载更多' }}
            </button>
          </div>
        </div>
      </section>
    </template>

    <div class="toast" v-if="toast">{{ toast }}</div>
  </div>
</template>
