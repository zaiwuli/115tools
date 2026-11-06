<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { Plus, RefreshCw, X, Save, Play, Shield, Link, KeyRound, Search, Trash2, Archive, FileClock } from 'lucide-vue-next'
import { api, date, stateLabel, textLabel } from './api'
import { useSystem } from './store'
import SettingsNav from './SettingsNav.vue'
import RecentEvents from './RecentEvents.vue'
import SeedRuleDesigner from './SeedRuleDesigner.vue'
const ruleSearch = ref('')
const ruleSource = ref('')
const visibleRules = computed(() => items.value.filter(item =>
  (!ruleSearch.value || item.name.toLowerCase().includes(ruleSearch.value.toLowerCase())) &&
  (!ruleSource.value || (ruleSource.value === 'all' ? !item.source_id : item.source_id === ruleSource.value)),
))
function sourceLabel(id: string) {
  return id === '115-torrent' ? '种子下载' : id === '115-offline' ? '115离线下载' : id ? sources.value.find(x => x.id === id)?.name || '已删除来源' : '所有普通来源'
}
const system = useSystem()
const canWrite = computed(() => system.status?.role !== 'readonly')

const route = useRoute()
const view = computed(() => String(route.meta.view || route.params.view))
const titles: Record<string, string> = { connections: '连接管理', rules: '处理规则', security: '安全管理', logs: '工作日志' }
const items = ref<any[]>([])
const sources = ref<any[]>([])
const error = ref('')
const message = ref('')
const barkMessages = ref<any[]>([])
const barkAddress = window.location.origin + '/bark'
async function copyBarkAddress() {
  try { await navigator.clipboard.writeText(barkAddress); message.value = 'Bark 地址已复制' }
  catch { error.value = '复制失败，请选中地址手动复制' }
}
const runtimeItems = computed(() => items.value.filter(x => ['cloud_monitor','offline_manual','offline_download','discover'].includes(x.stage) || String(x.kind).startsWith('BARK')))
const busy = ref(false)
const opened = ref(false)
const editing = ref<any>(null)
const name = ref('')
const kind = ref('115')
const url = ref('')
const root = ref('/')
const credential = ref('')
const sourceId = ref('')
const pattern = ref('*')
const originalAction = ref('retain')
const documentAction = ref('register')
const workflow = ref('auto')
const autoFallback = ref(false)
const disableRecursion = ref(false)
const recurseIso = ref(false)
const squashRoot = ref(true)
const cleanupEnabled = ref(false)
const cleanupSuffixes = ref('')
const cleanupRecursive = ref(true)
const extractIsos = ref(true)
const archiveDate = ref(false)
const archiveCid = ref('')
const archivePath = ref('')
const failedCid = ref('')
const documentLinks = ref(false)
const trigger = ref('interval')
const interval = ref(300)
const expression = ref('0 1 * * *')
const timezone = ref('Asia/Shanghai')
const role = ref('readonly')
const tokenName = ref('')
const tokenNote = ref('')
const connectionTokens = ref<any[]>([])
const ttl = ref<number | null>(null)
const createdToken = ref('')
const newPassword = ref('')
const previewName = ref('')
const preview = ref<any>(null)
const diagnosis = ref<any>(null)
const directory = ref('0')
const browser = ref<any[]>([])
const browseConnection = ref('')
const deletingRule = ref<any>(null)
const deletingConnection = ref<any>(null)

async function load() {
  busy.value = true
  error.value = ''
  try {
    const path = view.value === 'security' ? 'security/tokens' : view.value
    items.value = (await api('/api/v1/' + path)).items
    if (view.value === 'logs' && system.status?.role !== 'readonly') barkMessages.value = (await api('/api/v1/bark/messages')).items
    if (view.value === 'connections') connectionTokens.value = (await api('/api/v1/security/tokens')).items
    sources.value = (await api('/api/v1/sources')).items
  } catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
async function command(path: string, body: unknown = {}, method?: string) {
  busy.value = true
  error.value = ''
  try {
    const result = await api(path, body, method)
    message.value = '操作成功'
    await load()
    return result
  } catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
function open(item?: any) {
  editing.value = item || null
  credential.value = ''
  kind.value = item?.kind || '115'
  tokenNote.value = ''
  role.value = 'admin'
  createdToken.value = ''
  name.value = item?.name || (view.value === 'connections' ? '115' : '')
  sourceId.value = item?.source_id || ''
  pattern.value = item?.config.pattern || '*'
  originalAction.value = item?.config.original_action || 'retain'
  documentAction.value = item?.config.document_action || 'register'
  workflow.value = item?.config.workflow || 'auto'
  autoFallback.value = item?.config.auto_fallback || false
  disableRecursion.value = item?.config.disable_recursion ?? false
  recurseIso.value = item?.config.recurse_iso ?? false
  squashRoot.value = item?.config.squash_root ?? true
  cleanupEnabled.value = item?.config.cleanup_enabled ?? false
  cleanupSuffixes.value = (item?.config.cleanup_suffixes || []).join(', ')
  cleanupRecursive.value = item?.config.cleanup_recursive ?? true
  extractIsos.value = item?.config.extract_isos ?? true
  archiveDate.value = item?.config.archive_date ?? false
  archiveCid.value = item?.config.archive_cid || ''
  archivePath.value = item?.config.archive_path || ''
  failedCid.value = item?.config.cloud_failed_cid || ''
  documentLinks.value = item?.config.document_links || false
  trigger.value = item?.trigger || 'interval'
  interval.value = item?.config.seconds || 300
  expression.value = item?.config.expression || '0 1 * * *'
  timezone.value = item?.timezone || 'Asia/Shanghai'
  opened.value = true
}
async function save() {
  let result
  if (view.value === 'connections' && kind.value === 'token') {
    result = await command('/api/v1/security/tokens', { name: name.value, note: tokenNote.value, role: role.value, ttl_days: ttl.value })
    if (result) createdToken.value = result.token
  } else if (view.value === 'connections') {
    result = await command('/api/v1/connections', { name: name.value, kind: kind.value,
      config: { root_cid: directory.value },
      credential: credential.value })
  } else if (view.value === 'rules') {
    const config = { pattern: pattern.value, original_action: originalAction.value,
      document_action: documentAction.value, workflow: workflow.value, auto_fallback: autoFallback.value,
      document_links: documentLinks.value, archive_date: archiveDate.value,
      disable_recursion: disableRecursion.value, recurse_iso: recurseIso.value,
      squash_root: squashRoot.value,
      cleanup_enabled: cleanupEnabled.value,
      cleanup_suffixes: cleanupSuffixes.value.split(/[,，\s]+/).filter(Boolean),
      cleanup_recursive: cleanupRecursive.value,
      extract_isos: extractIsos.value,
      ...(archiveCid.value ? { archive_cid: archiveCid.value } : {}),
      ...(archivePath.value ? { archive_path: archivePath.value } : {}),
      ...(failedCid.value ? { cloud_failed_cid: failedCid.value } : {}) }
    const body = { name: name.value, source_id: sourceId.value, config,
      ...(editing.value ? { expected_revision: editing.value.revision } : {}) }
    result = await command('/api/v1/rules' + (editing.value ? '/' + editing.value.id : ''), body,
      editing.value ? 'PUT' : 'POST')
  } else {
    result = await command('/api/v1/schedules' + (editing.value ? '/' + editing.value.id : ''), { name: name.value, source_id: sourceId.value,
      trigger: trigger.value, config: trigger.value === 'cron' ? { expression: expression.value } : { seconds: interval.value },
      timezone: timezone.value, misfire: 'coalesce',
      ...(editing.value ? { expected_revision: editing.value.revision, enabled: editing.value.enabled } : {}),
    }, editing.value ? 'PUT' : 'POST')
  }
  if (result) { opened.value = false; credential.value = '' }
}
async function saveCredentials() {
  const result = await command('/api/v1/connections/' + editing.value.id + '/credentials',
    { credential: credential.value, expected_revision: editing.value.revision })
  if (result) {
    credential.value = ''
    opened.value = false
    message.value = 'Cookie 已保存，115连接已自动启用'
  }
}
async function browse(item: any) {
  browseConnection.value = item.id
  const body = item.kind === '115' ? { cid: directory.value } : { path: item.config.root }
  const result = await command('/api/v1/connections/' + item.id + '/browse', body)
  if (result) browser.value = result.items
}
function reload() { window.location.reload() }
watch(view, () => { items.value = []; message.value = ''; void load() }, { immediate: true })
watch(() => system.refreshVersion, () => void load())
</script>

<template>
  <section v-mobile-tables class="workspace">
    <header class="page-heading"><div><div class="eyebrow">115tools</div><h1>{{ view === 'connections' ? '系统设置' : titles[view] }}</h1></div>
      <div class="heading-actions"><v-btn v-if="view === 'logs'" variant="text" :disabled="busy" @click="load"><RefreshCw :size="17" class="mr-2" />刷新</v-btn><v-btn v-if="canWrite && ['connections','rules','schedules'].includes(view)" color="primary" @click="open()"><Plus :size="17" class="mr-2" />添加</v-btn>
        </div>
    </header>
    <SettingsNav v-if="view === 'connections'" />
    <nav v-if="view === 'logs'" class="section-nav" aria-label="工作日志分页">
      <RouterLink to="/logs" :class="{ active: !route.query.section }">最近事件</RouterLink>
      <RouterLink to="/logs?section=runtime" :class="{ active: route.query.section === 'runtime' }">运行日志</RouterLink>
      <RouterLink v-if="canWrite" to="/logs?section=bark" :class="{ active: route.query.section === 'bark' }">Bark 消息</RouterLink>
      <RouterLink to="/logs?section=details" :class="{ active: route.query.section === 'details' }">工作详情</RouterLink>
    </nav>
    <p v-if="error" role="alert" class="error-text alert">{{ error }}</p>
    <p v-if="message" role="status" class="success-text alert">{{ message }}</p>
    <v-progress-linear v-if="busy" indeterminate color="primary" height="2" />

    <template v-if="view === 'connections'">

      <div class="table-wrap"><table><thead><tr><th>连接</th><th>类型 / 协议</th><th>健康</th><th>凭据</th><th>操作</th></tr></thead><tbody>
        <tr v-for="item in items" :key="item.id"><td><strong>{{ item.name }}</strong><small>{{ item.config.url || ('CID ' + item.config.root_cid) }}</small></td>
          <td>{{ stateLabel(item.kind) }}</td><td>{{ stateLabel(item.health) }}</td><td>{{ item.credential_configured ? '已加密保存' : '未配置' }}<small>版本 {{ item.revision }}</small></td>
          <td><div class="row-actions"><button aria-label="诊断连接" title="诊断连接" @click="command('/api/v1/connections/' + item.id + '/diagnose').then(x => diagnosis = x)"><Shield :size="18" /></button>
            <button v-if="item.kind === '115'" aria-label="启用115连接" title="启用115连接" @click="command('/api/v1/connections/' + item.id + '/activate')"><Play :size="18" /></button>
            <button aria-label="浏览目录" title="浏览目录" @click="browse(item)"><Search :size="18" /></button>
            <button aria-label="更新凭据" title="更新凭据" @click="open(item)"><KeyRound :size="18" /></button>
            <button v-if="canWrite" :disabled="busy" aria-label="删除115账户" title="删除115账户" @click="deletingConnection = item"><Trash2 :size="18" /></button></div></td></tr>
        <tr v-for="item in connectionTokens" :key="item.id">
          <td><strong>{{ item.name }}</strong><small>{{ item.note || '无备注' }}</small></td>
          <td>API 令牌</td><td>{{ item.expires && item.expires < Date.now() / 1000 ? '已过期' : '有效' }}</td>
          <td>{{ item.role === 'admin' ? '操作' : item.role === 'readonly' ? '只读' : '操作（仅导入）' }}<small>{{ item.expires ? '到期 ' + date(item.expires) : '长期' }}</small></td>
          <td><div class="row-actions"><button v-if="canWrite" :disabled="busy" aria-label="删除API令牌" title="删除API令牌" @click="command('/api/v1/security/tokens/' + item.id, undefined, 'DELETE')"><Trash2 :size="18" /></button></div></td>
        </tr>
      </tbody></table><div v-if="!items.length && !connectionTokens.length" class="empty"><Link :size="32" /><span>暂无连接</span></div></div>
      <p v-if="diagnosis" class="mono mt-5">{{ stateLabel(diagnosis.status) }}</p>
      <section v-if="browser.length" class="settings-section"><h2>目录列表</h2><div v-for="file in browser" :key="file.id || file.fid" class="event-line"><span>{{ file.name }}</span><small>{{ file.path || file.cid }} · {{ file.size }}</small></div></section>
    </template>

    <template v-else-if="view === 'rules'">
      <div class="form-grid mt-5"><v-text-field v-model="ruleSearch" label="搜索规则名称" /><v-select v-model="ruleSource" label="筛选来源" :items="[{title:'全部规则',value:''},{title:'所有普通来源',value:'all'},{title:'115离线下载',value:'115-offline'},{title:'种子下载',value:'115-torrent'},...sources.map(x => ({title:x.name,value:x.id}))]" /></div>
      <div class="table-wrap"><table><thead><tr><th>规则</th><th>来源</th><th>匹配</th><th>流程</th><th>原包策略</th><th>操作</th></tr></thead><tbody>
        <tr v-for="item in visibleRules" :key="item.id"><td><strong>{{ item.name }}</strong><small>{{ item.enabled ? '已启用' : '已停用' }} · 版本 {{ item.revision }}</small></td><td>{{ sourceLabel(item.source_id) }}</td><td>{{ item.config.pattern || '*' }}</td><td>{{ stateLabel(item.config.workflow || 'auto') }}<small>{{ item.config.auto_fallback ? '云端失败后转本地' : '不自动回退' }}</small></td><td>{{ stateLabel(item.config.original_action || 'retain') }}<small v-if="item.config.cleanup_enabled">解压后清理指定类型</small></td>
          <td><div v-if="canWrite" class="row-actions"><button class="file-link" aria-label="编辑规则" title="编辑规则" @click="open(item)"><Save :size="17" /></button><button aria-label="删除规则" title="删除规则" @click="deletingRule = item"><Trash2 :size="17" /></button></div></td></tr>
      </tbody></table><div v-if="!visibleRules.length" class="empty"><Archive :size="32" /><span>{{ items.length ? '没有符合筛选条件的规则' : '暂无规则' }}</span></div></div>
      <section v-if="canWrite" class="settings-section"><h2>规则预览</h2><div class="form-grid"><v-text-field v-model="previewName" label="文件名" /><v-btn variant="outlined" :disabled="!previewName" @click="command('/api/v1/rules/preview', { name: previewName, size: 0, source_id: sourceId }).then(x => preview = x)"><Search :size="17" class="mr-2" />预览</v-btn></div><p v-if="preview" class="mono mt-4">{{ stateLabel(preview.policy.workflow || 'auto') }}</p></section>
    </template>

    <template v-else-if="view === 'schedules'">
      <div class="table-wrap"><table><thead><tr><th>计划</th><th>触发</th><th>下次运行</th><th>时区</th><th>启用</th><th>操作</th></tr></thead><tbody>
        <tr v-for="item in items" :key="item.id"><td><strong>{{ item.name }}</strong><small>{{ sources.find(x => x.id === item.source_id)?.name || '维护' }}</small></td><td>{{ stateLabel(item.trigger) }}<small>{{ item.config.expression || (item.config.seconds + ' 秒') }}</small></td><td>{{ date(item.next_run) }}</td><td>{{ item.timezone }}</td><td><v-switch :model-value="item.enabled" :disabled="!canWrite" color="primary" hide-details :aria-label="'启用' + item.name" @update:model-value="command('/api/v1/schedules/' + item.id, { enabled: !!$event, expected_revision: item.revision }, 'PATCH')" /></td>
          <td><div v-if="canWrite" class="row-actions"><button class="file-link" aria-label="编辑计划" title="编辑计划" @click="open(item)"><Save :size="17" /></button><button class="file-link" aria-label="立即执行" title="立即执行" @click="command('/api/v1/schedules/' + item.id + '/run')"><Play :size="17" /></button></div></td></tr>
      </tbody></table><div v-if="!items.length" class="empty"><FileClock :size="32" /><span>暂无自动计划</span></div></div>
    </template>

    <template v-else-if="view === 'security'">
      <section class="settings-section"><h2>API 令牌</h2><p>令牌已统一移至连接管理。</p><RouterLink to="/connections">前往连接管理</RouterLink></section>
      <section class="settings-section"><h2>管理员密码</h2><div class="form-grid"><v-text-field v-model="newPassword" type="password" label="新密码" autocomplete="new-password" /><v-btn variant="outlined" :disabled="newPassword.length < 12" @click="command('/api/v1/security/password', { password: newPassword }).then(x => x && reload())"><Save :size="17" class="mr-2" />更新密码</v-btn></div></section>
    </template>

    <RecentEvents v-else-if="view === 'logs' && !route.query.section" />
    <section v-else-if="view === 'logs' && route.query.section === 'bark'" class="settings-section"><h2>Bark 消息</h2><p>仅保留最近50条，单字段最多4096字符；不会记录设备 key。收到的旧通知无法补回正文。</p><article v-for="(item,index) in barkMessages" :key="index" class="event-line"><div><small>{{ date(item.received_at) }} · {{ item.group }}</small><h3>{{ item.title || '无标题' }}</h3><p style="white-space:pre-wrap;overflow-wrap:anywhere">{{ item.body || '无正文' }}</p><small v-if="item.match">{{ item.match.result }} · {{ item.match.path }}</small></div></article><p v-if="!barkMessages.length">暂无消息，请发送一条新通知。</p></section>
    <template v-else>
      <div class="table-wrap"><table><thead><tr><th>时间</th><th>事件 / 操作</th><th>详情</th></tr></thead><tbody>
        <tr v-for="item in (route.query.section === 'runtime' ? runtimeItems : items)" :key="item.id"><td>{{ date(item.created_at) }}</td><td>{{ textLabel(item.kind || item.action) }}</td><td class="path-cell"><div>{{ textLabel(item.message || '') }}</div><details v-if="item.details && Object.keys(item.details).length"><summary>工作详情</summary><dl><template v-for="(value, key) in item.details" :key="key"><dt>{{ key }}</dt><dd class="path-cell">{{ typeof value === 'object' ? JSON.stringify(value, null, 2) : (String(key) === '当前状态' ? textLabel(String(value)) : String(value)) }}</dd></template></dl></details></td></tr>
      </tbody></table><div v-if="!items.length" class="empty">暂无记录</div></div>
    </template>


    <v-dialog v-model="opened" :max-width="view === 'rules' ? 800 : 600"><SeedRuleDesigner v-if="view === 'rules' && sourceId === '115-torrent'" :rule="editing" :initial-name="name" @source-change="sourceId = ''" @saved="load" @close="opened = false" /><v-card v-else><header class="dialog-heading"><h2>{{ editing ? '编辑' : '添加' }}{{ titles[view] }}</h2><v-btn icon variant="text" aria-label="关闭" @click="opened = false"><X :size="20" /></v-btn></header>
      <v-card-text><v-form class="dialog-form" @submit.prevent="editing && view === 'connections' ? saveCredentials() : save()">
        <template v-if="view === 'connections'">
          <v-select v-if="!editing" v-model="kind" :items="[{ title: '115 Cookie', value: '115' }, { title: 'API 令牌', value: 'token' }]" label="类型" />
          <v-text-field v-if="!editing" v-model="name" :label="kind === 'token' ? '令牌名称' : '连接名称'" />
          <template v-if="kind === 'token'">
            <v-textarea v-model="tokenNote" label="备注" maxlength="500" rows="2" />
            <v-select v-model="role" :items="[{ title: '操作', value: 'admin' }, { title: '只读', value: 'readonly' }]" label="权限" />
            <v-select v-model="ttl" :items="[{ title: '长期', value: null }, { title: '30 天', value: 30 }, { title: '90 天', value: 90 }, { title: '365 天', value: 365 }]" label="有效期" />
          </template>
          <template v-else><v-text-field v-if="!editing" v-model="directory" label="根 CID" /><v-text-field v-model="credential" type="password" label="115 Cookie" autocomplete="off" /></template>
        </template>
        <template v-else><v-text-field v-model="name" label="名称" /><v-select v-model="sourceId" :items="[{ title: '所有来源', value: '' }, ...(view === 'rules' ? [{ title: '115离线下载', value: '115-offline' }, { title: '种子下载', value: '115-torrent' }] : []), ...sources.map(x => ({ title: x.name, value: x.id }))]" label="来源" aria-label="来源" />
          <template v-if="view === 'rules'">
            <h3>01 · 匹配与执行</h3>
            <v-text-field v-model="pattern" label="文件名模式" />
            <v-select v-model="workflow" :items="[{ title: '自动', value: 'auto' }, { title: '本地', value: 'local' }, { title: '云端', value: 'cloud' }, { title: '忽略', value: 'ignore' }]" label="工作流" />
            <h3>02 · 完成后处理</h3>
            <v-select v-model="originalAction" :items="[{ title: '保留', value: 'retain' }, { title: '移动', value: 'move' }, { title: '归档', value: 'archive' }, { title: '删除（需逐任务批准）', value: 'delete' }]" label="原包策略" />
            <v-text-field v-if="['archive','move'].includes(originalAction)" v-model="archiveCid" label="云端目标目录 CID（留空使用系统归档目录）" />
            <v-switch v-if="originalAction === 'archive'" v-model="archiveDate" label="按日期创建归档子目录" hint="使用任务首次登记日期，跨天重试保持不变；归档是移动原包，不会重新压缩" persistent-hint />
            <p v-if="originalAction === 'delete'" class="muted">规则不会自动批准删除；解压成功后仍需逐任务批准，并遵循延迟删除设置。</p>
            <v-select v-model="documentAction" :items="[{ title: '只读登记', value: 'register' }, { title: '复制', value: 'copy' }, { title: '归档', value: 'archive' }]" label="普通文档" />
            <h3>03 · 解压与清理选项</h3>
            <v-switch v-model="disableRecursion" label="禁止递归解压" color="primary" />
            <v-switch v-model="squashRoot" label="同名文件夹提层" color="primary" />
            <v-switch v-model="autoFallback" label="云解压失败后自动下载到本地" color="primary" />
            <v-switch v-model="cleanupEnabled" label="解压后删除指定后缀文件" color="primary" />
            <template v-if="cleanupEnabled">
              <v-text-field v-model="cleanupSuffixes" label="删除后缀（例如 .url, .lnk, .nfo）" />
              <v-switch v-model="cleanupRecursive" label="包含解压输出子目录" color="primary" />
            </template>
          </template>
          <template v-else><v-select v-model="trigger" :items="[{ title: '固定间隔', value: 'interval' }, { title: 'Cron', value: 'cron' }]" label="触发" /><v-text-field v-if="trigger === 'interval'" v-model.number="interval" type="number" min="15" label="间隔 / 秒" /><v-text-field v-else v-model="expression" label="Cron 表达式" /><v-text-field v-model="timezone" label="时区" /></template>
        </template>
        <v-btn type="submit" color="primary" :disabled="busy || (view === 'connections' && (kind === 'token' ? !name.trim() : !credential.trim()))"><Save :size="17" class="mr-2" />{{ view === 'connections' && kind === 'token' ? '创建令牌' : '保存' }}</v-btn>
      </v-form></v-card-text></v-card></v-dialog>
  </section>
  <v-dialog :model-value="!!createdToken" max-width="600" persistent><v-card><v-card-title>API 令牌已创建</v-card-title><v-card-text><p>令牌仅显示一次，请复制保存。</p><v-textarea :model-value="createdToken" label="API 令牌" readonly rows="3" /></v-card-text><v-card-actions><v-spacer /><v-btn color="primary" @click="createdToken = ''">已保存</v-btn></v-card-actions></v-card></v-dialog>
  <v-dialog :model-value="!!deletingRule" max-width="480" @update:model-value="!$event && (deletingRule = null)"><v-card><v-card-title>删除规则“{{ deletingRule?.name }}”？</v-card-title><v-card-actions><v-spacer /><v-btn variant="text" @click="deletingRule = null">返回</v-btn><v-btn color="error" @click="command('/api/v1/rules/' + deletingRule.id, undefined, 'DELETE').then(x => x && (deletingRule = null))">删除</v-btn></v-card-actions></v-card></v-dialog>
  <v-dialog :model-value="!!deletingConnection" max-width="480" @update:model-value="!$event && (deletingConnection = null)"><v-card><v-card-title>删除115账户？</v-card-title><v-card-text>删除“{{ deletingConnection?.name }}”及保存的 Cookie，并停用115连接。云文件、任务记录和处理规则不会删除。</v-card-text><v-card-actions><v-spacer /><v-btn :disabled="busy" variant="text" @click="deletingConnection = null">返回</v-btn><v-btn :disabled="busy" color="error" @click="command('/api/v1/connections/' + deletingConnection.id, undefined, 'DELETE').then(x => { if (x) { deletingConnection = null; browser = []; diagnosis = null; system.refresh() } })">确认删除</v-btn></v-card-actions></v-card></v-dialog>
</template>
