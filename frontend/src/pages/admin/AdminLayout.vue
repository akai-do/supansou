<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppIcon from '../../components/AppIcon.vue'
import { fetchMe, logout } from '../../adminApi.js'
import { adminState, setAdminUser } from '../../adminState.js'
import { siteState } from '../../site.js'

const route = useRoute()
const router = useRouter()
const enabled = computed(() => !!siteState.settings.features?.admin_v2)
const site = computed(() => siteState.settings.site || {})
const user = computed(() => adminState.user)
const loading = ref(true)
const error = ref('')

const nav = [
  { path: '/admin/dashboard', label: '控制台', icon: 'chart' },
  { path: '/admin/resources', label: '资源管理', icon: 'cloud' },
  { path: '/admin/demands', label: '需求管理', icon: 'demand' },
  { path: '/admin/settings', label: '站点设置', icon: 'tools' },
  { path: '/admin/password', label: '修改密码', icon: 'check' },
]

const coming = [
  { label: '网盘管理', icon: 'cloud' },
  { label: '源库管理', icon: 'link' },
  { label: '接口配置', icon: 'tools' },
  { label: '搜索排名', icon: 'chart' },
]

const title = computed(() => route.meta.adminTitle || '管理后台')

async function load() {
  loading.value = true
  error.value = ''
  if (!enabled.value) {
    loading.value = false
    return
  }
  const me = await fetchMe()
  if (!me) {
    setAdminUser(null)
    loading.value = false
    router.replace('/admin/login')
    return
  }
  setAdminUser(me)
  loading.value = false
  if (me.must_change_password && route.path !== '/admin/password') {
    router.replace('/admin/password')
  }
}

async function doLogout() {
  await logout()
  setAdminUser(null)
  router.replace('/admin/login')
}

onMounted(load)
watch(() => route.path, (path) => {
  if (user.value?.must_change_password && path !== '/admin/password') {
    router.replace('/admin/password')
  }
})
</script>

<template>
  <div v-if="loading" class="admin-full-state">
    <span class="spinner"></span>正在检查登录状态…
  </div>

  <div v-else-if="!enabled" class="admin-full-state">
    <AppIcon name="tools" :size="32" />
    <h1>管理后台未开启</h1>
    <p>请在后端环境变量中设置 <code>ADMIN_V2=1</code> 并重启服务。</p>
    <router-link class="btn" to="/">返回站点</router-link>
  </div>

  <div v-else-if="user" class="admin-shell">
    <aside class="admin-sidebar">
      <router-link to="/" class="admin-brand">
        <img :src="site.logo || '/favicon.svg'" alt="" />
        <span>
          <strong>{{ site.name || 'SuPanSou' }}</strong>
          <small>管理后台</small>
        </span>
      </router-link>

      <nav class="admin-nav" aria-label="后台导航">
        <router-link v-for="item in nav" :key="item.path" :to="item.path"
                     class="admin-nav-item" active-class="active">
          <AppIcon :name="item.icon" :size="17" />
          <span>{{ item.label }}</span>
        </router-link>

        <div class="admin-nav-group">阶段 5 模块</div>
        <div v-for="item in coming" :key="item.label"
             class="admin-nav-item is-disabled" title="阶段 5 上线">
          <AppIcon :name="item.icon" :size="17" />
          <span>{{ item.label }}</span>
          <em>待上线</em>
        </div>
      </nav>

      <div class="admin-sidebar-foot">
        <router-link to="/">← 返回站点</router-link>
      </div>
    </aside>

    <div class="admin-main">
      <header class="admin-topbar">
        <strong>{{ title }}</strong>
        <div class="admin-topbar-right">
          <span class="admin-user">{{ user.username }}</span>
          <button class="btn sm" @click="doLogout">退出登录</button>
        </div>
      </header>
      <main class="admin-content">
        <div v-if="error" class="error-msg">{{ error }}</div>
        <router-view />
      </main>
    </div>
  </div>
</template>
