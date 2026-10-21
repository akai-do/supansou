import { createRouter, createWebHistory } from 'vue-router'
import HomePage from './pages/HomePage.vue'
import SearchPage from './pages/SearchPage.vue'
import ResourcePage from './pages/ResourcePage.vue'
import DemandPage from './pages/DemandPage.vue'
import LegalPage from './pages/LegalPage.vue'
import DoubanPage from './pages/DoubanPage.vue'
import AccelPage from './pages/AccelPage.vue'
import GuestPage from './pages/GuestPage.vue'
import BoosterPage from './pages/BoosterPage.vue'
import StatsPage from './pages/StatsPage.vue'
import AdminLoginPage from './pages/admin/AdminLoginPage.vue'
import AdminLayout from './pages/admin/AdminLayout.vue'
import AdminDashboardPage from './pages/admin/AdminDashboardPage.vue'
import AdminResourcesPage from './pages/admin/AdminResourcesPage.vue'
import AdminDemandsPage from './pages/admin/AdminDemandsPage.vue'
import AdminSettingsPage from './pages/admin/AdminSettingsPage.vue'
import AdminPasswordPage from './pages/admin/AdminPasswordPage.vue'
import { siteState } from './site.js'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: HomePage },
    { path: '/search', component: SearchPage },
    // 旧地址保留：ALISO_UI=1 时由守卫转到 /search；ALISO_UI=0 时直接渲染旧搜索页
    { path: '/resource', component: ResourcePage },
    { path: '/demand', component: DemandPage },
    { path: '/legal/:type', component: LegalPage },
    { path: '/douban', component: DoubanPage },
    { path: '/accel', component: AccelPage },
    { path: '/guest', component: GuestPage },
    { path: '/booster', component: BoosterPage },
    { path: '/stats', component: StatsPage },
    { path: '/admin/login', component: AdminLoginPage },
    {
      path: '/admin',
      component: AdminLayout,
      children: [
        { path: '', redirect: '/admin/dashboard' },
        { path: 'dashboard', component: AdminDashboardPage, meta: { adminTitle: '控制台' } },
        { path: 'resources', component: AdminResourcesPage, meta: { adminTitle: '资源管理' } },
        { path: 'demands', component: AdminDemandsPage, meta: { adminTitle: '需求管理' } },
        { path: 'settings', component: AdminSettingsPage, meta: { adminTitle: '站点设置' } },
        { path: 'password', component: AdminPasswordPage, meta: { adminTitle: '修改密码' } },
      ],
    },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

// 新 UI / 旧 UI 的兼容开关。配置在启动时已拉取；拉取失败按新 UI 处理。
router.beforeEach((to) => {
  const aliso = !siteState.loaded || !!siteState.settings.features?.aliso_ui

  if (!aliso) {
    if (to.path === '/') return { path: '/resource' }
    if (to.path === '/search') {
      const kw = to.query.ai || to.query.kw || ''
      const query = { ...to.query }
      delete query.ai
      delete query.kw
      if (kw) query.kw = kw
      return { path: '/resource', query }
    }
    return true
  }

  if (to.path === '/resource') {
    const query = { ...to.query }
    const ai = query.ai || query.kw || ''
    delete query.kw
    if (ai) query.ai = ai
    return { path: '/search', query }
  }

  // 后台总开关关闭时，除登录页外统一回到登录页（由登录页显示“未开启”说明）
  if (to.path.startsWith('/admin') && to.path !== '/admin/login') {
    if (siteState.loaded && !siteState.settings.features?.admin_v2) {
      return { path: '/admin/login' }
    }
  }

  return true
})

export default router
