<script>
// 搜索结果页：居中 Hero 搜索（品牌 + 提示 + 胶囊搜索框 + 检索状态卡）
// + URL 状态（ai/disk/sort/link）+ 网盘计数/多选 + 排序 + 分页
// + 资源交付弹窗 + 有效性检测/举报/隐藏失效
import ResourceCard from '../components/ResourceCard.vue'
import ResourceDialog from '../components/ResourceDialog.vue'
import HeroSearch from '../components/HeroSearch.vue'
import SearchStatusBar from '../components/SearchStatusBar.vue'
import SortSelect from '../components/SortSelect.vue'
import DiskFilterChips from '../components/DiskFilterChips.vue'
import EmptyState from '../components/EmptyState.vue'
import { siteState, featureEnabled } from '../site.js'
import {
  API, DISK_LABELS, cardDead, guessDiskType, isPanLink,
} from '../constants.js'

const SORTS = ['relevance', 'time', 'name']

function sameArray(a, b) {
  return a.length === b.length && a.every((v, i) => v === b[i])
}

export default {
  components: {
    ResourceCard, ResourceDialog, HeroSearch, SearchStatusBar, SortSelect,
    DiskFilterChips, EmptyState,
  },
  data() {
    return {
      keyword: '',
      loading: false,
      filtering: false,
      error: null,
      results: [],
      total: 0,
      rawCount: 0,
      page: 1,
      pageSize: 20,
      totalPages: 1,
      cached: false,
      sort: 'relevance',
      elapsedMs: 0,
      pansouError: null,
      searched: false,
      correction: null,
      // 网盘筛选：服务端基于完整池计数
      selectedDiskTypes: [],
      diskCounts: {},
      diskTypeCount: 0,
      filtered: false,
      // 有效性
      checking: false,
      hideDead: true,
      showHidden: false,
      hiddenDead: [],
      hiddenDeadCount: 0,
      // 封面：默认无封面，勾选后记住偏好（dps-poster='1'）
      showPoster: localStorage.getItem('dps-poster') === '1',
      posters: {},
      // 粘贴链接直检
      linkCheck: null,
      // 本轮搜索是否已产出过结果（翻页/改筛选再加载时维持 compact 态）
      gotResults: false,
      // 流式搜索：streaming=正在渐进接收；streamOk=本次会话是否可用
      // （后端 STREAM_SEARCH 开关经 /api/public/settings 下发；一旦失败自动回退）
      streaming: false,
      streamOk: featureEnabled('stream_search'),
      // 资源弹窗
      dialog: { open: false, card: null, item: null },
      // 轻提示
      toast: '',
      _toastTimer: null,
      _elapsedTimer: null,
      _searchStartedAt: 0,
      _abortCtrl: null,
    }
  },
  computed: {
    site() {
      return siteState.settings.site || {}
    },
    notice() {
      return siteState.settings.notice || {}
    },
    // Hero 品牌区下方的温馨提示：notice 未启用或为空时用兜底文案
    heroTip() {
      const n = this.notice
      if (n && n.enabled && n.content) return n.content
      return '支持关键词搜索，也可以直接粘贴网盘分享链接检测有效性'
    },
    // Hero 搜索框回填：路由关键词（ai/kw/link）
    heroInitial() {
      const q = this.$route.query
      return String(q.ai || q.kw || q.link || '')
    },
    // 有过结果（含翻页/改筛选的再次加载）→ Hero 保持 compact 态
    heroCompact() {
      return !this.linkCheck && this.gotResults
        && (this.loading || this.results.length > 0 || this.hiddenDeadCount > 0)
    },
    // 加载态展示：秒数 / 已收条数
    seconds() {
      return (Math.max(0, this.elapsedMs) / 1000).toFixed(1)
    },
    received() {
      return this.loading ? (this.results.length + this.hiddenDead.length) : this.total
    },
    displayList() {
      const showHidden = !this.hideDead || this.showHidden
      return showHidden ? [...this.results, ...this.hiddenDead] : this.results
    },
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
    pagerItems() {
      const t = this.totalPages, c = this.page
      const items = []
      const push = (n, label) => items.push({
        k: label || 'p' + n, n: n || 0, label: label || String(n), cur: n === c,
      })
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
    emptyTitle() {
      return this.selectedDiskTypes.length ? '当前筛选下没有结果' : '未找到相关资源'
    },
    emptyText() {
      if (this.selectedDiskTypes.length) return '可以取消部分网盘筛选，或换个关键词再试。'
      return '换个关键词试试，或检查一下拼写。'
    },
  },
  watch: {
    '$route'(to) {
      if (to.path !== '/search') return
      this.applyRoute()
    },
    showPoster(v) { localStorage.setItem('dps-poster', v ? '1' : '0') },
  },
  mounted() {
    this.applyRoute()
  },
  beforeUnmount() {
    // 离开页面时中止未完成的搜索，避免继续写状态
    if (this._abortCtrl) this._abortCtrl.abort()
    clearInterval(this._elapsedTimer)
    clearTimeout(this._toastTimer)
  },
  methods: {
    // ===== 路由同步 =====
    applyRoute() {
      const q = this.$route.query
      const kw = String(q.ai || q.kw || '').trim()
      const link = String(q.link || '').trim()
      const disks = String(q.disk || '')
        .split(',').map(s => s.trim().toLowerCase()).filter(Boolean)
      const sort = SORTS.includes(String(q.sort || '')) ? String(q.sort) : 'relevance'

      if (link) {
        this.keyword = ''
        this.selectedDiskTypes = []
        this.sort = 'relevance'
        this.runLinkCheck(link)
        return
      }

      const keywordChanged = kw !== this.keyword
      const disksChanged = !sameArray(disks, this.selectedDiskTypes)
      const sortChanged = sort !== this.sort

      this.keyword = kw
      this.selectedDiskTypes = disks
      this.sort = sort

      if (!kw) {
        this.resetToIdle()
        return
      }

      if (q._t || keywordChanged) {
        this.page = 1
        this.doSearch()
        return
      }

      if (disksChanged || sortChanged) {
        // 只改网盘筛选/排序：后端已按关键词缓存完整结果池，经典接口
        // 命中缓存后是毫秒级。不清空当前列表，避免看起来像"重新搜索"。
        this.page = 1
        this.filtering = true
        this.fetchSearch(this.searchParams(1))
      }
    },
    // 回到未搜索态（不清 Hero 输入框，路由关键词由 HeroSearch 自行同步）
    resetToIdle() {
      if (this._abortCtrl) this._abortCtrl.abort()
      clearInterval(this._elapsedTimer)
      this.loading = false
      this.filtering = false
      this.searched = false
      this.gotResults = false
      this.results = []
      this.total = 0
      this.rawCount = 0
      this.hiddenDead = []
      this.hiddenDeadCount = 0
      this.linkCheck = null
      this.error = null
      this.correction = null
    },
    pushQuery(patch = {}) {
      const q = { ...this.$route.query }
      delete q._t
      delete q.link
      if (this.keyword) q.ai = this.keyword
      else delete q.ai
      delete q.kw
      if (patch.disk !== undefined) {
        if (patch.disk) q.disk = patch.disk
        else delete q.disk
      }
      if (patch.sort !== undefined) {
        if (patch.sort && patch.sort !== 'relevance') q.sort = patch.sort
        else delete q.sort
      }
      this.$router.push({ path: '/search', query: q })
    },
    setDisks(next) {
      const value = (next || []).join(',')
      if (sameArray(next || [], this.selectedDiskTypes)) return
      this.pushQuery({ disk: value })
    },
    setSort(value) {
      if (value === this.sort) return
      this.pushQuery({ sort: value })
    },
    // ===== 搜索 =====
    searchParams(page) {
      const p = {
        kw: this.keyword.trim(),
        page,
        page_size: this.pageSize,
        sort: this.sort,
      }
      if (this.selectedDiskTypes.length) p.disk_types = [...this.selectedDiskTypes]
      return p
    },
    async doSearch() {
      const kw = this.keyword.trim()
      if (!kw) return
      this.results = []
      this.posters = {}
      this.total = 0
      this.diskCounts = {}
      this.diskTypeCount = 0
      this.hiddenDead = []
      this.hiddenDeadCount = 0
      this.cached = false
      this.pansouError = null
      this.correction = null
      this.gotResults = false
      this.searched = true
      this.filtering = false
      // 第 1 页新搜索优先走流式：本地索引/实时首批渐进上屏；
      // 翻页与筛选变更仍走经典接口（结果已落缓存，够快）
      if (this.streamOk) {
        await this.fetchStreamSearch(this.searchParams(1))
      } else {
        await this.fetchSearch(this.searchParams(1))
      }
    },
    // 把一帧响应应用到页面状态（经典与流式共用同一结构）
    applyFrame(data, params) {
      this.results = data.results || []
      // 把本地已拿到的检测结论补回新一批卡片（筛选/翻页复用同一批 URL 时
      // 无需再发 check/batch）
      this.applyValidityCache()
      this.total = data.total || 0
      this.rawCount = data.raw_count || 0
      this.page = data.page || 1
      this.totalPages = data.total_pages || 1
      this.cached = !!data.cached
      this.sort = SORTS.includes(data.sort) ? data.sort : (params ? params.sort : this.sort)
      this.elapsedMs = Number(data.elapsed_ms) || (performance.now() - this._searchStartedAt)
      this.diskCounts = data.disk_counts || {}
      this.diskTypeCount = data.disk_type_count || 0
      this.filtered = !!data.filtered
      this.pansouError = data.pansou_error || null
      this.correction = data.correction || null
      this.hiddenDead = data.hidden_dead || []
      this.hiddenDeadCount = data.hidden_dead_count || 0
      this.gotResults = this.results.length > 0 || this.hiddenDeadCount > 0
      this.loadPosters()
    },
    async fetchStreamSearch(params) {
      // 中止上一次未完成的搜索（经典/流式共用同一个控制器）
      if (this._abortCtrl) this._abortCtrl.abort()
      const ctrl = new AbortController()
      this._abortCtrl = ctrl
      this.loading = true
      this.error = null
      this.linkCheck = null
      this.streaming = true
      this._searchStartedAt = performance.now()
      this.elapsedMs = 0
      clearInterval(this._elapsedTimer)
      this._elapsedTimer = setInterval(() => {
        this.elapsedMs = performance.now() - this._searchStartedAt
      }, 250)
      try {
        const resp = await fetch(`${API}/search/stream`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(params),
          signal: ctrl.signal,
        })
        if (!resp.ok) throw new Error('HTTP ' + resp.status)
        if (!resp.body || !resp.body.getReader) throw new Error('浏览器不支持流式接收')
        const reader = resp.body.getReader()
        const decoder = new TextDecoder()
        let buf = ''
        let sawDone = false
        let lastFrameAt = 0
        // 看门狗：某些 WebView 会悄悄"冻住"fetch 流（帧不再到达也不报错）。
        // 20 秒无新帧才放弃等待——零结果纠错会先做一次片段搜索
        // 和一次纠正后搜索，期间可能短暂没有新帧。流式保活每 5s
        // 会重置计时；真冻住时才回退经典接口（缓存命中毫秒级）。
        const DOG_MS = 20000
        const _bark = () => { ctrl._dogAbort = true; try { ctrl.abort() } catch (e) {} }
        let dog = setTimeout(_bark, DOG_MS)
        const feedDog = () => {
          clearTimeout(dog)
          dog = setTimeout(_bark, DOG_MS)
        }
        try {
        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          buf += decoder.decode(value, { stream: true })
          let idx
          while ((idx = buf.indexOf('\n\n')) >= 0) {
            const chunk = buf.slice(0, idx)
            buf = buf.slice(idx + 2)
            const line = chunk.split('\n').find(l => l.startsWith('data: '))
            if (!line) { feedDog(); continue }   // keep-alive 注释行等
            let data
            try { data = JSON.parse(line.slice(6)) } catch (e) { continue }
            if (data.stage === 'error') throw new Error(data.error || '流式搜索失败')
            // 已被更新的搜索接管：停止消费旧流，避免旧帧覆盖新结果
            if (ctrl !== this._abortCtrl) break
            // 节流渲染：中间帧至少间隔 300ms 才替换列表，避免每帧整页重排
            const now = performance.now()
            if (!data.done && lastFrameAt && now - lastFrameAt < 300) { feedDog(); continue }
            lastFrameAt = now
            feedDog()
            this.applyFrame(data, params)
            if (data.done) {
              sawDone = true
              this.showHidden = false
              // 只在完成帧送检一次：中间帧逐帧送检会把 check/batch 打成风暴
              this.smartCheck()
            }
          }
        }
        if (!sawDone) throw new Error('连接中断，未收到完成帧')
        // ★ 收到 done 帧：流式正常收尾。loading 必须在这里清——
        //   fetchStreamSearch 不走 fetchSearch 的 finally，漏清会让
        //   状态条永远停在"搜索中…"（计时器也要停）。
        clearInterval(this._elapsedTimer)
        if (ctrl === this._abortCtrl && !ctrl.signal.aborted) {
          this.loading = false
          this.searched = true
        }
        } finally {
          clearTimeout(dog)
        }
      } catch (e) {
        // 看门狗放弃等待：WebView 冻住了流（帧不再到达）。这与用户取消不同——
        // 管线多半已完成、结果已落缓存，改走经典接口拿最终结果。
        const dogAbort = ctrl._dogAbort === true
        // 部分浏览器在 abort 时抛的是 TypeError("Failed to fetch") 而非
        // AbortError；一律以 signal.aborted 为准，避免把取消误判成网络故障。
        const aborted = !!ctrl.signal.aborted || (e && e.name === 'AbortError')
        if (aborted) {
          if (dogAbort && ctrl === this._abortCtrl) {
            this.streaming = false
            await this.fetchSearch(params)
            return
          }
          // 用户主动取消 / 已被更新的搜索接管：静默退出
          if (ctrl !== this._abortCtrl) return
          clearInterval(this._elapsedTimer)
          this.loading = false
          this.streaming = false
          return
        }
        // ★ 过期请求（已被新搜索接管）的失败绝不能回退经典接口：
        //   fetchSearch 会 abort 当前控制器，连点筛选时会互杀，
        //   最终把 "Failed to fetch" 写到页面上。
        if (ctrl !== this._abortCtrl || ctrl.signal.aborted) return
        // 其他失败（404 / 不支持流式 / 中断无 done 帧）：本次会话停用流式，
        // 回退经典接口。已渐进渲染的部分结果保持显示，随后被经典响应替换。
        this.streamOk = false
        await this.fetchSearch(params)
      } finally {
        // 归还流式标志：只由当前搜索的控制器归还，过期请求不能改新搜索的状态
        if (ctrl === this._abortCtrl) {
          this.streaming = false
        }
      }
    },
    async fetchSearch(params, retry = 3) {
      // 中止上一次未完成的请求（翻页快速点击 / 连续搜索）
      if (this._abortCtrl) this._abortCtrl.abort()
      const ctrl = new AbortController()
      this._abortCtrl = ctrl
      this.loading = true
      this.streaming = false
      this.error = null
      this.linkCheck = null
      this._searchStartedAt = performance.now()
      this.elapsedMs = 0
      clearInterval(this._elapsedTimer)
      this._elapsedTimer = setInterval(() => {
        this.elapsedMs = performance.now() - this._searchStartedAt
      }, 250)
      try {
        const resp = await fetch(`${API}/search`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(params),
          signal: ctrl.signal,
        })
        const data = await resp.json()
        // 返回时已被更新的搜索接管：丢弃这份旧响应
        if (ctrl !== this._abortCtrl || ctrl.signal.aborted) return
        if (data.error) {
          this.error = data.error
        } else {
          this.applyFrame(data, params)
          this.showHidden = false
          this.smartCheck()
        }
      } catch (e) {
        const aborted = !!ctrl.signal.aborted || (e && e.name === 'AbortError')
        if (aborted) {
          // 用户主动取消 / 已被下一次搜索接管：静默退出
          if (ctrl !== this._abortCtrl) return
          clearInterval(this._elapsedTimer)
          this.loading = false
          return
        }
        // ★ 过期请求的失败（含被 abort 时浏览器报的 TypeError）一律丢弃，
        //   否则会把 "Failed to fetch" 写到当前页面上。
        if (ctrl !== this._abortCtrl || ctrl.signal.aborted) return
        // 瞬时网络抖动 / 上游繁忙：退避重试（高延迟 VPN 下多给两次机会）
        if (retry > 0) {
          clearInterval(this._elapsedTimer)
          await new Promise(r => setTimeout(r, 400 * (4 - retry)))
          if (ctrl === this._abortCtrl && !ctrl.signal.aborted) {
            return this.fetchSearch(params, retry - 1)
          }
          return
        }
        this.error = '请求失败：' + e.message
      } finally {
        // 只清理"自己的"计时器：过期请求的 finally 不能停掉新请求的计时
        if (ctrl === this._abortCtrl && !ctrl.signal.aborted) {
          clearInterval(this._elapsedTimer)
          this.loading = false
          this.searched = true
          this.filtering = false
        }
      }
    },
    // 状态卡 ×：取消正在进行的搜索，回到未搜索态
    cancelSearch() {
      if (this._abortCtrl) this._abortCtrl.abort()
      clearInterval(this._elapsedTimer)
      this.loading = false
      this.filtering = false
      this.searched = false
      this.gotResults = false
      this.error = null
      this.results = []
      this.total = 0
      this.rawCount = 0
      this.hiddenDead = []
      this.hiddenDeadCount = 0
      this.diskCounts = {}
      this.diskTypeCount = 0
      this.posters = {}
      this.correction = null
      this.pansouError = null
      // 输入框保留关键词并重新聚焦
      this.$nextTick(() => {
        this.$refs.heroSearch && this.$refs.heroSearch.focus()
      })
    },
    retrySearch() {
      if (this.keyword.trim()) this.doSearch()
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
    // Hero 搜索提交：关键词走 ai，网盘链接走 link 直检
    goSearch(value) {
      const v = String(value || '').trim()
      if (!v) return
      const query = isPanLink(v) ? { link: v } : { ai: v }
      const same = this.$route.path === '/search' &&
        (this.$route.query.ai || '') === (query.ai || '') &&
        (this.$route.query.link || '') === (query.link || '')
      this.$router.push({
        path: '/search',
        query: same ? { ...query, _t: String(Date.now()) } : query,
      })
    },
    searchOriginal() {
      if (!this.correction) return
      this.$router.push({ path: '/search', query: { ai: this.correction.from } })
    },
    // ===== 粘贴网盘链接直接检测 =====
    async runLinkCheck(link) {
      if (this._abortCtrl) this._abortCtrl.abort()
      this.searched = true
      this.loading = false
      this.gotResults = false
      this.results = []
      this.hiddenDead = []
      this.hiddenDeadCount = 0
      this.diskCounts = {}
      this.diskTypeCount = 0
      this.total = 0
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
    // ===== 有效性检测 =====
    checkable(it) {
      if (!it || !/^https?:\/\//i.test(it.url || '')) return false
      const t = (it.disk_type || '').toLowerCase()
      return !['magnet', 'ed2k', 'others', ''].includes(t)
    },
    async smartCheck() {
      if (this.checking || !this.results.length) return
      const checkedAt = this._checkedAt || (this._checkedAt = new Map())
      const validityCache = this._validityCache || (this._validityCache = new Map())
      const now = Date.now()
      const FRESH_MS = 5 * 60 * 1000
      const seen = new Set()
      const items = []
      const push = (it) => {
        if (!it || seen.has(it.url) || !this.checkable(it)) return
        seen.add(it.url)
        // 同一 URL 5 分钟内不重复送检：筛选/翻页/流式帧会反复渲染同一批卡片，
        // 不设门槛会把 check/batch 打成请求风暴，拖垮后端。
        if (now - (checkedAt.get(it.url) || 0) < FRESH_MS) return
        items.push({ url: it.url, disk_type: it.disk_type || '', password: it.password || '' })
      }
      this.results.forEach(r => {
        push(r)
        ;(r.variants || []).slice(0, 3).forEach(push)
      })
      if (!items.length) {
        this.applyValidityCache()
        return
      }
      this.checking = true
      try {
        const resp = await fetch(`${API}/check/batch`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ items: items.slice(0, 30) }),
        })
        const data = await resp.json()
        const res = data.results || {}
        // 本地留存检测结论：之后筛选/翻页重新渲染同一批卡片时能直接补徽章
        for (const url of Object.keys(res)) {
          const st = res[url]
          if (st && st.state !== 'unsupported') validityCache.set(url, st)
        }
        for (const it of items) checkedAt.set(it.url, now)
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
      } catch (e) { /* 检测失败不打扰结果展示；不记 checkedAt，下次可重试 */ }
      this.checking = false
    },
    // 用本地检测缓存补齐徽章（不发请求）
    applyValidityCache() {
      const cache = this._validityCache
      if (!cache || !cache.size) return
      const apply = (it) => {
        const st = cache.get(it.url)
        if (st && st.state !== 'unsupported') {
          it.validity = st.validity || ''
          it.checked_at = st.checked_at || it.checked_at || ''
          it.state_summary = st.summary || it.state_summary || ''
        }
      }
      for (const r of this.results) {
        apply(r)
        if (r.variants) r.variants.forEach(apply)
      }
    },
    repositionDead() {
      const dead = this.results.filter(r => cardDead(r))
      if (!dead.length) return
      const rest = this.results.filter(r => !cardDead(r))
      if (this.hideDead) {
        this.hiddenDead = [...dead, ...this.hiddenDead]
        this.hiddenDeadCount += dead.length
        this.results = rest
        this.total = Math.max(0, this.total - dead.length)
        for (const card of dead) {
          const dt = String(card.disk_type || '').toLowerCase() || 'others'
          if (this.diskCounts[dt] > 0) this.diskCounts[dt] -= 1
        }
        this.diskTypeCount = new Set(
          rest.map(c => String(c.disk_type || '').toLowerCase() || 'others')
        ).size
      } else {
        this.results = [...rest, ...dead]
      }
    },
    // ===== 封面 =====
    async loadPosters() {
      if (!this.showPoster || !this.results.length) return
      // 流式期间每帧都会调用：1.5s 内不重复请求（服务端有缓存，仍省一次往返）
      const now = performance.now()
      if (this._lastPosterAt && now - this._lastPosterAt < 1500) return
      this._lastPosterAt = now
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
          .catch(() => { /* 封面失败静默兜底 */ })
      }
    },
    // ===== 举报 =====
    async reportDead(item) {
      if (!item || item.dead_marked) return
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
        this.showToast('举报失败：' + e.message)
      }
    },
    // ===== 弹窗/提示 =====
    openDialog(payload) {
      this.dialog = { open: true, card: payload.card, item: payload.item }
    },
    closeDialog() {
      this.dialog = { open: false, card: null, item: null }
    },
    onDialogReport(item) {
      this.reportDead(item)
      this.closeDialog()
    },
    showToast(msg) {
      this.toast = msg
      clearTimeout(this._toastTimer)
      this._toastTimer = setTimeout(() => { this.toast = '' }, 1800)
    },
    getDiskLabel(t) { return DISK_LABELS[t] || t },
  },
}
</script>

<template>
  <div class="page search-page">
    <!-- ===== 居中 Hero：品牌 + 提示 + 搜索框 + 状态卡/直检卡 ===== -->
    <section class="search-hero" :class="{ compact: heroCompact }">
      <div class="shero-brand" v-if="!heroCompact">
        <img class="shero-logo" :src="site.logo || '/favicon.svg'" alt="" />
        <h1 class="shero-name">{{ site.name || 'SuPanSou' }}</h1>
      </div>
      <div class="shero-brand shero-brand-compact" v-else>
        <img class="shero-logo shero-logo-compact" :src="site.logo || '/favicon.svg'" alt="" />
        <h1 class="shero-name shero-name-compact">{{ site.name || 'SuPanSou' }}</h1>
      </div>

      <p class="shero-tip" :class="{ 'shero-tip-compact': heroCompact }">{{ heroTip }}</p>

      <HeroSearch ref="heroSearch" :initial="heroInitial" :auto-focus="!searched && !linkCheck"
                  placeholder="搜关键词，或粘贴网盘链接直接检测…"
                  @search="goSearch" />

      <!-- 检索状态卡：仅"还没有任何结果"时整卡显示；流式首批到达后隐藏，改由状态条+结果区渐进呈现 -->
      <div class="searching-card" v-if="loading && !heroCompact" aria-live="polite">
        <span class="sgc-word">全网检索中<span class="sgc-dots" aria-hidden="true"></span></span>
        <span class="sgc-item">已用时 <b>{{ seconds }}</b>s</span>
        <span class="sgc-item">已收到 <b>{{ received }}</b> 条</span>
        <button class="sgc-close" type="button" aria-label="取消搜索" title="取消搜索"
                @click="cancelSearch">×</button>
      </div>

      <!-- 粘贴链接直检结果卡：与状态卡同一位置、同族样式 -->
      <div class="link-check-card" v-else-if="linkCheck">
        <span class="lc-url">{{ linkCheck.url }}</span>
        <span class="lc-text" :class="'lc-' + (linkCheck.validity || linkCheck.state)">
          {{ linkCheckText() }}
        </span>
        <a v-if="/^https?:\/\//i.test(linkCheck.url)" class="btn primary sm"
           :href="linkCheck.url" target="_blank" rel="noopener">仍要打开</a>
      </div>

      <!-- 搜索失败/无结果：保持居中 Hero，反馈显示在搜索框正下方 -->
      <template v-else-if="!heroCompact && searched">
        <div class="shero-feedback" v-if="error">
          <div class="error-msg">{{ error }}
            <button v-if="keyword" class="btn xs" style="margin-left:8px"
                    @click="retrySearch">重试</button>
          </div>
        </div>
        <div class="shero-feedback" v-else-if="results.length === 0 && hiddenDeadCount === 0">
          <EmptyState :title="emptyTitle" :text="emptyText"
                      :show-demand="!selectedDiskTypes.length"
                      :retry="!!error" @retry="retrySearch" />
          <div v-if="selectedDiskTypes.length" class="empty-clear">
            <button class="btn" @click="setDisks([])">清除网盘筛选</button>
          </div>
        </div>
      </template>
    </section>

    <!-- 纠错 -->
    <div class="correction-bar" v-if="searched && correction">
      没找到「<b>{{ correction.from }}</b>」，您是不是要找：<b>{{ correction.to }}</b>？
      <span class="correction-note">已显示该片名的结果</span>
      <a href="#" class="correction-link" @click.prevent="searchOriginal">仍然搜索原词「{{ correction.from }}」</a>
    </div>

    <!-- 状态条：有部分结果后常驻；loading（流式/回退/翻页）时显示"搜索中…" -->
    <SearchStatusBar v-if="searched && heroCompact"
                     :loading="loading"
                     :filtering="filtering"
                     :elapsed-ms="elapsedMs"
                     :total="total"
                     :disk-type-count="diskTypeCount"
                     :cached="cached"
                     :hidden-dead-count="hiddenDeadCount"
                     :checking="checking"
                     :checked-known="checkStats.known"
                     :checked-total="checkStats.total" />

    <!-- 结果区（1040px 内容宽度）：一旦有（部分）结果就常驻渲染，
         流式渐进/回退等待/翻页加载都不清空列表 -->
    <div v-if="heroCompact" class="search-results">
      <div class="error-msg" v-if="error" style="margin-bottom:12px;">
        {{ error }}
        <button class="btn xs" style="margin-left:8px" @click="retrySearch">重试</button>
      </div>

      <div class="error-msg" v-if="pansouError" style="margin-bottom:12px;">
        ⚠ 实时搜索不可用：{{ pansouError }}<br>
        <span style="font-size:12px;">仅显示历史索引结果</span>
      </div>

      <!-- 筛选 + 排序 -->
      <div class="search-toolbar">
        <DiskFilterChips :counts="diskCounts" :selected="selectedDiskTypes"
                         @update:selected="setDisks" />
        <div class="toolbar-right">
          <SortSelect :model-value="sort" @update:model-value="setSort" />
          <label class="toggle-label" title="自动隐藏全部链接已确认失效的资源卡">
            <input type="checkbox" v-model="hideDead"> 隐藏失效
          </label>
          <label class="toggle-label" title="搜索结果卡片显示豆瓣封面图">
            <input type="checkbox" v-model="showPoster"> 封面图
          </label>
        </div>
      </div>

      <div class="hidden-dead-bar" v-if="hiddenDeadCount > 0">
        🩺 智能检测已确认 {{ hiddenDeadCount }} 条资源失效并自动降权/隐藏
        <a @click.prevent="showHidden = !showHidden">{{ (!hideDead || showHidden) ? '收起' : '查看' }}</a>
      </div>

      <ResourceCard v-for="(r, i) in displayList" :key="(r.url || '') + '|' + i"
                    :item="r" :show-poster="showPoster"
                    :poster="posters[r.title] || ''"
                    :keyword="keyword.trim()"
                    @report="reportDead" @toast="showToast" @open="openDialog" />

      <!-- 翻页 -->
      <div class="pager" v-if="totalPages > 1">
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

    <ResourceDialog :open="dialog.open" :card="dialog.card" :item="dialog.item"
                    :keyword="keyword" @close="closeDialog" @toast="showToast"
                    @report="onDialogReport" />

    <div class="toast" v-if="toast">{{ toast }}</div>
  </div>
</template>
