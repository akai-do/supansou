<script setup>
// 应用壳：PC 左侧栏 + 顶栏搜索；移动端顶部 dock + 底部 tabbar
import { ref, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import SearchDock from './SearchDock.vue'
import { getTheme, toggleTheme } from '../theme.js'
import { isPanLink } from '../constants.js'

const route = useRoute()
const router = useRouter()

const collapsed = ref(localStorage.getItem('dps-side') === '1')
const isMobile = ref(window.innerWidth < 768)
const theme = ref(getTheme())
const pansouOk = ref(null)   // null=检测中 true/false
let healthTimer = null

const menus = [
  { path: '/resource', label: '资源搜索', icon: '🔍' },
  { path: '/douban', label: '豆瓣榜单', icon: '🎬' },
  { path: '/stats', label: '数据统计', icon: '📊' },
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

async function checkHealth() {
  try {
    const r = await fetch('/api/health')
    const d = await r.json()
    pansouOk.value = d.pansou === 'ok'
  } catch (e) {
    pansouOk.value = false
  }
}

onMounted(() => {
  window.addEventListener('resize', onResize)
  checkHealth()
  healthTimer = setInterval(checkHealth, 60000)
})
onUnmounted(() => {
  window.removeEventListener('resize', onResize)
  clearInterval(healthTimer)
})
</script>

<template>
  <div class="shell" :class="{ collapsed: collapsed && !isMobile }">
    <!-- PC 侧栏 -->
    <aside class="sidebar" v-if="!isMobile">
      <div class="side-brand">
        <span class="logo">DS</span>
        <h1 v-if="!collapsed">DuPanSou</h1>
      </div>
      <nav class="side-menu">
        <div class="side-group" v-if="!collapsed">常用功能</div>
        <router-link v-for="m in menus" :key="m.path" :to="m.path"
                     class="side-item" active-class="active" :title="m.label">
          <span class="ico">{{ m.icon }}</span>
          <span v-if="!collapsed">{{ m.label }}</span>
        </router-link>
      </nav>
      <div class="side-foot">
        <span class="h-dot" :class="pansouOk === true ? 'ok' : pansouOk === false ? 'bad' : ''"
              :title="pansouOk === true ? 'PanSou 搜索源在线' : pansouOk === false ? 'PanSou 搜索源离线（仅历史索引模式）' : '检测中…'"></span>
        <span v-if="!collapsed" class="h-text">
          {{ pansouOk === true ? '搜索源在线' : pansouOk === false ? '搜索源离线' : '检测中…' }}
        </span>
        <a v-if="!collapsed" class="gh" href="https://github.com/caixiaoq/DuPanSou-Archive"
           target="_blank" rel="noopener">GitHub</a>
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
