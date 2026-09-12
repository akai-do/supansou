<script>
const API = '/api'

export default {
  data() {
    return {
      loading: true,
      stats: null,
    }
  },
  methods: {
    async loadStats() {
      this.loading = true
      try {
        const resp = await fetch(`${API}/stats`)
        this.stats = await resp.json()
      } catch (e) {
        console.error(e)
      }
      this.loading = false
    },
    getDiskLabel(t) {
      const map = { baidu: '百度', quark: '夸克', aliyun: '阿里', xunlei: '迅雷', '115': '115', tianyi: '天翼', uc: 'UC', pikpak: 'PikPak', '123': '123', magnet: '磁力', ed2k: '电驴' }
      return map[t] || t
    },
  },
  mounted() { this.loadStats() },
}
</script>

<template>
  <div>
    <div class="header">
      <h1>📊 数据统计</h1>
      <p>索引库分析与系统运行状态</p>
    </div>

    <div v-if="loading" class="loading"><span class="spinner"></span>加载中...</div>

    <template v-if="!loading && stats">
      <!-- 索引概览 -->
      <div class="stats-grid">
        <div class="stat-card">
          <div class="num">{{ stats.index?.total || 0 }}</div>
          <div class="label">总计链接</div>
        </div>
        <div class="stat-card">
          <div class="num" style="color:var(--success)">{{ stats.index?.alive || 0 }}</div>
          <div class="label">有效链接</div>
        </div>
        <div class="stat-card">
          <div class="num" style="color:var(--danger)">{{ stats.index?.dead || 0 }}</div>
          <div class="label">失效链接</div>
        </div>
        <div class="stat-card">
          <div class="num" style="color:var(--primary)">
            {{ stats.analyzer?.health_overview?.alive_rate || 0 }}%
          </div>
          <div class="label">存活率</div>
        </div>
      </div>

      <!-- 运行状态 -->
      <div class="result-section" v-if="stats.harvester || stats.checker">
        <h3>⚙️ 运行状态</h3>
        <div class="stat-row" v-if="stats.harvester">
          <span>定时收割器</span>
          <span>
            <span class="status-badge" :class="stats.harvester.running ? 'status-running' : 'status-stopped'">
              <span class="dot" :class="stats.harvester.running ? 'dot-running' : 'dot-stopped'"></span>
              {{ stats.harvester.running ? '运行中' : '已停止' }}
            </span>
            <span style="margin-left:12px;color:var(--text-secondary);font-size:12px;">
              {{ stats.harvester.keywords || 0 }} 关键词 · 每 {{ stats.harvester.interval_hours || 0 }}h
            </span>
          </span>
        </div>
        <div class="stat-row" v-if="stats.checker">
          <span>链接巡检器</span>
          <span>
            <span class="status-badge status-running">
              <span class="dot dot-running"></span>
              运行中
            </span>
            <span style="margin-left:12px;color:var(--text-secondary);font-size:12px;">
              已检测 {{ stats.checker.total_checks || 0 }} 条
            </span>
          </span>
        </div>
      </div>

      <!-- 网盘分布 -->
      <div class="result-section" v-if="stats.analyzer?.disk_distribution">
        <h3>💾 网盘分布</h3>
        <div class="stat-row" v-for="item in stats.analyzer.disk_distribution" :key="item.type">
          <span>{{ getDiskLabel(item.type) || item.type }}</span>
          <span>
            <strong>{{ item.count }}</strong>
            <span style="color:var(--text-secondary);font-size:12px;margin-left:4px;">
              ({{ item.percentage }}%)
            </span>
          </span>
        </div>
      </div>

      <!-- 来源分布 -->
      <div class="result-section" v-if="stats.analyzer?.source_distribution">
        <h3>📡 数据来源分布</h3>
        <div class="stat-row" v-for="item in stats.analyzer.source_distribution" :key="item.source_group">
          <span>{{ item.source_group }}</span>
          <span><strong>{{ item.count }}</strong> 条</span>
        </div>
      </div>

      <!-- 搜索热词 -->
      <div class="result-section" v-if="stats.index?.hot_keywords?.length">
        <h3>🔥 热搜资源</h3>
        <div class="stat-row" v-for="item in stats.index.hot_keywords.slice(0, 15)" :key="item.title">
          <span>{{ item.title }}</span>
          <span>{{ item.count }} 次搜索</span>
        </div>
      </div>
    </template>
  </div>
</template>