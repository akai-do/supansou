<script>
// 豆瓣榜单 v2：11 分类富信息榜单（v2 数据源），排序 + 滚动加载
// 卡片：大海报 + 评分/口碑角标 + card_subtitle 简介 + 豆瓣详情/搜资源
import PosterImg from '../components/PosterImg.vue'
import { API } from '../constants.js'

const PAGE_SIZE = 25

export default {
  components: { PosterImg },
  data() {
    return {
      tabs: [
        { key: 'movie_hot', label: '热门电影' },
        { key: 'movie_latest', label: '最新电影' },
        { key: 'movie_gems', label: '冷门佳片' },
        { key: 'tv_hot', label: '热门电视剧' },
        { key: 'tv_domestic', label: '热门国产剧' },
        { key: 'tv_american', label: '热门欧美剧' },
        { key: 'tv_korean', label: '热门韩剧' },
        { key: 'tv_japanese', label: '热门日剧' },
        { key: 'tv_animation', label: '热门动画' },
        { key: 'tv_variety_show', label: '热门综艺' },
        { key: 'tv_documentary', label: '热门纪录片' },
      ],
      sorts: [
        { key: 'default', label: '默认排序' },
        { key: 'rating', label: '按评分排序' },
        { key: 'year', label: '按上映年份' },
      ],
      active: 'movie_hot',
      sortMode: 'default',
      items: [],
      loading: false,
      loadingMore: false,
      hasMore: false,
      error: null,
      enabled: true,
      _observer: null,
    }
  },
  computed: {
    sortedItems() {
      const list = [...this.items]
      if (this.sortMode === 'rating') {
        list.sort((a, b) => (b.rating_value || 0) - (a.rating_value || 0))
      } else if (this.sortMode === 'year') {
        const year = (it) => parseInt((it.card_subtitle || it.year || '').slice(0, 4)) || 0
        list.sort((a, b) => year(b) - year(a))
      }
      return list
    },
  },
  watch: {
    active() { this.load() },
  },
  async mounted() {
    this.load()
    // 滚动触底自动加载（CloudSaver 式往下拉）
    await this.$nextTick()
    this._observer = new IntersectionObserver((entries) => {
      if (entries[0].isIntersecting && this.hasMore &&
          !this.loadingMore && !this.loading) this.loadMore()
    }, { rootMargin: '300px' })
    if (this.$refs.sentinel) this._observer.observe(this.$refs.sentinel)
  },
  beforeUnmount() {
    if (this._observer) this._observer.disconnect()
  },
  methods: {
    cur() { return this.tabs.find(x => x.key === this.active) || this.tabs[0] },
    async load() {
      const t = this.cur()
      this.loading = true
      this.error = null
      this.items = []
      this.hasMore = false
      try {
        const resp = await fetch(
          `${API}/douban/hot?collection=${t.key}&start=0&count=${PAGE_SIZE}`)
        const data = await resp.json()
        this.items = data.items || []
        this.enabled = data.enabled !== false
        this.hasMore = this.items.length >= PAGE_SIZE
        if (!this.items.length && data.error) this.error = data.error
      } catch (e) {
        this.error = '请求失败: ' + e.message
      }
      this.loading = false
    },
    async loadMore() {
      if (this.loadingMore || !this.hasMore) return
      this.loadingMore = true
      const t = this.cur()
      try {
        const resp = await fetch(
          `${API}/douban/hot?collection=${t.key}&start=${this.items.length}&count=${PAGE_SIZE}`)
        const data = await resp.json()
        const batch = data.items || []
        const seen = new Set(this.items.map(x => x.id || x.title))
        this.items = [...this.items, ...batch.filter(x => !seen.has(x.id || x.title))]
        this.hasMore = batch.length >= PAGE_SIZE
      } catch (e) {
        this.error = '加载更多失败: ' + e.message
      }
      this.loadingMore = false
    },
    // 评分 → 口碑档（CloudSaver 同款阈值）
    tier(score) {
      const v = Number(score) || 0
      if (v <= 0) return { label: '待评分', desc: '暂无公开评分' }
      if (v >= 8.5) return { label: '神作', desc: '口碑极佳' }
      if (v >= 7.5) return { label: '推荐', desc: '高分推荐' }
      if (v >= 6.5) return { label: '可看', desc: '整体稳定' }
      return { label: '一般', desc: '分数偏低' }
    },
    fmtCount(n) {
      n = Number(n) || 0
      if (n >= 10000) return (n / 10000).toFixed(1).replace(/\.0$/, '') + '万'
      return String(n)
    },
    yearOf(it) {
      return (it.card_subtitle || it.year || '').slice(0, 4) || '年份未知'
    },
    searchRes(title) {
      this.$router.push({ path: '/resource', query: { kw: title } })
    },
  },
}
</script>

<template>
  <div class="page">
    <div class="page-title">
      <h2>🎬 豆瓣榜单</h2>
      <p>点击海报或「搜资源」跳转资源页搜索；点「豆瓣详情」打开豆瓣条目页</p>
    </div>

    <!-- 排序 -->
    <div class="chips-row sort-row">
      <span class="sort-label">排序方式</span>
      <span class="chip" v-for="s in sorts" :key="s.key"
            :class="{ active: sortMode === s.key }"
            @click="sortMode = s.key">{{ s.label }}</span>
    </div>

    <!-- 分类 -->
    <div class="chips-row douban-tabs">
      <span class="chip" v-for="t in tabs" :key="t.key"
            :class="{ active: active === t.key }"
            @click="active = t.key">{{ t.label }}</span>
    </div>

    <!-- 骨架屏 -->
    <div v-if="loading" class="douban-grid">
      <div class="d-card skel" v-for="i in 12" :key="i">
        <div class="skel-poster lg"></div>
        <div class="d-info"><div class="skel-line w60"></div><div class="skel-line w80"></div></div>
      </div>
    </div>

    <div class="error-msg" v-if="!loading && error && !items.length">
      ⚠ 豆瓣榜单暂时不可用：{{ error }}<br>
      <span style="font-size:12px;">不影响资源搜索功能，可稍后重试</span>
    </div>
    <div class="error-msg" v-if="!loading && !enabled">
      豆瓣榜单功能未启用（服务端 ENABLE_DOUBAN=0）
    </div>
    <div class="empty-state" v-if="!loading && !error && enabled && !items.length">
      <div class="icon">📭</div>
      <p>该分类暂时没有内容</p>
    </div>

    <div class="douban-grid" v-if="!loading && sortedItems.length">
      <div class="d-card" v-for="m in sortedItems" :key="m.id || m.title">
        <div class="d-poster-wrap">
          <PosterImg class="d-poster" :src="m.pic_large || m.pic_normal || m.cover"
                     :alt="m.title" />
          <span class="d-score" v-if="m.rating_value > 0">{{ Number(m.rating_value).toFixed(1) }}/10</span>
          <span class="d-tier" v-if="tier(m.rating_value).label !== '待评分'">
            {{ tier(m.rating_value).label }}
          </span>
        </div>
        <div class="d-info">
          <div class="d-title" :title="m.title">{{ m.title }}</div>
          <div class="d-sub" :title="m.card_subtitle">{{ m.card_subtitle || '暂无简介信息' }}</div>
          <div class="d-meta">
            <template v-if="m.rating_count > 0">
              ★ {{ Number(m.rating_value).toFixed(1) }} · {{ fmtCount(m.rating_count) }} 人评分
            </template>
            <template v-else>暂无评分</template>
            <template v-if="m.rating_value > 0"> · {{ tier(m.rating_value).desc }}</template>
          </div>
        </div>
        <div class="d-btns">
          <a v-if="m.url" class="btn sm" :href="m.url" target="_blank" rel="noopener">豆瓣详情</a>
          <button class="btn primary sm" @click="searchRes(m.title)">搜资源</button>
        </div>
      </div>
    </div>

    <!-- 滚动触底哨兵 + 加载更多 -->
    <div ref="sentinel" class="more-row" v-if="!loading && items.length">
      <button v-if="hasMore" class="btn more-btn"
              :disabled="loadingMore" @click="loadMore">
        {{ loadingMore ? '加载中…' : '加载更多' }}
      </button>
      <span v-else class="more-end">没有更多了~</span>
    </div>
  </div>
</template>
