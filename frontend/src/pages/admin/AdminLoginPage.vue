<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import AppIcon from '../../components/AppIcon.vue'
import { fetchMe, login } from '../../adminApi.js'
import { setAdminUser } from '../../adminState.js'
import { siteState } from '../../site.js'

const router = useRouter()
const enabled = computed(() => !!siteState.settings.features?.admin_v2)
const site = computed(() => siteState.settings.site || {})
const username = ref('')
const password = ref('')
const loading = ref(false)
const error = ref('')

onMounted(async () => {
  if (!enabled.value) return
  const user = await fetchMe()
  if (user) {
    setAdminUser(user)
    router.replace(user.must_change_password ? '/admin/password' : '/admin/dashboard')
  }
})

async function submit() {
  if (!enabled.value || loading.value) return
  error.value = ''
  if (!username.value.trim() || !password.value) {
    error.value = '请输入用户名和密码'
    return
  }
  loading.value = true
  try {
    const data = await login(username.value.trim(), password.value)
    setAdminUser(data.user)
    router.replace(data.user.must_change_password
      ? '/admin/password'
      : '/admin/dashboard')
  } catch (e) {
    error.value = e.message || '登录失败'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="admin-login-wrap">
    <div class="admin-login-card">
      <router-link to="/" class="admin-login-brand">
        <img :src="site.logo || '/favicon.svg'" alt="" />
        <div>
          <strong>{{ site.name || 'SuPanSou' }}</strong>
          <small>管理后台</small>
        </div>
      </router-link>

      <div v-if="!enabled" class="admin-disabled-box">
        <AppIcon name="tools" :size="28" />
        <h2>管理后台未开启</h2>
        <p>
          请在启动后端时设置 <code>ADMIN_V2=1</code>，并按需设置
          <code>ADMIN_PASSWORD</code>，然后重启服务。
        </p>
        <router-link class="btn" to="/">返回站点</router-link>
      </div>

      <form v-else class="admin-login-form" @submit.prevent="submit">
        <h1>登录管理后台</h1>
        <p class="admin-login-hint">后台账号与加速令牌相互独立。</p>

        <label class="admin-field">
          <span>用户名</span>
          <input v-model="username" autocomplete="username"
                 placeholder="管理员用户名" />
        </label>
        <label class="admin-field">
          <span>密码</span>
          <input v-model="password" type="password"
                 autocomplete="current-password" placeholder="登录密码" />
        </label>

        <div v-if="error" class="error-msg">{{ error }}</div>

        <button class="btn primary admin-login-btn" type="submit" :disabled="loading">
          {{ loading ? '登录中…' : '登录' }}
        </button>
        <p class="admin-login-note">
          首次登录需要修改初始密码。忘记密码时，请由服务器管理员在数据库中重置
          <code>admin_users</code> 的密码，或删除该账号后用
          <code>ADMIN_PASSWORD</code> 重新初始化。
        </p>
      </form>

      <router-link class="admin-login-back" to="/">← 返回站点首页</router-link>
    </div>
  </div>
</template>
