// 全局常量：网盘类型映射 / 筛选顺序 / 彩色 tag 类名 / API 前缀

export const API = '/api'

export const DISK_LABELS = {
  baidu: '百度', quark: '夸克', aliyun: '阿里', xunlei: '迅雷',
  '115': '115', tianyi: '天翼', uc: 'UC', pikpak: 'PikPak',
  '123': '123', magnet: '磁力', ed2k: '电驴', others: '其他',
}

// 筛选 chips 的展示顺序（与后端 DISK_PRIORITY 大体一致）
export const DISK_FILTERS = ['quark', 'baidu', 'aliyun', 'xunlei', '115', '123', 'tianyi', 'uc', 'pikpak', 'magnet']

// 彩色网盘 tag（仿 CloudSaver：夸克绿/百度蓝/阿里橙/115红）
export const DISK_TAG_CLASS = {
  quark: 'tag-quark', baidu: 'tag-baidu', aliyun: 'tag-aliyun', '115': 'tag-115',
  xunlei: 'tag-xunlei', tianyi: 'tag-tianyi', uc: 'tag-uc', '123': 'tag-123',
  pikpak: 'tag-pikpak', magnet: 'tag-magnet', ed2k: 'tag-magnet', others: 'tag-other',
}

// 资源卡整体有效性：任一链接有效即有效；全部确认失效才算失效
export function cardDead(r) {
  const vs = [r, ...(r.variants || [])]
  return vs.every(v => (v.validity || '') === 'dead')
}

export function cardSuspect(r) {
  const vs = [r, ...(r.variants || [])]
  return !cardDead(r) && !vs.some(v => v.validity === 'ok') &&
    vs.some(v => v.validity === 'suspect')
}

// 有效性徽章文案（ok/suspect/dead → 徽章三态）
export function validityBadge(it) {
  const v = it.validity || ''
  if (v === 'ok') return { cls: 'badge-alive', text: '✓ 有效', title: '智能检测确认有效' + (it.state_summary ? `（${it.state_summary}）` : '') }
  if (v === 'suspect') return { cls: 'badge-warn', text: '⚠ 疑似失效', title: '检测未确认，已降权：' + (it.state_summary || '待复检') }
  if (v === 'dead') return { cls: 'badge-dead', text: '✕ 已失效', title: '确认失效：' + (it.state_summary || '检测源报告分享不存在') }
  return null
}

// 粘贴内容是否为 http(s) 链接（走"直接检测有效性"）
export function isPanLink(s) {
  return /^https?:\/\/\S+/i.test(s || '')
}

// 网盘链接 host 粗判类型（粘贴检测时选对检测器）
export function guessDiskType(u) {
  const s = (u || '').toLowerCase()
  if (s.includes('pan.quark.cn')) return 'quark'
  if (s.includes('pan.baidu.com')) return 'baidu'
  if (s.includes('aliyundrive') || s.includes('alipan.com')) return 'aliyun'
  if (s.includes('115.com') || s.includes('anxia.com')) return '115'
  if (s.includes('123pan') || s.includes('123684.com')) return '123'
  if (s.includes('189.cn')) return 'tianyi'
  if (s.includes('xunlei') || s.includes('pan.xunlei')) return 'xunlei'
  if (s.includes('uc.cn')) return 'uc'
  return ''
}
