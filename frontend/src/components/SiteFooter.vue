<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { siteState } from '../site.js'

const props = defineProps({
  stars: { type: [String, Number], default: null },
})

const route = useRoute()

const site = computed(() => siteState.settings.site || {})
const footer = computed(() => siteState.settings.footer || {})
const year = new Date().getFullYear()

// /search 用极简变体：仅版权行 + 法律链接行，居中排列
const isSearchPage = computed(() => route.path === '/search')

const legalLinks = [
  { path: '/legal/clean-network', label: '净网声明' },
  { path: '/legal/copyright', label: '版权说明' },
  { path: '/legal/disclaimer', label: '免责声明' },
]
</script>

<template>
  <footer v-if="isSearchPage" class="site-footer site-footer-bare">
    <div class="sfb-inner">
      <p class="sfb-copy">© {{ year }} {{ site.name || 'SuPanSou' }}</p>
      <nav v-if="footer.legal" class="sfb-links" aria-label="法律声明">
        <router-link v-for="l in legalLinks" :key="l.path" :to="l.path">
          {{ l.label }}
        </router-link>
      </nav>
    </div>
  </footer>

  <footer v-else class="site-footer">
    <div class="sf-inner">
      <div class="sf-main">
        <div class="sf-brand">
          <img :src="site.logo || '/favicon.svg'" alt="" class="sf-logo" />
          <div>
            <strong>{{ site.name || 'SuPanSou' }}</strong>
            <p>{{ footer.note || '本站仅提供资源索引与链接有效性检测，不存储任何文件。' }}</p>
          </div>
        </div>

        <nav class="sf-links" v-if="footer.legal">
          <router-link v-for="l in legalLinks" :key="l.path" :to="l.path">
            {{ l.label }}
          </router-link>
        </nav>
      </div>

      <div class="sf-bottom">
        <span>© {{ year }} {{ site.name || 'SuPanSou' }}</span>
        <div class="sf-bottom-links">
          <router-link to="/admin/login" class="sf-admin">站长后台</router-link>
          <a v-if="footer.repo_url" :href="footer.repo_url" target="_blank"
             rel="noopener" class="sf-repo">
            GitHub<template v-if="stars !== null"> · ★ {{ stars }}</template>
          </a>
        </div>
      </div>
    </div>
  </footer>
</template>
