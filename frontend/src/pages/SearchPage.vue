<script>
const API = '/api'

const DISK_LABELS = { baidu: '百度', quark: '夸克', aliyun: '阿里', xunlei: '迅雷', '115': '115', tianyi: '天翼', uc: 'UC', pikpak: 'PikPak', '123': '123', magnet: '磁力', ed2k: '电驴', others: '其他' }

export default {
  data() {
    return {
      keyword: '',
      loading: false,
      error: null,
      results: [],
      total: 0,
      rawCount: 0,
      page: 1,
      pageSize: 20,
      totalPages: 1,
      cached: false,
      archiveCount: 0,
      realtimeCount: 0,
      pansouError: null,
      searched: false,
      diskType: '',
      expanded: {},
      history: [],
      diskTypeOptions: ['', 'baidu', 'quark', 'aliyun', 'xunlei', '115', 'tianyi', 'uc', 'pikpak', '123', 'magnet'],
    }
  },
  computed: {
    filtered() {
      if (!this.diskType) return this.results
      // 主链接或任一变体链接命中该网盘类型即保留该资源卡
      return this.results.filter(r =>
        r.disk_type === this.diskType ||
        (r.variants || []).some(v => v.disk_type === this.diskType)
      )
    },
    pagerItems() {
      const t = this.totalPages, c = this.page
      const items = []
      const push = (n, label) => items.push({ k: label || 'p' + n, n: n || 0, label: label || String(n), cur: n === c })
      if (t <= 7) {
        for (let i = 1; i <= t; i++) push(i)
      } else {
        push(1)
        if (c > 3) push(0, '…')
        for (let i = Math.max(2, c - 1); i <= Math.min(t - 1, c + 1); i++) push(i)
        if (c < t - 2) push(0, '…')
        push(t)
      }
      return items
    },
  },
  async mounted() {
    this.loadHistory()
  },
  methods: {
    async fetchSearch(params) {
      this.loading = true
      this.error = null
      try {
        const resp = await fetch(`${API}/search`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(params),
        })
        const data = await resp.json()
        if (data.error) {
          this.error = data.error
        } else {
          this.results = data.results || []
          this.total = data.total || 0
          this.rawCount = data.raw_count || 0
          this.page = data.page || 1
          this.totalPages = data.total_pages || 1
          this.cached = !!data.cached
          this.archiveCount = data.archive_count || 0
          this.realtimeCount = data.realtime_count || 0
          this.pansouError = data.pansou_error || null
        }
      } catch (e) {
        this.error = '请求失败: ' + e.message
      }
      this.loading = false
      this.searched = true
    },
    async doSearch() {
      const kw = this.keyword.trim()
      if (!kw) return
      this.results = []
      this.expanded = {}
      await this.fetchSearch({ kw, page: 1, page_size: this.pageSize })
      this.loadHistory()
    },
    async goPage(p) {
      if (this.loading || p < 1 || p > this.totalPages || p === this.page) return
      this.expanded = {}
      await this.fetchSearch({ kw: this.keyword.trim(), page: p, page_size: this.pageSize })
      window.scrollTo({ top: 0, behavior: 'smooth' })
    },
    async setPageSize() {
      if (!this.searched || !this.keyword.trim()) return
      this.expanded = {}
      await this.fetchSearch({ kw: this.keyword.trim(), page: 1, page_size: this.pageSize })
    },
    // ===== 搜索历史 =====
    async loadHistory() {
      try {
        const resp = await fetch(`${API}/history?limit=12`)
        const data = await resp.json()
        this.history = data.history || []
      } catch (e) { /* 历史加载失败不阻塞搜索 */ }
    },
    searchFrom(kw) {
      this.keyword = kw
      this.doSearch()
    },
    async delHistory(kw) {
      await fetch(`${API}/history?kw=${encodeURIComponent(kw)}`, { method: 'DELETE' })
      this.loadHistory()
    },
    async clearHistory() {
      await fetch(`${API}/history`, { method: 'DELETE' })
      this.history = []
    },
    toggleVariant(i) {
      this.expanded = { ...this.expanded, [i]: !this.expanded[i] }
    },
    handleKeydown(e) {
      if (e.key === 'Enter') this.doSearch()
    },
    getDiskLabel(t) {
      const map = { baidu: '百度', quark: '夸克', aliyun: '阿里', xunlei: '迅雷', '115': '115', tianyi: '天翼', uc: 'UC', pikpak: 'PikPak', '123': '123', magnet: '磁力', ed2k: '电驴' }
      return map[t] || t
    },
  },
}
</script>

<template>
  <div>
    <!-- 页面头部 -->
    <div class="header">
      <h1>DuPanSou-Archive</h1>
      <p>网盘资源聚合搜索引擎 · 历史索引 + 实时搜索</p>
    </div>

    <!-- 搜索框 -->
    <div class="search-box">
      <input
        v-model="keyword"
        placeholder="搜索关键词，如：庆余年、考研资料、Photoshop..."
        @keydown="handleKeydown"
      />
      <button @click="doSearch" :disabled="loading || !keyword.trim()">
        <span v-if="loading" class="spinner"></span>
        {{ loading ? '搜索中...' : '搜索' }}
      </button>
    </div>

    <!-- 搜索历史 -->
    <div class="history-row" v-if="history.length > 0 && !loading">
      <span class="history-label">搜索历史</span>
      <span class="history-chip" v-for="h in history" :key="h.keyword"
            :title="`搜过 ${h.cnt} 次`" @click="searchFrom(h.keyword)">
        {{ h.keyword }}
        <a class="chip-x" @click.stop="delHistory(h.keyword)">×</a>
      </span>
      <a class="history-clear" @click="clearHistory">清空</a>
    </div>

    <!-- 网盘类型过滤 -->
    <div class="controls-row" v-if="searched && !loading">
      <span style="font-size:13px;color:var(--text-secondary)">网盘过滤：</span>
      <select v-model="diskType" style="padding:6px 10px;border:1px solid var(--border);border-radius:4px;background:var(--bg-card);color:var(--text);font-size:13px;outline:none;">
        <option value="">全部</option>
        <option v-for="t in diskTypeOptions.slice(1)" :key="t" :value="t">{{ getDiskLabel(t) }}</option>
      </select>
    </div>

    <!-- 错误 -->
    <div class="error-msg" v-if="error">{{ error }}</div>

    <!-- 状态 -->
    <div v-if="loading" class="loading"><span class="spinner"></span>正在搜索...</div>

    <!-- 搜索结果 -->
    <div v-if="!loading && searched">
      <!-- PanSou 离线提示 -->
      <div class="error-msg" v-if="pansouError" style="margin-bottom:12px;">
        ⚠ 实时搜索不可用: {{ pansouError }}<br>
        <span style="font-size:12px;">仅显示历史索引结果</span>
      </div>

      <!-- 空结果 -->
      <div class="empty-state" v-if="filtered.length === 0 && !pansouError">
        <div class="icon">📭</div>
        <p>暂无结果，试试其他关键词</p>
      </div>

      <!-- 融合结果流 -->
      <div v-if="filtered.length > 0">
        <h3 style="margin-bottom:10px;">
          🔎 搜索结果
          <span style="font-size:12px;color:var(--text-secondary);font-weight:normal;">
            第 {{ page }}/{{ totalPages }} 页 · 共 {{ total }} 个资源 {{ rawCount }} 条链接 · 实时 {{ realtimeCount }} · 历史 {{ archiveCount }}
            <span v-if="cached" title="TTL 内翻页直接读缓存，不再请求搜索源">· ⚡缓存</span>
          </span>
        </h3>
        <div class="result-card" v-for="(r, i) in filtered" :key="i">
          <div class="title">{{ r.title || '(无标题)' }}</div>
          <div class="url-row">
            <a :href="r.url" target="_blank" rel="noopener">{{ r.url }}</a>
            <span v-if="r.password">🔑 {{ r.password }}</span>
          </div>
          <div class="meta">
            <span class="badge badge-disk">{{ getDiskLabel(r.disk_type) }}</span>
            <span class="badge" :class="r.from_archive ? 'badge-archive' : 'badge-alive'">
              {{ r.from_archive ? '历史' : '实时' }}
            </span>
            <span v-if="r.from_archive" class="badge" :class="r.alive ? 'badge-alive' : 'badge-dead'">
              {{ r.alive ? '有效' : '失效' }}
            </span>
            <span class="badge badge-source">{{ r.source }}</span>
            <span style="font-size:11px;color:var(--text-secondary)">
              {{ r.datetime || r.last_seen ? (r.datetime || r.last_seen).substring(0,16).replace('T',' ') : '时间未知' }}
            </span>
          </div>
          <!-- 同资源其他链接（不同网盘/不同发布帖） -->
          <div class="variants" v-if="r.variants && r.variants.length">
            <a href="#" class="variant-toggle" @click.prevent="toggleVariant(i)">
              {{ expanded[i] ? '▾' : '▸' }} 同资源其他 {{ r.variants.length }} 个链接
            </a>
            <div class="variant-list" v-if="expanded[i]">
              <div class="variant-row" v-for="(v, j) in r.variants" :key="j">
                <span class="badge badge-disk">{{ getDiskLabel(v.disk_type) }}</span>
                <a :href="v.url" target="_blank" rel="noopener">{{ v.url }}</a>
                <span v-if="v.password" class="variant-pw">🔑 {{ v.password }}</span>
                <span class="badge" :class="v.from_archive ? 'badge-archive' : 'badge-alive'">
                  {{ v.from_archive ? '历史' : '实时' }}
                </span>
              </div>
            </div>
          </div>
        </div>

        <!-- 翻页 -->
        <div class="pager" v-if="totalPages > 1 && !loading">
          <button class="pager-btn" :disabled="page <= 1" @click="goPage(page - 1)">‹ 上一页</button>
          <button v-for="p in pagerItems" :key="p.k"
                  class="pager-btn"
                  :class="{ 'pager-cur': p.cur }"
                  :disabled="!p.n"
                  @click="p.n && goPage(p.n)">{{ p.label }}</button>
          <button class="pager-btn" :disabled="page >= totalPages" @click="goPage(page + 1)">下一页 ›</button>
          <select class="pager-size" v-model.number="pageSize" @change="setPageSize" title="每页资源数">
            <option :value="10">10/页</option>
            <option :value="20">20/页</option>
            <option :value="50">50/页</option>
          </select>
        </div>
      </div>
    </div>

    <!-- 首次打开 -->
    <div class="empty-state" v-if="!searched && !loading">
      <div class="icon">🔍</div>
      <p>输入关键词开始搜索<br><span style="font-size:12px;color:var(--text-secondary)">支持电影、教程、软件、电子书等资源</span></p>
    </div>
  </div>
</template>