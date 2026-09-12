import { createRouter, createWebHistory } from 'vue-router'
import ResourcePage from './pages/ResourcePage.vue'
import DoubanPage from './pages/DoubanPage.vue'
import StatsPage from './pages/StatsPage.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/resource' },
    { path: '/resource', component: ResourcePage },
    { path: '/douban', component: DoubanPage },
    { path: '/stats', component: StatsPage },
    { path: '/:pathMatch(.*)*', redirect: '/resource' },
  ],
})

export default router
