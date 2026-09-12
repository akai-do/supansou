<script>
// 资源搜索页：融合搜索 + 智能有效性检测 + 封面图 + 失效隐藏 + 历史/热搜/纠错
import ResourceCard from '../components/ResourceCard.vue'
import {
  API, DISK_LABELS, DISK_FILTERS, cardDead, guessDiskType, isPanLink,
} from '../constants.js'

export default {
  components: { ResourceCard },
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
      correction: null,
      expanded: {},
      history: [],
      // 智能有效性检测
      checking: false,
      hideDead: true,
      showHidden: false,
      hiddenDead: [],
      hiddenDeadCount: 0,
      // 网盘筛选 chips（服务端过滤）；DISK_FILTERS 需挂 data 才能在模板使用
      diskType: '',
      DISK_FILTERS,
      // 封面图
      showPoster: localStorage.getItem('dps-poster') !== '0',
      posters: {},
      // 热搜词
      hotWords: [],
      // 粘贴链接直检
      linkCheck: null,
      // 轻提示
      toast: '',
      _toastTimer: null,
    }
  },
  computed: {
    displayList() {
      const showH = !this.hideDead || this.showHidden
      return showH ? [...this.results, ...this.hiddenDead] : this.results
    },
    filteredEmpty() {
      return this.results.length === 0 && this.hiddenDeadCount === 0
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
    // 智能检测状态：检测中 x/y / 有效 x/y
    checkStats() {
      let total = 0, known = 0, ok = 0
      for (const r of this.results) {
        if (!this.checkable(r)) continue
        total++
        const v = r.validity || ''
        if (v) known++
        if (v === 'ok') ok++
      }
      return { total, known, ok }
    },
  },
  watch: {
    '$route'(to) {
      if (to.path !== '/resource') return
      const kw = String(to.query.kw || '').trim()
      const link = String(to.query.link || '').trim()
      if (kw) {
        if (kw !== this.keyword) {
          this.keyword = kw
          this.doSearch()
        } else if (to.query._t) {
          this.doSearch()  // 同词强制重搜（dock 里再次点搜索）
        }
      } else if (link) {
        this.runLinkCheck(link)
      }
    },
    showPoster(v) { localStorage.setItem('dps-poster', v ? '1' : '0') },
  },
  async mounted() {
    this.loadHistory()
    this.loadHot()
    const kw = String(this.$route.query.kw || '').trim()
    const link = String(this.$route.query.link || '').trim()
    if (kw) {
      this.keyword = kw
      this.doSearch()
    } else if (link) {
      this.runLinkCheck(link)
    }
  },
  methods: {
    // ===== 搜索 =====
    async fetchSearch(params) {
      this.loading = true
      this.error = null
      this.linkCheck = null
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
          this.correction = data.correction || null
          this.hiddenDead = data.hidden_dead || []
          this.hiddenDeadCount = data.hidden_dead_count || 0
          this.showHidden = false
          // 渲染后异步补强：封面图优先于智能检测（图片是用户最直观的反馈）
          this.loadPosters()
          this.smartCheck()
        }
      } catch (e) {
        this.error = '请求失败: ' + e.message
      }
      this.loading = false
      this.searched = true
    },
    searchParams(page) {
      const p = { kw: this.keyword.trim(), page, page_size: this.pageSize }
      if (this.diskType) p.disk_types = [this.diskType]
      return p
    },
    async doSearch() {
      const kw = this.keyword.trim()
      if (!kw) return
      this.results = []
      this.posters = {}
      await this.fetchSearch(this.searchParams(1))
      this.loadHistory()
    },
    async goPage(p) {
      if (this.loading || p < 1 || p > this.totalPages || p === this.page) return
      await this.fetchSearch(this.searchParams(p))
      window.scrollTo({ top: 0, behavior: 'smooth' })
    },
    async setPageSize() {
      if (!this.searched || !this.keyword.trim()) return
      await this.fetchSearch(this.searchParams(1))
    },
    setDisk(t) {
      if (this.diskType === t) return
      this.diskType = t
      if (this.searched && this.keyword.trim()) this.doSearch()
    },
    // ===== 热搜 / 历史 =====
    async loadHot() {
      try {
        const resp = await fetch(`${API}/hot`)
        const data = await resp.json()
        this.hotWords = (data.hot || []).slice(0, 12)
      } catch (e) { /* 热搜失败不打扰 */ }
    },
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
    searchOriginal() {
      if (!this.correction) return
      this.keyword = this.correction.from
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
    // ===== 粘贴网盘链接直接检测 =====
    async runLinkCheck(link) {
      this.searched = true
      this.loading = false
      this.results = []
      this.hiddenDead = []
      this.hiddenDeadCount = 0
      this.linkCheck = { url: link, state: 'checking', validity: '', summary: '' }
      try {
        const resp = await fetch(`${API}/check/batch`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            items: [{ url: link, disk_type: guessDiskType(link), password: '' }],
          }),
        })
        const data = await resp.json()
        const r = (data.results || {})[link]
        if (r) {
          this.linkCheck = {
            url: link, state: r.state,
            validity: r.validity || '', summary: r.summary || '',
          }
        } else {
          this.linkCheck.state = 'error'
        }
      } catch (e) {
        this.linkCheck.state = 'error'
      }
    },
    linkCheckText() {
      const lc = this.linkCheck
      if (!lc) return ''
      if (lc.state === 'checking') return '🩺 正在检测该链接的有效性…'
      if (lc.validity === 'ok') return `✓ 链接有效${lc.summary ? '：' + lc.summary : ''}`
      if (lc.validity === 'suspect') return `⚠ 疑似失效${lc.summary ? '：' + lc.summary : '，建议打开确认'}`
      if (lc.validity === 'dead') return `✕ 链接已失效${lc.summary ? '：' + lc.summary : ''}`
      if (lc.state === 'unsupported') return '该链接类型暂不支持自动检测（如磁力/电驴），可直接打开确认'
      return '检测失败，请稍后重试'
    },
    // ===== 智能有效性检测 =====
    checkable(it) {
      if (!it || !/^https?:\/\//i.test(it.url || '')) return false
      const t = (it.disk_type || '').toLowerCase()
      return !['magnet', 'ed2k', 'others', ''].includes(t)
    },
    async smartCheck() {
      if (this.checking || !this.results.length) return
      const seen = new Set()
      const items = []
      const push = (it) => {
        if (!it || seen.has(it.url) || !this.checkable(it)) return
        seen.add(it.url)
        items.push({ url: it.url, disk_type: it.disk_type || '', password: it.password || '' })
      }
      this.results.forEach(r => {
        push(r)
        ;(r.variants || []).slice(0, 3).forEach(push)
      })
      if (!items.length) return
      this.checking = true
      try {
        const resp = await fetch(`${API}/check/batch`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ items: items.slice(0, 30) }),
        })
        const data = await resp.json()
        const res = data.results || {}
        const apply = (it) => {
          const st = res[it.url]
          if (st && st.state !== 'unsupported') {
            it.validity = st.validity || ''
            it.checked_at = st.checked_at || it.checked_at || ''
            it.state_summary = st.summary || it.state_summary || ''
          }
        }
        this.results = this.results.map(r => {
          const c = { ...r }
          apply(c)
          if (c.variants) c.variants = c.variants.map(v => { const w = { ...v }; apply(w); return w })
          return c
        })
        this.repositionDead()
      } catch (e) { /* 检测失败不打扰搜索结果展示 */ }
      this.checking = false
    },
    repositionDead() {
      const dead = this.results.filter(r => cardDead(r))
      if (!dead.length) return
      const rest = this.results.filter(r => !cardDead(r))
      if (this.hideDead) {
        this.hiddenDead = [...dead, ...this.hiddenDead]
        this.hiddenDeadCount += dead.length
        this.results = rest
      } else {
        this.results = [...rest, ...dead]
      }
    },
    // ===== 封面图 =====
    async loadPosters() {
      if (!this.showPoster || !this.results.length) return
      const seen = new Set()
      const titles = []
      for (const r of this.results) {
        if (r.title && !seen.has(r.title)) {
          seen.add(r.title)
          titles.push(r.title)
          if (titles.length >= 12) break
        }
      }
      if (!titles.length) return
      // 分两波并行请求：先回来的先渲染，不等全量
      const mid = Math.ceil(titles.length / 2)
      for (const chunk of [titles.slice(0, mid), titles.slice(mid)]) {
        if (!chunk.length) continue
        fetch(`${API}/poster/batch`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ titles: chunk }),
        })
          .then(r => r.json())
          .then(data => {
            if (data.posters) this.posters = { ...this.posters, ...data.posters }
          })
          .catch(() => { /* 封面失败静默兜底占位块 */ })
      }
    },
    // ===== 举报 =====
    async reportDead(item) {
      if (item.dead_marked) return
      try {
        const resp = await fetch(`${API}/report/dead`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ u: item.url, kw: this.keyword.trim() }),
        })
        const data = await resp.json()
        if (data.ok) {
          item.dead_marked = true
          item.alive = 0
          item.validity = 'dead'
          this.repositionDead()
          this.showToast('已标记失效，并撤销其点击热度')
        } else {
          this.showToast(data.error || '举报失败')
        }
      } catch (e) {
        this.showToast('举报失败: ' + e.message)
      }
    },
    // ===== 轻提示 =====
    showToast(msg) {
      this.toast = msg
      clearTimeout(this._toastTimer)
      this._toastTimer = setTimeout(() => { this.toast = '' }, 1800)
    },
    handleKeydown(e) {
      if (e.key === 'Enter') this.doSearch()
    },
    getDiskLabel(t) { return DISK_LABELS[t] || t },
  },
}
</script>

<template>
  <div class="page">
    <!-- 未搜索：热搜 + 历史 -->
    <div class="explore" v-if="!searched && !loading">
      <div class="explore-title" v-if="hotWords.length">🔥 热门搜索</div>
      <div class="chips-wrap" v-if="hotWords.length">
        <span class="chip hot" v-for="h in hotWords" :key="h.title"
              :title="`索引命中 ${h.count} 次`" @click="searchFrom(h.title)">{{ h.title }}</span>
      </div>
      <template v-if="history.length">
        <div class="explore-title">
          🕘 搜索历史 <a class="explore-clear" @click="clearHistory">清空</a>
        </div>
        <div class="chips-wrap">
          <span class="chip" v-for="h in history" :key="h.keyword"
                :title="`搜过 ${h.cnt} 次`" @click="searchFrom(h.keyword)">
            {{ h.keyword }}
            <a class="chip-x" @click.stop="delHistory(h.keyword)">×</a>
          </span>
        </div>
      </template>
      <div class="explore-hint" v-if="!hotWords.length && !history.length">
        输入关键词开始搜索 · 支持电影、剧集、教程、软件、电子书等<br>
        <span>粘贴网盘分享链接可直接检测有效性</span>
      </div>
    </div>

    <!-- 粘贴链接检测结果 -->
    <div class="link-check" v-if="linkCheck">
      <div class="lc-url">{{ linkCheck.url }}</div>
      <div class="lc-text" :class="'lc-' + (linkCheck.validity || linkCheck.state)">
        {{ linkCheckText() }}
      </div>
      <a v-if="/^https?:\/\//i.test(linkCheck.url)" class="btn primary sm"
         :href="linkCheck.url" target="_blank" rel="noopener">仍要打开</a>
    </div>

    <!-- 纠错 -->
    <div class="correction-bar" v-if="searched && correction">
      找不到「<b>{{ correction.from }}</b>」，已显示「<b>{{ correction.to }}</b>」的结果
      <a href="#" class="correction-link" @click.prevent="searchOriginal">改搜原词「{{ correction.from }}」</a>
    </div>

    <div class="error-msg" v-if="error">{{ error }}</div>

    <!-- 骨架屏 -->
    <div v-if="loading" class="skel-list">
      <div class="skel-card" v-for="i in 8" :key="i">
        <div class="skel-poster"></div>
        <div class="skel-body">
          <div class="skel-line w60"></div>
          <div class="skel-line w40"></div>
          <div class="skel-line w80"></div>
        </div>
      </div>
    </div>

    <!-- 结果区 -->
    <div v-if="!loading && searched">
      <div class="error-msg" v-if="pansouError" style="margin-bottom:12px;">
        ⚠ 实时搜索不可用: {{ pansouError }}<br>
        <span style="font-size:12px;">仅显示历史索引结果</span>
      </div>

      <div class="empty-state" v-if="results.length === 0 && filteredEmpty">
        <div class="icon">📭</div>
        <p>暂无结果，试试其他关键词</p>
      </div>

      <div v-if="results.length > 0 || hiddenDeadCount > 0">
        <!-- 状态条 -->
        <div class="status-bar">
          <span>🔎 共 {{ total }} 个资源 · {{ rawCount }} 条链接</span>
          <span class="st-dim">实时 {{ realtimeCount }} · 历史 {{ archiveCount }}</span>
          <span v-if="checkStats.total" class="st-check"
                :title="checking ? '正在对当前页链接做有效性检测' : '当前页检测结果'">
            {{ checking ? `🩺 智能检测中 ${checkStats.known}/${checkStats.total}` : `🩺 有效 ${checkStats.ok}/${checkStats.total}` }}
          </span>
          <span v-if="hiddenDeadCount > 0" class="st-dead">已隐藏 {{ hiddenDeadCount }} 条失效</span>
          <span v-if="cached" title="TTL 内翻页直接读缓存，不再请求搜索源">⚡缓存</span>
        </div>

        <!-- 筛选 chips + 开关 -->
        <div class="chips-row filters">
          <span class="chip" :class="{ active: diskType === '' }" @click="setDisk('')">全部</span>
          <span class="chip" v-for="t in DISK_FILTERS" :key="t"
                :class="{ active: diskType === t }" @click="setDisk(t)">{{ getDiskLabel(t) }}</span>
          <span class="flex-gap"></span>
          <label class="toggle-label" title="自动隐藏全部链接已确认失效的资源卡">
            <input type="checkbox" v-model="hideDead"> 隐藏失效
          </label>
          <label class="toggle-label" title="搜索结果卡片显示豆瓣封面图">
            <input type="checkbox" v-model="showPoster"> 封面图
          </label>
        </div>

        <!-- 隐藏失效资源 -->
        <div class="hidden-dead-bar" v-if="hiddenDeadCount > 0">
          🩺 智能检测已确认 {{ hiddenDeadCount }} 条资源失效并自动降权/隐藏
          <a @click.prevent="showHidden = !showHidden">{{ (!hideDead || showHidden) ? '收起' : '查看' }}</a>
        </div>

        <ResourceCard v-for="(r, i) in displayList" :key="i"
                      :item="r" :show-poster="showPoster"
                      :poster="posters[r.title] || ''"
                      :keyword="keyword.trim()"
                      @report="reportDead" @toast="showToast" />

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

    <div class="toast" v-if="toast">{{ toast }}</div>
  </div>
</template>
