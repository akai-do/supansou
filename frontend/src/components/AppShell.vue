<script setup>
// 应用外壳：顶部站点导航 + 搜索工具条 + 内容区 + 页脚 + 移动端 tabbar
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import SearchDock from './SearchDock.vue'
import SiteHeader from './SiteHeader.vue'
import SiteFooter from './SiteFooter.vue'
import AppIcon from './AppIcon.vue'
import { isPanLink } from '../constants.js'

const route = useRoute()
const router = useRouter()

const moreOpen = ref(false)
const stars = ref(null)

const primary = [
  { path: '/', label: '首页', icon: 'home' },
  { path: '/demand', label: '需求', icon: 'demand' },
]

const tools = [
  { path: '/douban', label: '豆瓣榜单', icon: 'film' },
]

const showDock = computed(() => {
  if (route.path === '/') return false
  if (route.path.startsWith('/legal')) return false
  if (route.path === '/demand') return false
  // /search 有自己的居中 Hero 搜索框，不再叠加全宽搜索条
  if (route.path === '/search') return false
  return true
})

const dockInitial = computed(() => String(
  route.query.ai || route.query.kw || route.query.link || ''
))

const toolsActive = computed(() => tools.some(t => route.path.startsWith(t.path)))

function goSearch(kw) {
  const value = String(kw || '').trim()
  if (!value) return
  const q = isPanLink(value) ? { link: value } : { ai: value }
  const same = route.path === '/search' &&
    (route.query.ai || '') === (q.ai || '') &&
    (route.query.link || '') === (q.link || '')
  // 同词重搜需要强制触发 route watch
  router.push({
    path: '/search',
    query: same ? { ...q, _t: String(Date.now()) } : q,
  })
}

const STAR_KEY = 'dps-stars'
async function loadStars() {
  try {
    const cached = JSON.parse(localStorage.getItem(STAR_KEY) || 'null')
    if (cached && Date.now() - cached.t < 21600 * 1000) {
      stars.value = cached.n
      return
    }
  } catch (e) { /* ignore */ }
  try {
    const resp = await fetch('/api/repo')
    const data = await resp.json()
    const text = data.stars_text || (typeof data.stars === 'number' ? String(data.stars) : '')
    if (text) {
      stars.value = text
      localStorage.setItem(STAR_KEY, JSON.stringify({ t: Date.now(), n: text }))
    }
  } catch (e) { /* 取不到就不显示星标 */ }
}

watch(() => route.path, () => { moreOpen.value = false })

onMounted(() => {
  loadStars()
})
onUnmounted(() => {})
</script>

<template>
  <div class="shell aliso-shell">
    <SiteHeader />

    <div class="main">
      <div v-if="showDock" class="topbar">
        <SearchDock class="dock" :initial="dockInitial" @search="goSearch" />
      </div>

      <main class="content">
        <router-view />
      </main>

      <SiteFooter :stars="stars" />
    </div>

    <nav class="tabbar" aria-label="移动端导航">
      <router-link v-for="m in primary" :key="m.path" :to="m.path"
                   class="tab-item" active-class="active">
        <AppIcon :name="m.icon" :size="20" />
        <span>{{ m.label }}</span>
      </router-link>
      <button class="tab-item tab-more" :class="{ active: toolsActive || moreOpen }"
              @click="moreOpen = !moreOpen" aria-label="更多工具">
        <AppIcon name="tools" :size="20" />
        <span>更多</span>
      </button>
    </nav>

    <div v-if="moreOpen" class="mobile-more-backdrop" @click.self="moreOpen = false">
      <div class="mobile-more-sheet" role="menu">
        <div class="mms-head">
          <strong>更多工具</strong>
          <button class="mms-close" @click="moreOpen = false" aria-label="关闭">
            <AppIcon name="close" :size="16" />
          </button>
        </div>
        <router-link v-for="t in tools" :key="t.path" :to="t.path"
                     class="mms-item" @click="moreOpen = false">
          <AppIcon :name="t.icon" :size="18" />
          <span>{{ t.label }}</span>
        </router-link>
      </div>
    </div>
  </div>
</template>
