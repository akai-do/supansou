<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import AppIcon from './AppIcon.vue'
import { getTheme, toggleTheme } from '../theme.js'

const route = useRoute()
const theme = ref(getTheme())
const toolsOpen = ref(false)
const moreRef = ref(null)

const primary = [
  { path: '/', label: '首页', icon: 'home' },
  { path: '/demand', label: '需求', icon: 'demand' },
]

const tools = [
  { path: '/douban', label: '豆瓣榜单', icon: 'film' },
]

const toolsActive = computed(() => tools.some(t => route.path.startsWith(t.path)))

// /search 用极简变体：无 Logo、无图标导航、仅右上角纯文字链接
const isSearchPage = computed(() => route.path === '/search')

const bareLinks = [
  { path: '/', label: '首页' },
  { path: '/demand', label: '需求' },
  { path: '/douban', label: '更多工具' },
]

function flipTheme() {
  theme.value = toggleTheme()
}

function onDocClick(e) {
  if (toolsOpen.value && moreRef.value && !moreRef.value.contains(e.target)) {
    toolsOpen.value = false
  }
}

onMounted(() => document.addEventListener('click', onDocClick))
onUnmounted(() => document.removeEventListener('click', onDocClick))
</script>

<template>
  <!-- /search 简化变体：右上角纯文字导航 + 主题切换 -->
  <header v-if="isSearchPage" class="site-header site-header-bare">
    <div class="sh-bare-inner">
      <nav class="sh-bare-nav" aria-label="主导航">
        <router-link v-for="m in bareLinks" :key="m.path" :to="m.path"
                     class="sh-bare-link"
                     :class="{ 'is-current': route.path === m.path }">
          {{ m.label }}
        </router-link>
      </nav>
      <button class="theme-btn sh-theme" @click="flipTheme"
              :title="theme === 'dark' ? '切换到亮色' : '切换到暗色'"
              :aria-label="theme === 'dark' ? '切换到亮色' : '切换到暗色'">
        <AppIcon :name="theme === 'dark' ? 'sun' : 'moon'" :size="18" />
      </button>
    </div>
  </header>

  <header v-else class="site-header">
    <div class="sh-inner">
      <nav class="sh-nav" aria-label="主导航">
        <router-link v-for="m in primary" :key="m.path" :to="m.path"
                     class="sh-link" active-class="active">
          <AppIcon :name="m.icon" :size="16" />
          <span>{{ m.label }}</span>
        </router-link>

        <div class="sh-more" ref="moreRef">
          <button class="sh-link sh-more-btn" :class="{ active: toolsActive || toolsOpen }"
                  @click.stop="toolsOpen = !toolsOpen" aria-haspopup="menu"
                  :aria-expanded="toolsOpen">
            <AppIcon name="tools" :size="16" />
            <span>更多工具</span>
          </button>
          <div v-if="toolsOpen" class="sh-more-menu" role="menu">
            <router-link v-for="t in tools" :key="t.path" :to="t.path"
                         class="sh-more-item" @click="toolsOpen = false">
              <AppIcon :name="t.icon" :size="16" />
              <span>{{ t.label }}</span>
            </router-link>
          </div>
        </div>
      </nav>

      <button class="theme-btn sh-theme" @click="flipTheme"
              :title="theme === 'dark' ? '切换到亮色' : '切换到暗色'"
              :aria-label="theme === 'dark' ? '切换到亮色' : '切换到暗色'">
        <AppIcon :name="theme === 'dark' ? 'sun' : 'moon'" :size="18" />
      </button>
    </div>
  </header>
</template>
