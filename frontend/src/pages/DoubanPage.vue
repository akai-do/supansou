<script>
// 豆瓣榜单：热门电影/剧集/综艺 → 点卡片一键搜索资源
import PosterImg from '../components/PosterImg.vue'
import { API } from '../constants.js'

export default {
  components: { PosterImg },
  data() {
    return {
      tabs: [
        { key: 'movie', label: '热门电影', type: 'movie', tag: '热门' },
        { key: 'tv', label: '热门剧集', type: 'tv', tag: '热门' },
        { key: 'show', label: '热门综艺', type: 'tv', tag: '综艺' },
      ],
      active: 'movie',
      items: [],
      loading: false,
      error: null,
      enabled: true,
    }
  },
  watch: { active() { this.load() } },
  mounted() { this.load() },
  methods: {
    async load() {
      const t = this.tabs.find(x => x.key === this.active)
      this.loading = true
      this.error = null
      this.items = []
      try {
        const resp = await fetch(
          `${API}/douban/hot?type=${t.type}&tag=${encodeURIComponent(t.tag)}`)
        const data = await resp.json()
        this.items = data.items || []
        this.enabled = data.enabled !== false
        if (!this.items.length && data.error) this.error = data.error
      } catch (e) {
        this.error = '请求失败: ' + e.message
      }
      this.loading = false
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
      <p>热门影视一键转资源搜索（结果来自豆瓣实时热榜）</p>
    </div>

    <div class="chips-row douban-tabs">
      <span class="chip" v-for="t in tabs" :key="t.key"
            :class="{ active: active === t.key }"
            @click="active = t.key">{{ t.label }}</span>
    </div>

    <!-- 骨架屏 -->
    <div v-if="loading" class="douban-grid">
      <div class="d-card skel" v-for="i in 10" :key="i">
        <div class="skel-poster lg"></div>
        <div class="d-info"><div class="skel-line w60"></div></div>
      </div>
    </div>

    <div class="error-msg" v-if="!loading && error && !items.length">
      ⚠ 豆瓣榜单暂时不可用：{{ error }}<br>
      <span style="font-size:12px;">不影响资源搜索功能，可稍后重试</span>
    </div>
    <div class="error-msg" v-if="!loading && !enabled">
      豆瓣榜单功能未启用（服务端 ENABLE_DOUBAN=0）
    </div>

    <div class="douban-grid" v-if="!loading && items.length">
      <div class="d-card" v-for="m in items" :key="m.url || m.title"
           @click="searchRes(m.title)">
        <PosterImg class="d-poster" :src="m.cover" :alt="m.title" />
        <div class="d-info">
          <div class="d-title" :title="m.title">{{ m.title }}</div>
          <div class="d-rate" v-if="m.rate">★ {{ m.rate }}</div>
        </div>
        <button class="btn primary sm d-btn" @click.stop="searchRes(m.title)">搜索资源</button>
      </div>
    </div>
  </div>
</template>
