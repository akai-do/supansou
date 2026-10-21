<script setup>
import { onMounted, reactive, ref } from 'vue'
import { adminFetch } from '../../adminApi.js'
import { loadSiteSettings } from '../../site.js'

const form = reactive({
  site_name: '',
  site_subtitle: '',
  site_logo: '',
  footer_note: '',
  repo_url: '',
  notice_enabled: true,
  notice_text: '',
  notice_version: '',
  legal_footer: true,
  demand_enabled: true,
})

const env = ref({})
const effective = ref({})
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const success = ref('')

function applySettings(settings) {
  form.site_name = settings.site_name || ''
  form.site_subtitle = settings.site_subtitle || ''
  form.site_logo = settings.site_logo || ''
  form.footer_note = settings.footer_note || ''
  form.repo_url = settings.repo_url || ''
  form.notice_text = settings.notice_text || ''
  form.notice_version = settings.notice_version || ''
  form.notice_enabled = String(settings.notice_enabled) === '1'
  form.legal_footer = String(settings.legal_footer) === '1'
  form.demand_enabled = String(settings.demand_enabled) === '1'
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const data = await adminFetch('/admin/settings')
    applySettings(data.settings || {})
    env.value = data.features?.env || {}
    effective.value = data.features?.effective || {}
  } catch (e) {
    error.value = e.message || '设置加载失败'
  } finally {
    loading.value = false
  }
}

async function save() {
  error.value = ''
  success.value = ''
  saving.value = true
  try {
    const data = await adminFetch('/admin/settings', {
      method: 'PUT',
      body: {
        settings: {
          ...form,
          notice_enabled: form.notice_enabled ? '1' : '0',
          legal_footer: form.legal_footer ? '1' : '0',
          demand_enabled: form.demand_enabled ? '1' : '0',
        },
      },
    })
    applySettings(data.settings || {})
    effective.value = data.public?.features || effective.value
    await loadSiteSettings()
    success.value = '设置已保存；首页、页脚与公告会在下次加载时使用新值'
  } catch (e) {
    error.value = e.message || '保存失败'
    if (e.data?.fields) {
      error.value += '：' + Object.values(e.data.fields).join('；')
    }
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="admin-settings">
    <div class="admin-page-head">
      <div>
        <h2>站点设置</h2>
        <p>修改站点名称、公告与页脚；功能总开关仍由环境变量控制。</p>
      </div>
      <button class="btn sm" :disabled="loading" @click="load">重新读取</button>
    </div>

    <div v-if="loading" class="loading"><span class="spinner"></span>加载中…</div>
    <div v-else>
      <div v-if="error" class="error-msg">{{ error }}</div>
      <div v-if="success" class="admin-notice">{{ success }}</div>

      <section class="admin-card">
        <h3>站点信息</h3>
        <div class="admin-form-grid">
          <label class="admin-field">
            <span>站点名称</span>
            <input v-model="form.site_name" maxlength="40" />
          </label>
          <label class="admin-field">
            <span>副标题</span>
            <input v-model="form.site_subtitle" maxlength="60" />
          </label>
          <label class="admin-field wide">
            <span>Logo 地址</span>
            <input v-model="form.site_logo" maxlength="200"
                   placeholder="/favicon.svg 或 https://..." />
          </label>
          <label class="admin-field wide">
            <span>页脚说明</span>
            <input v-model="form.footer_note" maxlength="300" />
          </label>
          <label class="admin-field wide">
            <span>仓库地址</span>
            <input v-model="form.repo_url" maxlength="200" />
          </label>
        </div>
      </section>

      <section class="admin-card">
        <h3>公告</h3>
        <label class="admin-check">
          <input type="checkbox" v-model="form.notice_enabled" />
          显示首页公告条
        </label>
        <label class="admin-field wide">
          <span>公告内容</span>
          <textarea v-model="form.notice_text" rows="3" maxlength="1000"></textarea>
        </label>
        <label class="admin-field">
          <span>公告版本</span>
          <input v-model="form.notice_version" maxlength="40"
                 placeholder="改变版本号会让已关闭公告的用户重新看到" />
        </label>
        <p class="admin-hint">
          用户关闭公告后，会按版本号记住；修改版本号可让公告重新出现。
        </p>
      </section>

      <section class="admin-card">
        <h3>功能开关</h3>
        <label class="admin-check">
          <input type="checkbox" v-model="form.demand_enabled" />
          开启需求板
        </label>
        <label class="admin-check">
          <input type="checkbox" v-model="form.legal_footer" />
          页脚显示法律页入口
        </label>
        <p class="admin-hint">
          需求板需要环境变量 <code>DEMAND_ENABLED=1</code> 作为总闸；
          本页开关是二级开关。若在这里关闭过，之后需要在这里重新打开。
        </p>
      </section>

      <section class="admin-card">
        <h3>环境变量（只读）</h3>
        <div class="admin-kv"><span>ALISO_UI</span><b>{{ env.aliso_ui ? '开启' : '关闭' }}</b></div>
        <div class="admin-kv"><span>STREAM_SEARCH</span><b>{{ env.stream_search ? '开启' : '关闭' }}</b></div>
        <div class="admin-kv"><span>ADMIN_V2</span><b>{{ env.admin_v2 ? '开启' : '关闭' }}</b></div>
        <div class="admin-kv"><span>DEMAND_ENABLED 总闸</span><b>{{ env.demand_master ? '开启' : '关闭' }}</b></div>
        <div class="admin-kv"><span>SITE_NOTICE_ENABLED 总闸</span><b>{{ env.notice_master ? '开启' : '关闭' }}</b></div>
        <p class="admin-hint">
          当前生效：需求板 {{ effective.demand_enabled ? '开启' : '关闭' }} ·
          公告 {{ effective.site_notice_enabled ? '开启' : '关闭' }}
        </p>
      </section>

      <div class="admin-save-bar">
        <button class="btn primary" :disabled="saving" @click="save">
          {{ saving ? '保存中…' : '保存设置' }}
        </button>
      </div>
    </div>
  </div>
</template>
