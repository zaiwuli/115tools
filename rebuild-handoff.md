# 115work 下一阶段重构交接文档

用途：将本项目迁移到新仓库时，直接把本文交给下一次 AI 任务使用。本文是范围、来源和验收约束；未经用户确认，不要直接开始整库重写或删除当前实现。

## 1. 项目与来源地址

### 当前业务仓库

- 当前仓库：<https://github.com/zaiwuli/115work>
- 当前分支：`main`
- 最近已推送提交：`153982f`（通知方案简化与处理链结果汇总）
- 当前工作目录项目：`G:\codex项目\115work`
- 当前服务端口：5656
- 线上示例后端：`http://192.168.31.2:8056`（只读排查时可访问；不要未经用户授权发送通知、修改设置或删除远端文件）

### p115 来源

- Python 客户端仓库：<https://github.com/ChenyangGao/p115client>
- 当前固定提交：`856e4f8f09ce034534bd458f06d3dee4474a1698`
- 当前依赖声明：`pyproject.toml` 的 `p115` optional dependency，uv.lock 同步锁定。
- 当前调用适配层：`app/adapters/p115.py`
- 业务代码不能直接依赖 p115client 内部 API；应只通过适配层或未来的 p115 组件协议调用。

### 解压来源

- 原始 Go 解码库：<https://github.com/golift/xtractr>
- 当前核查版本：`v0.8.0`
- 独立 CLI：<https://github.com/Unpackerr/xt>
- 当前核查 CLI 版本：`v0.3.0`
- 当前参考应用：<https://github.com/zaiwuli/unpackflow>（在线 API 核查时返回 404，可能为私有、改名或不可公开访问；不要据此推断已删除）
- 本地参考副本：`.references/unpackflow`
- 当前生产 helper 源码：`extractor/`、`third_party/xtractr/`
- 当前生产二进制：`bin/115work-extract.exe`（Linux 构建名为 `bin/115work-extract`）
- 当前 helper 调用层：`app/adapters/extract_worker.py`

相关调查文档：

- `docs/third-party-updates-design.md`：早期第三方更新机制设计。
- `docs/extract-upstream-assessment.md`：上游仓库和 CLI 发布物核查。
- `docs/extract-validation-results.md`：官方 `xt v0.3.0` 直接调用实测。
- `docs/xtractr-module-validation.md`：官方 xtractr 模块 + 薄 helper 实测。

## 2. 已确认的事实

### p115

p115client 是 Python 库，不是独立程序。当前主程序直接导入它。它可以独立做版本检查、依赖更新和回滚，但若仍在同一个 Python 主进程中，更新后需要重启主程序。

如果未来主程序改成 Go，p115 必须变成单独 Python 服务或改为 Go 适配；本次重构不要同时假定已有 Go 115 客户端，也不要把 p115client 源码复制进新仓库。

### 官方 xt

`Unpackerr/xt v0.3.0` 有 Linux amd64/arm64、Windows 等预编译包，可以直接安装，不需要复制 xtractr 源码。

但实测发现：错误密码、损坏包和输入不存在时，官方 CLI 会日志报错但返回退出码 0；CLI 没有暴露当前项目所需的文件数／大小预算；重复条目覆盖；嵌套行为不同。因此不能直接把 `xt` 当成当前 helper 的无损替代。

### 官方 xtractr 模块

将现有 helper 切换为 `golift.io/xtractr v0.8.0` 后，发现上游 API 没有当前项目使用的 `NameMapper`、`ValidateOutput`、`WrapOutput`、`SquashRootName` 和 `CUEInputs` 接口。上游已有 `MaxBytes`、`MaxFiles` 等基础预算。

因此“官方模块 + 极薄 helper”可以编译和返回 JSON，但仍会丢失当前长文件名映射、重复条目拒绝、CUE 输入枚举等能力。不要把验证原型直接当生产实现。

## 3. 用户明确目标

用户希望：

1. 主程序和第三方组件分开维护。
2. p115 不复制源码，跟随上游版本更新。
3. extract 不把整份 xtractr 源码塞在业务仓库中，优先直接使用可更新的官方程序。
4. 组件能定期检查更新、显示当前版本、支持验证后更新和回滚。
5. 用户曾提出 Go 主程序 + p115 + extract 三模块架构；随后询问是否需要整体重构。保留 Python、先解耦是此前 AI 的建议，尚未得到用户对最终实施路线的明确确认。
6. 新仓库中的 AI 能根据本文继续实现，不需要重新猜测仓库来源和技术取舍。

## 4. 推荐目标架构（当前阶段）

下面是此前 AI 建议的分阶段路线，不是用户已经确认的架构决定。开始实施前，应确认新仓库是直接重建 Go 主程序，还是先在 Python 基线上解耦。用户强调轻量与少维护，不要默认新增多后端、协议或补丁体系。以下保留现有 helper 的设计仅用于迁移安全，不代表应长期维护两套后端。

若选择渐进迁移，第一阶段先做组件解耦：

```text
Python 主程序（暂不重写）
  ├─ 业务 API、数据库、队列、规则、通知、任务链
  ├─ p115 适配边界
  │    └─ p115client（Python 依赖，独立版本记录）
  └─ ExtractBackend 边界
       ├─ CurrentHelperBackend（迁移期默认，保留当前安全和协议）
       └─ XtBackend（官方 xt，验证通过后可选）
```

第二阶段才评估：

```text
Go 主程序
  ├─ API、任务状态和调度
  ├─ p115 独立 Python 服务（若没有完整 Go 替代）
  └─ extract 官方独立程序或验证后的 helper
```

不要同时做 Python → Go、p115 服务拆分和 extract 行为切换。每一项都要有独立可回滚提交。

## 5. 新仓库第一阶段要做什么

### A. 建立组件清单

新增 `components.lock.json` 或等价配置，至少记录：

- `p115client`：仓库、精确 commit、Python/uv 锁版本。
- `extract.xt`：来源仓库、发行版本、平台、下载地址、SHA-256、签名状态。
- `extract.current_helper`：协议版本、构建提交、是否默认启用。
- 主程序版本、构建提交和组件清单版本。

页面显示“当前运行版本”和“最新兼容版本”，不能把上游最新提交直接显示成可安装版本。

### B. 统一 p115 边界

盘点所有 `p115client` 导入，只允许 `app/adapters/p115.py` 保留直接依赖。适配层输出稳定 DTO 和错误类型，业务层不接触远端返回字典、p115client 异常或签名细节。

加入只读契约测试：登录／Cookie 初始化、目录分页和 CID 校验、离线提交和轮询、直链下载、云解压查询、密码、超时、限流及取消语义。真实账号写操作不能放到无人值守 CI。

### C. 统一 extract 边界

定义 `ExtractBackend`，输入包含：源文件、staging 目录、密码、输出预算、是否提层、嵌套规则；输出包含：成功／失败、错误分类、文件清单、总字节、组件版本。

当前 helper 继续作为默认后端，确保现有任务链不变。`XtBackend` 只在显式配置开启时使用；不支持的能力必须显示为“不支持”，不能静默忽略。

所有后端都必须：

- 使用独立 staging，成功后由主程序发布。
- 不把用户下载目录直接交给工具扫描。
- 任务取消时回收进程和部分输出。
- 解压失败、空包、部分输出不能进入成功通知和原包删除流程。

### D. 更新检查和回滚

- 默认每天检查，可关闭和手动检查。
- 检查只读元数据，有限重试和限流退避。
- p115 的可用更新随应用镜像发布；extract 的官方二进制可独立下载和切换，但先验证摘要、平台、版本输出和测试样本。
- 每个版本独立目录；活动任务固定旧组件路径，新任务才使用新版本。
- 保留上一验证版本；失败时切回，不强杀正在写文件的任务。
- 初期不提供容器内自动安装，不挂 Docker socket，不使用 `@latest`。

## 6. 明确暂时不做

- 不把完整 UnpackFlow 当成解压后端接入。
- 不直接把官方 xt v0.3.0 设为生产默认。
- 不删除 `third_party/xtractr`，直到长文件名、重复条目、CUE 和预算差异被明确处理。
- 不把日志关键词当成成功判定。
- 不在一次提交里同时迁移 Go、拆 p115 服务和替换 extract。
- 不为了“少维护”牺牲路径安全、输出预算和任务链正确性；如果用户确认接受行为变化，必须单独记录取舍并更新验收。

## 7. 实施顺序

1. 新仓库导入本文和当前业务基线；确认数据库兼容与运行方式。
2. 建立组件清单和版本自报，不改变运行行为。
3. 收口 p115 导入，补充契约测试。
4. 建立 ExtractBackend，CurrentHelperBackend 默认保持现状。
5. 实现组件检查页、历史记录、摘要／签名校验和回滚记录。
6. 在隔离环境完成 XtBackend 对照测试；只有满足验收才允许启用。
7. 再决定是否创建独立 p115 Python 服务。
8. 最后单独评估 Go 主程序，不以本次组件解耦为前置承诺。

## 8. 验收标准

### 版本和更新

- 断网、限流或来源错误不能显示“已是最新”。
- 当前版本、上游候选、最新兼容版本和实际运行组件可追溯。
- 校验失败、架构不匹配和签名不可信的组件不能启用。
- 更新失败可回到上一版本，不影响数据库和已完成任务。

### p115

- 上游客户端变更不穿透业务层。
- 远端提交后本地断线时能核对，不重复提交。
- 取消、超时、限流、密码错误和远端未知结果有明确状态。

### extract

- 错误密码、损坏包、输入不存在不会报成功。
- 路径穿越、链接／特殊文件、重复输出和预算限制符合选定后端的明确策略。
- 长文件名、CUE、嵌套、分卷和多平台行为有样本结果。
- 取消不会遗留可发布的部分输出。

### 任务链

- 解压失败不能删除原包或发送成功通知。
- 种子下载、清理、云解压、本地兜底和完成后处理的状态仍由主程序统一决定。
- 活动任务不因组件更新被强制中断。

## 9. 给下一次 AI 的第一条工作指令

先阅读本文及以下文件：

```text
docs/rebuild-handoff.md
docs/extract-upstream-assessment.md
docs/extract-validation-results.md
docs/xtractr-module-validation.md
app/adapters/p115.py
app/adapters/extract_worker.py
extractor/main.go
pyproject.toml
Dockerfile
```

然后只做只读盘点，输出：

1. p115client 的所有导入点及业务耦合。
2. extract helper 的调用协议、预算、CUE 和文件发布流程。
3. 新仓库迁移所需的最小文件集合。
4. 哪些测试必须保留。

盘点完成前不要删除源码、切换默认后端、重写主程序或执行真实 115 写操作。所有结论都要引用文件路径和测试证据。盘点后向用户确认 Go 重建与渐进迁移的选择，以及是否接受官方 xt 已知的行为差异；不要把此前 AI 的建议当作用户已经批准的实现范围。

## 10. 当前工作树注意事项

当前仓库还有未提交的设计文档：

- `docs/third-party-updates-design.md`
- `docs/extract-upstream-assessment.md`
- `docs/extract-validation-results.md`
- `docs/xtractr-module-validation.md`
- 本交接文档 `docs/rebuild-handoff.md`

新仓库创建时可以只迁移文档和经过确认的业务基线；不要把 `.local` 验证产物或临时下载的二进制作为生产依赖提交。
