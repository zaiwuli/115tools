<script setup lang="ts">
import { useSystem } from './store'
import { ref, onMounted, computed } from 'vue'
import { api } from './api'
const system = useSystem()
const updates = ref<any>(null)
const busy = ref(false)
const error = ref('')
const installed = ref<any>(null)
const metrics = ref<any>(null)
async function loadMetrics() { metrics.value = await api('/api/v1/system/metrics') }
function mib(value: any) { return typeof value === 'number' ? (value / 1024 ** 2).toFixed(1) + ' MiB' : '不可用' }
async function componentAction(action: string, component?: string) {
  busy.value = true; error.value = ''
  try {
    const result = await api('/api/v1/system/components/' + action, component ? { component } : {})
    installed.value = await api('/api/v1/system/components')
    if (action === 'cleanup') error.value = '已清理 ' + result.removed + ' 个过期版本'
    await system.refresh()
  } catch { error.value = '操作失败，请检查网络、版本来源和组件验证结果，旧版本仍保留' }
  finally { busy.value = false }
}
const p115UpdateLabel = computed(() => {
  const checked = updates.value?.result?.p115
  if (!checked) return '未检查'
  if (checked.status !== 'checked') return '检查失败'
  const current = system.status?.p115_component
  if (current?.installed) {
    if (!current.running_commit) return '安装提交未知'
    return current.running_commit === checked.upstream_commit ? '最新' : '有更新'
  }
  return checked.update_available ? '有更新（未安装）' : '锁定版本最新（未安装）'
})
const sevenUpdateLabel = computed(() => {
  const checked = updates.value?.result
  if (!checked) return '未检查'
  if (checked.status !== 'checked') return '检查失败'
  const current = system.status?.extract_component?.version
  if (!current) return '组件不可用，无法判断更新'
  const parts = (value: string) => value.split('.').map(Number)
  const installed = parts(current), upstream = parts(checked.upstream)
  for (let index = 0; index < Math.max(installed.length, upstream.length); index++) {
    if ((installed[index] || 0) !== (upstream[index] || 0)) {
      return (installed[index] || 0) > (upstream[index] || 0) ? '高于上游发布版本' : '有更新'
    }
  }
  return '最新'
})
async function loadUpdates() { updates.value = await api('/api/v1/system/component-updates') }
onMounted(() => { loadMetrics().catch(() => {}); loadUpdates().catch(() => {}); api('/api/v1/system/components').then(value => { installed.value = value }).catch(() => {}) })
async function checkUpdates() {
  busy.value = true; error.value = ''
  try { await api('/api/v1/system/component-updates/check', {}); await loadUpdates() }
  catch { error.value = '检查失败，请稍后重试' }
  finally { busy.value = false }
}
async function toggleChecks() {
  try { await api('/api/v1/system/component-updates/settings', { enabled: !updates.value?.enabled }); await loadUpdates() }
  catch { error.value = '更新检查设置保存失败' }
}
</script>

<template>
  <section class="settings-section">
    <h2>运行状态</h2>
    <div class="import-actions"><v-btn size="small" variant="text" @click="loadMetrics().catch(() => {})">刷新内存状态</v-btn></div>
    <dl class="health-list">
      <div><dt>进程内存 RSS</dt><dd>{{ mib(metrics?.memory?.process?.rss_bytes) }}</dd></div>
      <div><dt>容器工作集</dt><dd>{{ mib(metrics?.memory?.container?.working_set_bytes) }}</dd></div>
      <div><dt>容器文件缓存</dt><dd>{{ mib(metrics?.memory?.container?.file_cache_bytes) }}</dd></div>
      <div><dt>执行 / 排队任务</dt><dd>{{ metrics ? metrics.active_jobs + ' / ' + metrics.ready_jobs : '加载中' }}</dd></div>
      <div><dt>调度状态</dt><dd>{{ system.status?.paused ? '已暂停' : '运行中' }}</dd></div>
      <div><dt>115 连接</dt><dd>{{ system.status?.adapter === 'disabled' ? '未配置' : system.status?.adapter === 'fake' ? '测试模式' : '已配置' }}</dd></div>
      <div><dt>p115 安装状态</dt><dd>{{ system.status?.p115_component?.installed ? '已安装' : '未安装' }}</dd></div>
      <div><dt>p115 版本</dt><dd>{{ system.status?.p115_component?.version || '未安装' }}</dd></div>
      <div><dt>p115 更新状态</dt><dd>{{ p115UpdateLabel }}</dd></div>
      <div><dt>p115 锁定提交</dt><dd class="path-cell">{{ system.status?.p115_component?.locked_commit || '-' }}</dd></div>
      <div><dt>p115 安装提交</dt><dd class="path-cell">{{ system.status?.p115_component?.running_commit || (system.status?.p115_component?.installed ? '无法识别' : '未安装') }}</dd></div>
      <div><dt>来源目录</dt><dd>{{ system.status?.sources.length || 0 }} 个</dd></div>
      <div><dt>本地执行器</dt><dd>{{ system.status?.local_formats || '-' }}</dd></div>
      <div><dt>7-Zip 版本</dt><dd>{{ system.status?.extract_component?.version || '未检测到' }}</dd></div>
      <div><dt>7-Zip 更新状态</dt><dd>{{ sevenUpdateLabel }}</dd></div>
      <div><dt>7-Zip 路径</dt><dd class="path-cell">{{ system.status?.extract_component?.path || '未配置' }}</dd></div>
      <div v-if="system.status?.extract_component?.error"><dt>7-Zip 检测</dt><dd>{{ system.status.extract_component.error === 'EXTRACTOR_VERSION_UNRECOGNIZED' ? '无法识别版本' : system.status.extract_component.error === 'EXTRACTOR_NOT_FOUND' ? '程序不存在' : '程序执行失败或超时' }}（{{ system.status.extract_component.error }}）</dd></div>
      <div><dt>解压组件更新</dt><dd>验证后随版本发布更新</dd></div>
      <div><dt>应用版本</dt><dd>{{ system.status?.version || '-' }}</dd></div>
      <div><dt>构建编号</dt><dd class="path-cell">{{ system.status?.build_revision || '-' }}</dd></div>
      <div><dt>构建时间</dt><dd>{{ system.status?.build_time || '-' }}</dd></div>
    </dl>
    <h3>组件更新</h3>
    <h4>7-Zip</h4>
    <p>{{ sevenUpdateLabel }}</p>
    <p v-if="updates?.result">{{ updates.result.status === 'failed' ? '上次检查失败，无法确认最新版本' : '上游版本：' + updates.result.upstream + '；项目已验证版本：' + updates.result.compatible }}</p>
    <p v-else>尚未检查上游版本</p>
    <h4>p115client</h4>
    <p>{{ p115UpdateLabel }}</p>
    <template v-if="updates?.result?.p115">
      <p>锁定提交：{{ updates.result.p115.locked_commit }}</p>
      <p>{{ updates.result.p115.installed ? '已安装版本：' + updates.result.p115.version : '当前环境未安装 p115client' }}</p>
      <p v-if="updates.result.p115.running_commit">实际安装提交：{{ updates.result.p115.running_commit }}</p>
      <p v-if="updates.result.p115.status === 'failed'">上次检查失败，无法确认上游版本</p>
      <p v-else>上游提交：<a :href="updates.result.p115.upstream_url" target="_blank" rel="noopener noreferrer">{{ updates.result.p115.upstream_commit }}</a> · {{ updates.result.p115.update_available ? '上游有变更，待兼容验证' : '锁定版本最新' }}</p>
    </template>
    <p v-else>尚未检查 p115 上游版本</p>
    <p>组件安装在配置目录中。7-Zip 对新任务生效；p115 更新后需重启容器生效。保留当前和上一版本，更早版本保留 7 天。</p>
    <p v-if="installed?.p115_restart_required">p115 版本已切换，请等待活动任务结束后重启容器。</p>
    <v-btn :disabled="busy || updates?.result?.status !== 'checked'" @click="componentAction('install', '7zip')">更新 7-Zip</v-btn>
    <v-btn :disabled="busy || updates?.result?.p115?.status !== 'checked'" @click="componentAction('install', 'p115')">更新 p115</v-btn>
    <v-btn :disabled="busy || !installed?.components?.['7zip']?.current" variant="text" @click="componentAction('rollback', '7zip')">回滚 7-Zip</v-btn>
    <v-btn :disabled="busy || !installed?.components?.p115?.current" variant="text" @click="componentAction('rollback', 'p115')">回滚 p115</v-btn>
    <v-btn :disabled="busy" variant="text" @click="componentAction('cleanup')">清理过期版本</v-btn>
    <v-btn :loading="busy" @click="checkUpdates">检查更新</v-btn>
    <v-btn v-if="updates" variant="text" @click="toggleChecks">{{ updates.enabled ? '关闭每日检查' : '开启每日检查' }}</v-btn>
    <p v-if="error">{{ error }}</p>
    <details v-if="updates?.history?.length"><summary>最近检查记录</summary>
      <p v-for="item in [...updates.history].reverse()" :key="item.checked_at">{{ new Date(item.checked_at * 1000).toLocaleString() }} · {{ item.status === 'checked' ? '上游 ' + item.upstream : '检查失败' }}</p>
      <p v-for="item in [...updates.history].reverse().filter((entry: any) => entry.p115)" :key="'p115-' + item.checked_at">{{ new Date(item.checked_at * 1000).toLocaleString() }} · p115 {{ item.p115.status === 'checked' ? item.p115.upstream_commit.slice(0, 12) : '检查失败' }}</p>
    </details>
  </section>
</template>
