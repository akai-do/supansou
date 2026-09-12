<script>
const API = '/api'

export default {
  data() {
    return {
      keyword: '',
      loading: false,
      results: [],
      diskType: '',
      onlyAlive: true,
      diskTypeOptions: ['', 'baidu', 'quark', 'aliyun', 'xunlei', '115', 'tianyi', 'uc', 'pikpak', '123', 'magnet'],
    }
  },
  methods: {
    async loadArchive() {
      this.loading = true
      try {
        const params = new URLSearchParams()
        if (this.keyword.trim()) params.set('kw', this.keyword.trim())
        if (this.diskType) params.set('disk_type', this.diskType)
        params.set('only_alive', this.onlyAlive ? 'true' : 'false')
        params.set('page_size', '100')

        const resp = await fetch(`${API}/archive?${params}`)
        const data = await resp.json()
        if (data.error) {
          console.error(data.error)
          this.results = []
        } else {
          this.results = data.results || []
        }
      } catch (e) {
        console.error(e)
        this.results = []
      }
      this.loading = false
    },
    handleKeydown(e) {
      if (e.key === 'Enter') this.loadArchive()
    },
    getDiskLabel(t) {
      const map = { baidu: '百度', quark: '夸克', aliyun: '阿里', xunlei: '迅雷', '115': '115', tianyi: '天翼', uc: 'UC', pikpak: 'PikPak', '123': '123', magnet: '磁力', ed2k: '电驴' }
      return map[t] || t
    },
  },
  mounted() { this.loadArchive() },
}
</script>

<template>
  <div>
    <div class="header">
      <h1>📚 历史索引</h1>
      <p>浏览本地索引库中累积的网盘资源链接</p>
    </div>

    <div class="archive-controls">
      <input v-model="keyword" placeholder="在索引中搜索..." @keydown="handleKeydown" />
      <select v-model="diskType">
        <option value="">全部网盘</option>
        <option v-for="t in diskTypeOptions.slice(1)" :key="t" :value="t">{{ getDiskLabel(t) }}</option>
      </select>
      <label class="toggle-label">
        <input type="checkbox" v-model="onlyAlive" />
        仅有效链接
      </label>
      <button @click="loadArchive" :disabled="loading">
        <span v-if="loading" class="spinner"></span>
        {{ loading ? '加载中' : '查询' }}
      </button>
    </div>

    <div v-if="loading" class="loading"><span class="spinner"></span>加载中...</div>

    <div v-if="!loading && results.length === 0" class="empty-state">
      <div class="icon">📭</div>
      <p>索引库暂无数据，快去搜点资源吧！</p>
    </div>

    <div class="result-card" v-for="(r, i) in results" :key="i">
      <div class="title">{{ r.title || '(无标题)' }}</div>
      <div class="url-row">
        <a :href="r.url" target="_blank" rel="noopener">{{ r.url }}</a>
        <span v-if="r.password">🔑 {{ r.password }}</span>
      </div>
      <div class="meta">
        <span class="badge badge-disk">{{ getDiskLabel(r.disk_type) }}</span>
        <span class="badge" :class="r.alive ? 'badge-alive' : 'badge-dead'">
          {{ r.alive ? '有效' : '失效' }}
        </span>
        <span class="badge badge-source">{{ r.source }}</span>
        <span style="font-size:11px;color:var(--text-secondary)">
          收录: {{ r.first_seen ? r.first_seen.substring(0,10) : '' }}
        </span>
        <span style="font-size:11px;color:var(--text-secondary)">
          搜到 {{ r.search_cnt || 1 }} 次
        </span>
      </div>
    </div>
  </div>
</template>