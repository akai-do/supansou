<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import HeroSearch from '../components/HeroSearch.vue'
import NoticeBar from '../components/NoticeBar.vue'
import QuickDiscovery from '../components/QuickDiscovery.vue'
import AppIcon from '../components/AppIcon.vue'
import { API, isPanLink } from '../constants.js'
import { siteState } from '../site.js'

const router = useRouter()
const hot = ref([])
const history = ref([])
const titles = ref([])
const loading = ref(true)

const site = computed(() => siteState.settings.site || {})
const notice = computed(() => siteState.settings.notice || {})

const features = [
  { icon: 'link', title: '直链可用', text: '点开即达分享页，支持复制链接与提取码' },
  { icon: 'check', title: '有效性三态', text: '有效 / 疑似失效 / 已失效，巡检与举报共同维护' },
  { icon: 'cloud', title: '本地索引', text: '搜索即积累，老资源也能在本地归档里找到' },
]

// “历史”行只做氛围展示：不读取、不保存任何真实搜索记录
const HISTORY_WORDS = [
  '嫁金钗', '小森林', '帕特森', '黑袍纠察队', '黑袍',
  '写真', '新手上路', '粉笔', '八仙',
]

function shuffle(list) {
  const arr = list.slice()
  for (let i = arr.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1))
    ;[arr[i], arr[j]] = [arr[j], arr[i]]
  }
  return arr
}

function go(value) {
  const kw = (value || '').trim()
  if (!kw) return
  if (isPanLink(kw)) {
    router.push({ path: '/search', query: { link: kw } })
  } else {
    router.push({ path: '/search', query: { ai: kw } })
  }
}

async function loadHot() {
  try {
    const resp = await fetch(`${API}/hot-search-words?limit=18`)
    const data = await resp.json()
    if (Array.isArray(data.words)) hot.value = data.words
  } catch (e) { /* 首页局部失败不阻塞其他行 */ }
}

async function loadTitles() {
  try {
    const resp = await fetch(`${API}/resource-titles?limit=16&random=1`)
    const data = await resp.json()
    if (Array.isArray(data.titles)) titles.value = data.titles.slice(0, 16)
  } catch (e) { /* 忽略 */ }
}

onMounted(async () => {
  history.value = shuffle(HISTORY_WORDS).map(keyword => ({ keyword }))
  loading.value = true
  await Promise.allSettled([loadHot(), loadTitles()])
  loading.value = false
})
</script>

<template>
  <div class="home-page">
    <NoticeBar :notice="notice" />

    <section class="home-hero">
      <div class="home-brand">
        <img class="home-logo" :src="site.logo || '/favicon.svg'" alt="" />
        <div>
          <h1>{{ site.name || 'SuPanSou' }}</h1>
          <p>{{ site.subtitle || '网盘资源聚合搜索' }}</p>
        </div>
      </div>

      <HeroSearch @search="go" />

      <QuickDiscovery :hot="hot" :history="history" :titles="titles"
                      :loading="loading" @select="go" />
    </section>

    <section class="home-features" aria-label="站点能力">
      <div v-for="f in features" :key="f.title" class="home-feature">
        <span class="hf-icon"><AppIcon :name="f.icon" :size="20" /></span>
        <div>
          <strong>{{ f.title }}</strong>
          <p>{{ f.text }}</p>
        </div>
      </div>
    </section>
  </div>
</template>
