<script setup>
import { ref, computed } from 'vue'

const currentPage = ref('search')

const pages = {
  search: '搜索',
  archive: '历史索引',
  stats: '数据统计',
}
</script>

<template>
  <div class="nav">
    <a
      v-for="(label, key) in pages"
      :key="key"
      :class="{ active: currentPage === key }"
      href="#"
      @click.prevent="currentPage = key"
    >{{ label }}</a>
  </div>

  <SearchPage v-if="currentPage === 'search'" />
  <ArchivePage v-if="currentPage === 'archive'" />
  <StatsPage v-if="currentPage === 'stats'" />
</template>

<script>
import SearchPage from './pages/SearchPage.vue'
import ArchivePage from './pages/ArchivePage.vue'
import StatsPage from './pages/StatsPage.vue'

export default { components: { SearchPage, ArchivePage, StatsPage } }
</script>