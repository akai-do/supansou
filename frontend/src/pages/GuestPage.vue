<script>
// 直链解析页（访客版）：粘贴分享链接 → 解析 → 获取高速直链
// 无需登录任何账号；后端用站长的会员账号解析，访客配额按 IP 每天限制
import { API } from '../constants.js'

export default {
  data() {
    return {
      linkText: '',
      pwd: '',
      // 解析口令：存在本地，请求时带 X-Parse-Pass。真正的校验在服务端，
      // 这里只负责"没有/过期就弹窗要口令"，绕开弹窗也过不了服务端。
      pass: localStorage.getItem('dps-parse-pass') || '',
      passEnabled: false,
      passInput: '',
      passError: '',
      showPass: false,
      parsing: false,
      parseError: null,
      share: null,          // { surl, pwd, files }
      crumbs: [],
      dirLoading: false,
      dlinkResults: {},     // fid → { name, size, dlink }
      loadingFid: '',
      copyTip: false,
      quota: { unlimited: false, used: 0, limit: 0, remaining: 0 },
      toast: '',
      _toastTimer: null,
    }
  },
  computed: {
    currentFiles() { return this.share ? this.share.files : [] },
    hasResults() { return Object.keys(this.dlinkResults).length > 0 },
  },
  mounted() {
    this.loadQuota()
  },
  methods: {
    async req(path, opts = {}) {
      const headers = { 'Content-Type': 'application/json' }
      const token = localStorage.getItem('dps-accel-token') || ''
      if (token) headers['X-Access-Token'] = token
      if (this.pass) headers['X-Parse-Pass'] = this.pass
      const resp = await fetch(`${API}/accel${path}`, { ...opts, headers })
      const data = await resp.json().catch(() => ({}))
      if (!resp.ok) {
        const err = new Error(data.error || `请求失败（${resp.status}）`)
        err.needPass = !!data.need_pass   // 服务端要求口令 → 交给 handleErr 弹窗
        throw err
      }
      return data
    },
    // 统一错误处理：要口令就弹窗（并清掉本地那份，可能是过期的），否则显示错误
    handleErr(e) {
      if (e && e.needPass) {
        this.passError = e.message
        this.pass = ''
        localStorage.removeItem('dps-parse-pass')
        this.showPass = true
      } else {
        this.parseError = (e && e.message) || '请求失败'
      }
    },
    submitPass() {
      const v = (this.passInput || '').trim()
      if (!v) { this.passError = '请输入口令'; return }
      this.pass = v
      localStorage.setItem('dps-parse-pass', v)
      this.showPass = false
      this.passInput = ''
      this.passError = ''
      this.parse()          // 立刻重试刚才那次解析
    },
    cancelPass() {
      this.showPass = false
      this.passInput = ''
      this.passError = ''
    },
    toastMsg(m) {
      this.toast = m
      clearTimeout(this._toastTimer)
      this._toastTimer = setTimeout(() => { this.toast = '' }, 2200)
    },
    async loadQuota() {
      try {
        const q = await this.req('/guest-quota')
        this.quota = q
        this.passEnabled = !!q.pass_enabled
      } catch (e) { /* 访客配额拉不到不阻塞页面 */ }
    },
    fmtSize(b) {
      if (!b || b <= 0) return '0 B'
      if (b < 1024) return b + ' B'
      if (b < 1048576) return (b / 1024).toFixed(1) + ' KB'
      if (b < 1073741824) return (b / 1048576).toFixed(1) + ' MB'
      return (b / 1073741824).toFixed(2) + ' GB'
    },

    // ===== 解析 =====
    async parse() {
      if (!this.linkText.trim()) return
      // 需要口令但本地还没有 → 先弹窗，拿到口令后自动重试
      if (this.passEnabled && !this.pass) {
        this.passError = '请输入站长给你的解析口令'
        this.showPass = true
        return
      }
      this.parsing = true
      this.parseError = null
      this.dlinkResults = {}
      this.share = null
      try {
        const d = await this.req('/parse', {
          method: 'POST',
          body: JSON.stringify({ url: this.linkText, pwd: this.pwd || undefined }),
        })
        this.share = { surl: d.surl, pwd: d.pwd, files: d.files }
        this.pwd = d.pwd || this.pwd
        this.crumbs = [{ name: '根目录', dir: '/' }]
        this.loadQuota()
      } catch (e) {
        this.handleErr(e)
      }
      this.parsing = false
    },
    shareUrl() {
      return this.share ? `https://pan.baidu.com/s/${this.share.surl}` : ''
    },
    async fetchDir(dir) {
      const d = await this.req('/list-dir', {
        method: 'POST',
        body: JSON.stringify({ url: this.shareUrl(), pwd: this.share.pwd, dir }),
      })
      this.share.files = d.files
    },
    async enterDir(f) {
      this.dirLoading = true
      this.parseError = null
      try {
        await this.fetchDir(f.path)
        this.crumbs.push({ name: f.name, dir: f.path })
      } catch (e) {
        this.handleErr(e)
      }
      this.dirLoading = false
    },
    async crumbTo(i) {
      if (!this.share || i === this.crumbs.length - 1) return
      this.dirLoading = true
      try {
        await this.fetchDir(this.crumbs[i].dir)
        this.crumbs = this.crumbs.slice(0, i + 1)
      } catch (e) {
        this.handleErr(e)
      }
      this.dirLoading = false
    },

    // ===== 直链 =====
    async getDlink(f) {
      this.loadingFid = f.fid
      this.parseError = null
      try {
        const d = await this.req('/dlinks', {
          method: 'POST',
          body: JSON.stringify({
            url: this.shareUrl(),
            pwd: this.share.pwd,
            items: [{ fid: f.fid, name: f.name, size: f.size, path: f.path }],
          }),
        })
        for (const it of (d.links || [])) {
          this.dlinkResults[f.fid] = it
        }
        if (d.quota) this.quota = d.quota
        this.toastMsg('直链已生成，复制到浏览器或下载器即可')
      } catch (e) {
        this.handleErr(e)
      }
      this.loadingFid = ''
    },
    async copyLink(dlink) {
      try {
        await navigator.clipboard.writeText(dlink)
        this.copyTip = true
        setTimeout(() => { this.copyTip = false }, 1500)
      } catch (e) {
        // 剪贴板权限失败时退化为选中文本
        const el = document.querySelector('.g-dlink-input')
        if (el) { el.select(); document.execCommand('copy') }
        this.toastMsg('已复制')
      }
    },
  },
}
</script>

<template>
  <div>
    <div class="g-hero">
      <h2>网盘直链解析</h2>
      <p>粘贴百度网盘分享链接，获取可直连下载的高速链接 · 无需登录</p>
    </div>

    <div class="g-card">
      <div class="g-label">文件分享链接</div>
      <input class="g-input" v-model="linkText" rows="1"
             placeholder="请输入文件分享链接，例如：https://pan.baidu.com/s/1xxxxxx"
             @keydown.enter="parse" />
      <div class="g-label">提取码 (可选)</div>
      <input class="g-input" v-model="pwd" maxlength="8"
             placeholder="如果有提取码请输入（链接里带了就不用填）"
             @keydown.enter="parse" />
      <button class="g-btn" @click="parse" :disabled="parsing || !linkText.trim()">
        {{ parsing ? '解析中…' : '开始解析' }}
      </button>
      <div class="error-msg" v-if="parseError">{{ parseError }}</div>
    </div>

    <div class="g-card" v-if="share">
      <div class="g-crumbs">
        <template v-for="(c, i) in crumbs" :key="c.dir">
          <span v-if="i" class="g-sep">/</span>
          <a href="javascript:void(0)" @click="crumbTo(i)">{{ c.name }}</a>
        </template>
      </div>
      <div class="g-files">
        <div class="g-file" v-for="f in currentFiles" :key="f.fid">
          <div class="g-file-main">
            <span class="g-ico">{{ f.is_dir ? '📁' : '📄' }}</span>
            <span class="g-name" :class="{ dir: f.is_dir }"
                  @click="f.is_dir && enterDir(f)">{{ f.name }}</span>
            <span class="g-size">{{ f.is_dir ? '目录' : fmtSize(f.size) }}</span>
            <button class="g-mini-btn" v-if="!f.is_dir && !dlinkResults[f.fid]"
                    @click="getDlink(f)" :disabled="loadingFid === f.fid">
              {{ loadingFid === f.fid ? '获取中…' : '获取直链' }}
            </button>
          </div>
          <div class="g-dlink-row" v-if="dlinkResults[f.fid]">
            <input class="g-dlink-input" readonly :value="dlinkResults[f.fid].dlink"
                   @focus="$event.target.select()" />
            <button class="g-mini-btn primary" @click="copyLink(dlinkResults[f.fid].dlink)">
              {{ copyTip ? '已复制 ✓' : '复制链接' }}
            </button>
          </div>
        </div>
        <div class="loading" v-if="dirLoading"><span class="spinner"></span>加载中…</div>
        <div class="empty-state" v-if="!dirLoading && !currentFiles.length"><p>（空目录）</p></div>
      </div>
      <p class="g-hint">
        直链有时效（约 8 小时），复制后用浏览器、IDM 或 Motrix/aria2 下载均可；
        多线程下载器速度更好。
      </p>
    </div>

    <div class="g-quota" v-if="quota.limit">
      <template v-if="quota.unlimited">站长模式 · 不限次数</template>
      <template v-else>今日已用 {{ quota.used }} / {{ quota.limit }} 次（按 IP 计）</template>
    </div>

    <!-- 解析口令弹窗：服务端 401 need_pass 时弹出；口令存本地，过期会被要求重输 -->
    <div class="g-modal-mask" v-if="showPass" @click.self="cancelPass">
      <div class="g-modal">
        <div class="g-modal-title">需要解析口令</div>
        <p class="g-modal-desc">
          本页解析用的是站长的会员账号，为防滥用已开启口令。
          请输入站长给你的口令（每天可能更新，以站长当天给的为准）。
        </p>
        <input class="g-input" v-model="passInput" type="password"
               placeholder="请输入解析口令" @keydown.enter="submitPass" />
        <div class="error-msg" v-if="passError">{{ passError }}</div>
        <div class="g-modal-btns">
          <button class="g-btn" @click="submitPass" :disabled="!passInput.trim()">确定并解析</button>
          <button class="g-mini-btn" @click="cancelPass">取消</button>
        </div>
      </div>
    </div>

    <div class="toast" v-if="toast">{{ toast }}</div>
  </div>
</template>

<style scoped>
.g-hero { text-align: center; margin: 6px 0 20px; }
.g-hero h2 { font-size: 26px; letter-spacing: 1px; }
.g-hero p { font-size: 13px; color: var(--text-secondary); margin-top: 6px; }

.g-card {
  background: var(--bg-card); border: 1px solid var(--border);
  border-radius: var(--radius-lg); padding: 20px; margin-bottom: 14px;
  box-shadow: var(--shadow); max-width: 860px; margin-left: auto; margin-right: auto;
}
.g-label { font-size: 14px; font-weight: 600; margin-bottom: 8px; }
.g-input {
  width: 100%; padding: 12px 14px; margin-bottom: 12px;
  border: 1px solid var(--border); border-radius: var(--radius);
  background: var(--bg-card); color: var(--text); font-size: 14px; outline: none;
}
.g-input:focus { border-color: var(--primary); box-shadow: 0 0 0 3px var(--primary-soft); }
.g-btn {
  width: 100%; padding: 13px; border: none; border-radius: var(--radius);
  background: linear-gradient(135deg, var(--primary), var(--primary-hover));
  color: #fff; font-size: 15px; font-weight: 600; cursor: pointer; transition: opacity .15s;
}
.g-btn:hover { opacity: .92; }
.g-btn:disabled { opacity: .55; cursor: not-allowed; }

.g-crumbs { font-size: 13px; margin-bottom: 10px; }
.g-crumbs a { color: var(--primary); text-decoration: none; }
.g-sep { color: var(--text-secondary); margin: 0 4px; }

.g-files { border: 1px solid var(--border); border-radius: var(--radius); }
.g-file { padding: 10px 14px; border-bottom: 1px solid var(--border); }
.g-file:last-child { border-bottom: none; }
.g-file-main { display: flex; align-items: center; gap: 10px; }
.g-ico { flex-shrink: 0; }
.g-name { flex: 1; min-width: 0; font-size: 13px; word-break: break-all; }
.g-name.dir { color: var(--primary); font-weight: 500; cursor: pointer; }
.g-name.dir:hover { text-decoration: underline; }
.g-size { color: var(--text-secondary); font-size: 12px; flex-shrink: 0; }
.g-mini-btn {
  padding: 5px 12px; border-radius: 8px; font-size: 12px; flex-shrink: 0;
  border: 1px solid var(--primary); background: var(--primary-soft); color: var(--primary);
  cursor: pointer; transition: all .15s;
}
.g-mini-btn:hover { background: var(--primary); color: #fff; }
.g-mini-btn:disabled { opacity: .55; cursor: not-allowed; }
.g-mini-btn.primary { background: var(--primary); color: #fff; }
.g-dlink-row { display: flex; gap: 8px; margin-top: 8px; }
.g-dlink-input {
  flex: 1; min-width: 0; padding: 7px 10px; font-size: 12px;
  border: 1px dashed var(--border); border-radius: 8px;
  background: var(--bg); color: var(--text-secondary);
  font-family: Consolas, monospace; word-break: break-all;
}
.g-hint { font-size: 12px; color: var(--text-secondary); margin-top: 10px; line-height: 1.8; }
.g-quota {
  max-width: 860px; margin: 0 auto; text-align: center;
  font-size: 12px; color: var(--text-secondary);
}

@media (max-width: 768px) {
  .g-dlink-row { flex-direction: column; }
}

/* 口令弹窗 */
.g-modal-mask {
  position: fixed; inset: 0; z-index: 999;
  background: rgba(0, 0, 0, .45);
  display: flex; align-items: center; justify-content: center; padding: 20px;
}
.g-modal {
  width: 100%; max-width: 420px;
  background: var(--bg-card); border: 1px solid var(--border);
  border-radius: var(--radius-lg); padding: 20px;
  box-shadow: 0 12px 40px rgba(0, 0, 0, .3);
}
.g-modal-title { font-size: 16px; font-weight: 700; margin-bottom: 8px; }
.g-modal-desc {
  font-size: 13px; color: var(--text-secondary);
  line-height: 1.8; margin-bottom: 12px;
}
.g-modal-btns {
  display: flex; align-items: center; gap: 10px; margin-top: 14px;
}
.g-modal-btns .g-btn { flex: 1; margin-top: 0; }
</style>
