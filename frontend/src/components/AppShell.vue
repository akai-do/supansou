<script setup>
// 应用壳：PC 左侧栏 + 顶栏搜索；移动端顶部 dock + 底部 tabbar
import { ref, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import SearchDock from './SearchDock.vue'
import { getTheme, toggleTheme } from '../theme.js'
import { isPanLink, REPO_URL } from '../constants.js'

const route = useRoute()
const router = useRouter()

const collapsed = ref(localStorage.getItem('dps-side') === '1')
const isMobile = ref(window.innerWidth < 768)
const theme = ref(getTheme())
// 仓库星标数（左下角 GitHub 入口显示）。走服务端 /api/repo 取——它在后端缓存 30 分钟，
// 否则每个访客各自去请求 GitHub，未认证限流是"每 IP 60 次/小时"，很快就会被限。
const stars = ref(null)

const menus = [
  { path: '/resource', label: '资源搜索' },
  { path: '/douban', label: '豆瓣榜单'},
  { path: '/accel', label: '网盘加速'},
  { path: '/guest', label: '直链解析'},
  { path: '/booster', label: '下载提速'},
  { path: '/stats', label: '数据统计'},
]

function onResize() { isMobile.value = window.innerWidth < 768 }
function toggleSide() {
  collapsed.value = !collapsed.value
  localStorage.setItem('dps-side', collapsed.value ? '1' : '0')
}
function flipTheme() { theme.value = toggleTheme() }

function goSearch(kw) {
  const q = isPanLink(kw) ? { link: kw } : { kw }
  const same = route.path === '/resource' &&
    (route.query.kw || '') === (q.kw || '') &&
    (route.query.link || '') === (q.link || '')
  // 同词重搜需要强制触发 route watch
  router.push({ path: '/resource', query: same ? { ...q, _t: String(Date.now()) } : q })
}

// 星标数：优先用本地缓存（6 小时），避免每次刷新都打后端
// 后端返回的 stars_text 可能是 '9.3k' 这种写法，直接显示它更省事
const STAR_KEY = 'dps-stars'
async function loadStars() {
  try {
    const c = JSON.parse(localStorage.getItem(STAR_KEY) || 'null')
    if (c && Date.now() - c.t < 21600 * 1000) { stars.value = c.n; return }
  } catch (e) { /* ignore */ }
  try {
    const r = await fetch('/api/repo')
    const d = await r.json()
    const txt = d.stars_text || (typeof d.stars === 'number' ? String(d.stars) : '')
    if (txt) {
      stars.value = txt
      localStorage.setItem(STAR_KEY, JSON.stringify({ t: Date.now(), n: txt }))
    }
  } catch (e) { /* 取不到就不显示星标，不影响入口 */ }
}

onMounted(() => {
  window.addEventListener('resize', onResize)
  loadStars()
})
onUnmounted(() => {
  window.removeEventListener('resize', onResize)
})
</script>

<template>
  <div class="shell" :class="{ collapsed: collapsed && !isMobile }">
    <!-- PC 侧栏 -->
    <aside class="sidebar" v-if="!isMobile">
      <div class="side-brand">
        <span class="logo"></span>
        <h1 v-if="!collapsed">SuPanSou</h1>
      </div>
      <nav class="side-menu">
        <div class="side-group" v-if="!collapsed">常用功能</div>
        <router-link v-for="m in menus" :key="m.path" :to="m.path"
                     class="side-item" active-class="active" :title="m.label">
          <span v-if="!collapsed">{{ m.label }}</span>
        </router-link>
      </nav>
      <!-- 左下角仓库入口（仿 CloudSaver）：整行块级链接、图标 + 文字 + 星标，
           悬停变主题色并轻微上浮。原来这里还有"搜索源在线"的健康点，按需求去掉。 -->
      <div class="side-foot">
        <a class="gh" :class="{ 'gh-icon': collapsed }" :href="REPO_URL"
           :title="REPO_URL" target="_blank" rel="noopener" aria-label="GitHub 仓库">
          <svg class="gh-svg" viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
            <path fill="currentColor" d="M12.5.75C6.146.75 1 5.896 1 12.25c0 5.089 3.292 9.387 7.863 10.91.575.101.79-.244.79-.546
              0-.273-.014-1.178-.014-2.142-2.889.532-3.636-.704-3.866-1.35-.13-.331-.69-1.352-1.18-1.625-.402-.216-.977-.748-.014-.762.906-.014
              1.553.834 1.769 1.179 1.035 1.74 2.688 1.25 3.349.948.1-.747.402-1.25.733-1.538-2.559-.287-5.232-1.279-5.232-5.678
              0-1.25.445-2.285 1.178-3.09-.115-.288-.517-1.467.115-3.048 0 0 .963-.302 3.163 1.179.92-.259 1.897-.388 2.875-.388.977 0
              1.955.13 2.875.388 2.2-1.495 3.162-1.179 3.162-1.179.633 1.581.23 2.76.115 3.048.733.805 1.179 1.825 1.179 3.09 0
              4.413-2.688 5.39-5.247 5.678.417.36.776 1.05.776 2.128 0 1.538-.014 2.774-.014 3.162 0 .302.216.662.79.547C20.709
              21.637 24 17.324 24 12.25 24 5.896 18.854.75 12.5.75Z"/>
          </svg>
          <template v-if="!collapsed">
            <span class="gh-text" data-tip="去 GitHub 点个 Star 支持一下">GitHub</span>
            <span v-if="stars !== null" class="gh-star" :title="`当前 ${stars} 个 Star`">
              ★ {{ stars }}
            </span>
          </template>
        </a>
      </div>
      <button class="side-toggle" @click="toggleSide" :title="collapsed ? '展开菜单' : '折叠菜单'">
        {{ collapsed ? '»' : '«' }}
      </button>
    </aside>

    <!-- 主区 -->
    <div class="main">
      <header class="topbar">
        <SearchDock class="dock"
                    :initial="String(route.query.kw || route.query.link || '')"
                    @search="goSearch" />
        <button class="theme-btn" @click="flipTheme"
                :title="theme === 'dark' ? '切换到亮色' : '切换到暗色'">
          {{ theme === 'dark' ? '☀️' : '🌙' }}
        </button>
      </header>
      <main class="content">
        <router-view />
      </main>
    </div>

    <!-- 移动端底部 tabbar -->
    <nav class="tabbar" v-if="isMobile">
      <router-link v-for="m in menus" :key="m.path" :to="m.path"
                   class="tab-item" active-class="active">
        <span class="ico">{{ m.icon }}</span>
        <span>{{ m.label }}</span>
      </router-link>
    </nav>
  </div>
</template>
