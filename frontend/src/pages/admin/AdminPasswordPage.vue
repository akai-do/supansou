<script setup>
import { computed, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { adminFetch } from '../../adminApi.js'
import { adminState } from '../../adminState.js'

const router = useRouter()
const form = reactive({ old_password: '', new_password: '', confirm: '' })
const saving = ref(false)
const error = ref('')
const success = ref('')
const mustChange = computed(() => !!adminState.user?.must_change_password)

async function submit() {
  error.value = ''
  success.value = ''
  if (!form.old_password || !form.new_password) {
    error.value = '请填写原密码和新密码'
    return
  }
  if (form.new_password.length < 8) {
    error.value = '新密码至少 8 位'
    return
  }
  if (form.new_password !== form.confirm) {
    error.value = '两次输入的新密码不一致'
    return
  }
  saving.value = true
  try {
    await adminFetch('/auth/change-password', {
      method: 'POST',
      body: {
        old_password: form.old_password,
        new_password: form.new_password,
      },
    })
    if (adminState.user) adminState.user.must_change_password = false
    success.value = '密码已修改，正在返回控制台…'
    form.old_password = form.new_password = form.confirm = ''
    setTimeout(() => router.replace('/admin/dashboard'), 600)
  } catch (e) {
    error.value = e.message || '修改失败'
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="admin-password">
    <div class="admin-page-head">
      <div>
        <h2>修改密码</h2>
        <p>后台密码与 ACCEL_TOKEN 相互独立，密码只保存哈希。</p>
      </div>
    </div>

    <div v-if="mustChange" class="admin-notice">
      这是初始密码，请先修改后再使用后台其他功能。
    </div>

    <section class="admin-card admin-password-card">
      <form @submit.prevent="submit">
        <label class="admin-field wide">
          <span>原密码</span>
          <input v-model="form.old_password" type="password"
                 autocomplete="current-password" />
        </label>
        <label class="admin-field wide">
          <span>新密码</span>
          <input v-model="form.new_password" type="password"
                 autocomplete="new-password" placeholder="至少 8 位" />
        </label>
        <label class="admin-field wide">
          <span>确认新密码</span>
          <input v-model="form.confirm" type="password"
                 autocomplete="new-password" />
        </label>

        <div v-if="error" class="error-msg">{{ error }}</div>
        <div v-if="success" class="admin-notice">{{ success }}</div>

        <button class="btn primary" type="submit" :disabled="saving">
          {{ saving ? '保存中…' : '修改密码' }}
        </button>
      </form>
    </section>
  </div>
</template>
