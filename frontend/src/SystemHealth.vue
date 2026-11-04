<script setup lang="ts">
import { useSystem } from './store'
import { ref, onMounted, computed } from 'vue'
import { api } from './api'
const system = useSystem()
const updates = ref<any>(null)
const busy = ref(false)
const error = ref('')
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
  if (!current) return '未安装'
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
onMounted(() => { loadUpdates().catch(() => {}) })
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
    <dl class="health-list">
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
    <p>更新通过应用版本或镜像发布安装，保留上一版本用于回退。</p>
    <v-btn :loading="busy" @click="checkUpdates">检查更新</v-btn>
    <v-btn v-if="updates" variant="text" @click="toggleChecks">{{ updates.enabled ? '关闭每日检查' : '开启每日检查' }}</v-btn>
    <p v-if="error">{{ error }}</p>
    <details v-if="updates?.history?.length"><summary>最近检查记录</summary>
      <p v-for="item in [...updates.history].reverse()" :key="item.checked_at">{{ new Date(item.checked_at * 1000).toLocaleString() }} · {{ item.status === 'checked' ? '上游 ' + item.upstream : '检查失败' }}</p>
      <p v-for="item in [...updates.history].reverse().filter((entry: any) => entry.p115)" :key="'p115-' + item.checked_at">{{ new Date(item.checked_at * 1000).toLocaleString() }} · p115 {{ item.p115.status === 'checked' ? item.p115.upstream_commit.slice(0, 12) : '检查失败' }}</p>
    </details>
  </section>
</template>
