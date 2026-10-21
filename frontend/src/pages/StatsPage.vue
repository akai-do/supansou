<script>
import { API, DISK_LABELS } from '../constants.js'

export default {
  data() {
    return {
      loading: true,
      stats: null,
      sourceStats: null,
      sourceLoading: false,
      days: 30,
      category: 'all',
      dayOptions: [
        { value: 7, label: '最近 7 天' },
        { value: 30, label: '最近 30 天' },
        { value: 90, label: '最近 90 天' },
        { value: 0, label: '全部时间' },
      ],
      categoryOptions: [
        { value: 'all', label: '全部类型' },
        { value: 'movie', label: '影视' },
        { value: 'study', label: '学习' },
        { value: 'software', label: '软件' },
        { value: 'music', label: '音乐' },
      ],
    }
  },
  watch: {
    days() { this.loadSources() },
    category() { this.loadSources() },
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
    async loadSources(force = false) {
      this.sourceLoading = true
      try {
        const suffix = force ? '&refresh=1' : ''
        const resp = await fetch(
          `${API}/stats/sources?days=${this.days}&category=${this.category}${suffix}`
        )
        this.sourceStats = await resp.json()
      } catch (e) {
        console.error(e)
      }
      this.sourceLoading = false
    },
    getDiskLabel(t) { return DISK_LABELS[t] || t },
    num(v) {
      if (v === null || v === undefined || v === '') return '0'
      return Number(v).toLocaleString('zh-CN')
    },
    pct(v, digits = 1) {
      if (v === null || v === undefined || v === '') return '—'
      return `${Number(v).toFixed(digits)}%`
    },
    dt(v) {
      if (!v) return '—'
      return String(v).replace('T', ' ').slice(0, 16)
    },
    sourceName(s) { return s || '（未知来源）' },
    score(v) {
      if (v === null || v === undefined) return '—'
      return Math.round(Number(v) * 100)
    },
    suggestionClass(s) {
      if (s === '优先保留') return 'sr-keep'
      if (s === '建议降权') return 'sr-down'
      if (s === '样本不足') return 'sr-low'
      return 'sr-watch'
    },
  },
  mounted() {
    this.loadStats()
    this.loadSources()
  },
}
</script>

<template>
  <div class="page">
    <div class="page-title">
      <h2>数据统计</h2>
      <p>索引库分析、来源质量与系统运行状态</p>
    </div>

    <div v-if="loading" class="loading"><span class="spinner"></span>加载中...</div>

    <template v-if="!loading && stats">
      <!-- 索引概览：只认 validity 四态；未检测单独列出 -->
      <div class="stats-grid">
        <div class="stat-card">
          <div class="num">{{ num(stats.index?.total) }}</div>
          <div class="label">总计链接</div>
        </div>
        <div class="stat-card">
          <div class="num" style="color:var(--primary)">{{ num(stats.index?.checked) }}</div>
          <div class="label">已检测（{{ pct(stats.index?.checked_rate) }}）</div>
        </div>
        <div class="stat-card">
          <div class="num" style="color:var(--danger)">{{ num(stats.index?.dead) }}</div>
          <div class="label">确认失效</div>
        </div>
        <div class="stat-card">
          <div class="num" style="color:var(--text-secondary)">{{ num(stats.index?.untested) }}</div>
          <div class="label">未检测</div>
        </div>
      </div>
      <p class="stats-note">
        死链率 = 确认失效 / 已检测（{{ pct(stats.index?.dead_rate) }}）；
        未检测不计入分母。有效 {{ num(stats.index?.ok) }}，
        疑似失效 {{ num(stats.index?.suspect) }}。
      </p>

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

      <!-- 来源质量表：按来源展开，所有比例带分母/样本量 -->
      <div class="result-section source-section">
        <div class="source-head">
          <div>
            <h3>来源质量</h3>
            <p class="source-sub">
              每行一个来源；未检测或样本不足的来源灰显，不参与排名建议。
            </p>
          </div>
          <div class="source-toolbar">
            <select v-model.number="days" aria-label="统计窗口">
              <option v-for="opt in dayOptions" :key="opt.value" :value="opt.value">
                {{ opt.label }}
              </option>
            </select>
            <select v-model="category" aria-label="查询类型">
              <option v-for="opt in categoryOptions" :key="opt.value" :value="opt.value">
                {{ opt.label }}
              </option>
            </select>
            <button class="btn xs" :disabled="sourceLoading" @click="loadSources(true)">刷新</button>
          </div>
        </div>

        <div v-if="sourceLoading" class="loading">
          <span class="spinner"></span>来源统计加载中...
        </div>
        <template v-else-if="sourceStats">
          <div class="source-summary">
            <span>窗口：{{ sourceStats.days === 0 ? '全部时间' : `最近 ${sourceStats.days} 天` }}</span>
            <span>类型：{{ sourceStats.category_label }}</span>
            <span>已检测：{{ num(sourceStats.totals?.checked) }} / {{ num(sourceStats.totals?.total) }}</span>
            <span>死链率：{{ pct(sourceStats.totals?.dead_rate) }}</span>
            <span>曝光：{{ num(sourceStats.totals?.impressions) }}</span>
            <span>点击：{{ num(sourceStats.totals?.clicks) }}</span>
            <span>CTR：{{ pct(sourceStats.totals?.ctr, 2) }}</span>
            <span>数据截止：{{ dt(sourceStats.generated_at) }}</span>
            <span v-if="sourceStats.stats_since">曝光起始：{{ sourceStats.stats_since }}</span>
          </div>

          <div class="source-table-wrap">
            <table class="source-table">
              <thead>
                <tr>
                  <th>来源</th>
                  <th>建议</th>
                  <th title="可用率 × 独家率 × 点击权重；样本不足不评分">评分</th>
                  <th title="该来源在索引中的链接数">收录</th>
                  <th title="有 validity 四态结论的链接数 / 收录数">已检测</th>
                  <th title="确认失效 / 已检测">死链率</th>
                  <th title="疑似失效 / 已检测">疑似率</th>
                  <th title="该来源独有的归一化标题 / 该来源标题数">独家率</th>
                  <th title="窗口内结果页展示的卡片数">曝光</th>
                  <th title="窗口内点击次数">点击</th>
                  <th title="窗口内点击 / 曝光">CTR</th>
                  <th :title="sourceStats.click_basis === 'all_time' ? '全量点击 / 全量总点击' : '窗口内该类别点击 / 窗口内总点击'">点击占比</th>
                  <th :title="sourceStats.click_basis === 'all_time' ? '全量点击 / 收录数 × 100' : '窗口内该类别点击 / 收录数 × 100'">每百条点击</th>
                  <th>7日新增</th>
                  <th>30日新增</th>
                  <th>最近复查</th>
                </tr>
              </thead>
              <tbody>
                <tr v-if="!sourceStats.sources?.length">
                  <td colspan="16" class="source-empty">暂无来源数据</td>
                </tr>
                <tr
                  v-for="s in sourceStats.sources"
                  :key="s.source || '__unknown__'"
                  :class="{ 'low-sample': s.low_sample }"
                >
                  <td class="src-name" :title="s.source || '（未知来源）'">{{ sourceName(s.source) }}</td>
                  <td>
                    <span class="sr-badge" :class="suggestionClass(s.suggestion)">{{ s.suggestion }}</span>
                  </td>
                  <td>{{ score(s.quality_score) }}</td>
                  <td>{{ num(s.total) }}</td>
                  <td>{{ num(s.checked) }} <small class="muted">({{ pct(s.checked_rate) }})</small></td>
                  <td :class="{ 'text-danger': (s.dead_rate || 0) >= 50 }">{{ pct(s.dead_rate) }}</td>
                  <td>{{ pct(s.suspect_rate) }}</td>
                  <td>{{ pct(s.exclusive_rate) }}</td>
                  <td>{{ num(s.impressions) }}</td>
                  <td>{{ num(s.clicks) }}</td>
                  <td>{{ pct(s.ctr, 2) }}</td>
                  <td>{{ pct(s.click_share) }}</td>
                  <td>{{ s.clicks_per_100 === null || s.clicks_per_100 === undefined ? '—' : Number(s.clicks_per_100).toFixed(2) }}</td>
                  <td>{{ num(s.new_7d) }}</td>
                  <td>{{ num(s.new_30d) }}</td>
                  <td>{{ dt(s.last_checked) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p class="source-note">{{ sourceStats.note }}</p>
        </template>
      </div>

      <!-- 网盘分布 -->
      <div class="result-section" v-if="stats.analyzer?.disk_distribution">
        <h3>网盘分布</h3>
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

      <!-- 搜索热词 -->
      <div class="result-section" v-if="stats.index?.hot_keywords?.length">
        <h3>热搜资源</h3>
        <div class="stat-row" v-for="item in stats.index.hot_keywords.slice(0, 15)" :key="item.title">
          <span>{{ item.title }}</span>
          <span>{{ item.count }} 次搜索</span>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.stats-note {
  margin: -4px 0 18px;
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.6;
}

.source-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.source-sub {
  margin: 4px 0 0;
  color: var(--text-secondary);
  font-size: 12px;
}

.source-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.source-toolbar select {
  height: 30px;
  padding: 0 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg-card);
  color: var(--text);
  font-size: 12px;
}

.source-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
  margin: 10px 0 12px;
  color: var(--text-secondary);
  font-size: 12px;
}

.source-table-wrap {
  overflow-x: auto;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg-card);
}

.source-table {
  width: 100%;
  min-width: 1440px;
  border-collapse: collapse;
  font-size: 12px;
}

.source-table th,
.source-table td {
  padding: 8px 10px;
  border-bottom: 1px solid var(--border);
  text-align: right;
  white-space: nowrap;
}

.source-table th {
  color: var(--text-secondary);
  font-weight: 600;
  background: var(--bg-card-hover);
  position: sticky;
  top: 0;
  z-index: 1;
}

.source-table th:first-child,
.source-table td:first-child {
  text-align: left;
  position: sticky;
  left: 0;
  background: var(--bg-card);
  z-index: 2;
}

.source-table th:first-child {
  z-index: 3;
  background: var(--bg-card-hover);
}

.source-table tbody tr:hover td {
  background: var(--bg-card-hover);
}

.source-table tbody tr:last-child td {
  border-bottom: 0;
}

.source-table .src-name {
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  font-weight: 600;
}

.source-table .low-sample {
  opacity: .55;
}

.muted {
  color: var(--text-muted);
}

.text-danger {
  color: var(--danger);
  font-weight: 600;
}

.source-empty {
  text-align: center !important;
  color: var(--text-secondary);
  padding: 24px 10px !important;
}

.sr-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: var(--radius-pill);
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
}

.sr-keep {
  color: var(--success);
  background: var(--success-soft);
}

.sr-down {
  color: var(--danger);
  background: var(--danger-soft);
}

.sr-low {
  color: var(--text-secondary);
  background: rgba(120, 120, 128, .1);
}

.sr-watch {
  color: var(--warning);
  background: var(--warning-soft);
}

.source-note {
  margin: 10px 0 0;
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.6;
}

@media (max-width: 720px) {
  .source-head {
    flex-direction: column;
  }

  .source-toolbar {
    width: 100%;
  }

  .source-toolbar select {
    flex: 1;
    min-width: 120px;
  }
}
</style>
