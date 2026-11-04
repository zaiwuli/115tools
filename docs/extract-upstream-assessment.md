# 解压组件直接安装核查与选型建议

核查日期：2026-11-04。范围：当前项目、已保存的 UnpackFlow 参考源码，以及 GitHub 在线仓库和发行版源码。本文只记录调查与设计，不变更运行组件。

## 1. 结论

可以使用上游现成的解压可执行程序，无须先把它的解码源码复制到主程序仓库。

具体候选不是 `go install golift.io/xtractr`，而是 **Unpackerr/xt（eXtractor Tool）**。xtractr 是库；其 cmd/xt 目录当前仅保留说明，明确将命令行工具指向 Unpackerr/xt。

但目前核查到的 xt v0.3.0 不能直接等价替换本项目 helper：缺少可靠的结构化成功/失败协议、输出上限参数，以及部分现有处理能力的明确映射。建议先验证 xt 适配，验证通过再移除 vendored 源码，而不是一开始就另建解码库维护分支。

此前“xtractr 只有 Go 库、没有现成命令行程序”的描述不完整，应以本次上游核查为准。

## 2. 三个仓库分别是什么

| 来源 | 实际性质 | 能否直接安装 |
| --- | --- | --- |
| zaiwuli/unpackflow | 本项目当前解码源码的直接参考来源，是含 UI、监控、队列的完整应用 | 本地 README 声明支持 Docker；不是独立单任务解压器 |
| golift/xtractr | 原始 Go 解码及队列库 | 可作为 Go 模块依赖，不是直接运行的命令 |
| Unpackerr/xt | 基于 xtractr 的独立 CLI | 可以，发布了预编译可执行程序及安装包 |

本次匿名访问 zaiwuli/unpackflow GitHub API 返回 404。可能是私有、改名或已不可公开访问，不能认定仓库删除，也不能确认其最新发布情况。对它的结论仅来自本地参考副本和本项目来源说明。

直接把完整 UnpackFlow 再作为服务接入，会引入第二套业务调度、监控、清理和通知，不适合作为单纯解码后端。

## 3. 在线确认的版本和安装方式

### xtractr

- 本次读取到的最新发布为 v0.8.0，发行版没有附带独立二进制资产。
- v0.8.0 的 cmd/xt/README.md 指向 https://github.com/Unpackerr/xt。
- 库已提供 MaxBytes、MaxFiles、MaxRatio 等能力；这不代表所有依赖它的 CLI 已暴露这些参数。

### xt

- 本次读取到的最新发行版为 v0.3.0。
- 发行资产包括 Linux amd64/arm64 的 tar.gz、deb、rpm、apk 等，以及 Windows amd64/arm64 ZIP。
- 提供 checksums 文件以及 .sig 签名文件。安装实现仍需确认签名格式和可信公钥来源，不能仅因存在 .sig 就宣称已完成真实性验证。
- README 提供系统包安装、macOS Homebrew、Windows 下载和 Go 安装方法。

固定版本源码安装命令：

```sh
go install golift.io/xt@v0.3.0
```

这会在安装阶段下载源码并编译，但不需要把源码复制进本项目；生产环境可直接采用对应平台的发布二进制，不需要安装 Go。

xt v0.3.0 的 go.mod 依赖 xtractr `v0.6.2-0.20260922231041-fec876ff1836`，并不是本次最新的 v0.8.0。因此检查更新时应追踪 xt 发行版本，不能把 xtractr 的最新版本当作已安装 CLI 的实际解码版本。其源码构建声明 Go 1.27.0，不能沿用当前 Docker Go 1.26.4 构建阶段而不调整。

## 4. 已核查的调用能力

v0.3.0 支持指定单个文件和输出目录：

```sh
xt --output /work/task-123/staging /work/task-123/input/example.zip
```

也支持 --job-file，读取 JSON、TOML、YAML、XML 参数文件，可配置 passwords、输出路径、权限、扩展名和 squashRoot。

建议使用每任务的私有 JSON 文件传密码，而不是 --password 命令行参数。JSON 示例：

```json
{
  "paths": ["/work/task-123/input/example.zip"],
  "output": "/work/task-123/staging",
  "passwords": [],
  "squashRoot": false,
  "verbose": false
}
```

文件必须限权、任务结束清除；同时检查上游日志会不会打印敏感信息。所有子进程通过参数数组调用，不拼接 shell 字符串。

## 5. 与现有 helper 的差距

| 需求 | xt v0.3.0 源码核查 | 接入判断 |
| --- | --- | --- |
| 单文件解压、指定输出 | 支持 | 可以适配 |
| RAR/7z 密码、多密码 | CLI 和 job 文件支持 | 需样本验证，不能外推所有加密格式 |
| 同名根目录提层 | squash-root 支持 | 验证行为与现有规则一致 |
| 结构化结果和文件清单 | 没有发现 JSON 结果协议；verbose 是文本日志 | 不能直接替换现有 JSON 协议 |
| 解压错误退出码 | Extract 返回 void，遇到错误记录日志并继续；main 未据此退出非零 | 不能将退出码 0 当成功 |
| 文件数 / 写出大小上限 | Job 和 CLI 未暴露对应选项，processArchive 未设置 XFile 的这些字段 | 不能依赖该 CLI 强制现有预算 |
| 进度 | 文本进度 | 可展示粗粒度日志，不能当稳定机器接口 |
| 嵌套解压 | --max-depth 控制搜索目录深度；递归参数代码处于注释状态 | 不能当成嵌套压缩包层数控制 |
| CUE、长路径、ISO、分卷 | 相关能力取决于其实际依赖版本 | 必须跑本项目样本，不能承诺完全一致 |
| 取消 | 外部进程可由主程序停止 | 仍需进程组回收、临时产物清理和状态确认 |

空目录、部分输出和日志“Done”都不足以证明完整解压成功。解压后统计文件大小也不能替代解压过程中强制的资源上限。

## 6. 推荐架构

采用用户提出的 Go 主程序方向时，解压层定义稳定接口，但先不把业务代码绑定到 xt 的日志格式：

```text
Go 主程序：任务、规则、状态、下载、清理、通知
  ├─ p115 薄服务：上游 p115client
  └─ ExtractBackend 接口
       ├─ XtBackend：调用官方 xt，待验证
       └─ ExistingHelperBackend：现有 helper，迁移期兼容
```

ExtractBackend 统一输入 source、staging、密码、预算和规则，统一输出状态、错误类型、文件清单、字节数及组件版本。不支持的参数必须拒绝或明确标记，不允许悄悄忽略。

对 xt 适配时，每次只提交一个受控任务；不能把用户下载根目录直接交给它自主扫描。输出先进入独立 staging，通过验证后再由主程序发布到最终目录，清理原包仍由主程序决定。

路径、进程、内存和磁盘隔离属于宿主责任。简单文本日志包装不能补出可靠的失败协议，也不能等价替代写入前的文件数和总量限制。

## 7. 如何在不复制源码的前提下补齐差距

优先顺序：

1. 向 xt 上游确认或推动：JSON 结果、可靠非零退出码、预算参数和密码安全传递。只有上游发布了覆盖需求的版本，才作为正式替换候选。
2. 若暂时无法满足，维护极薄 helper，使用 Go module 引入明确版本的上游 xtractr，取消 vendor 目录复制和本地 replace。主项目只调用发布二进制，仍可独立更新。
3. 如果最新上游库仍缺少必需安全能力，保留现有 helper 为兼容后端，明确记录阻塞项；不为“去源码”而降低保护或偷偷吞掉功能。

第二条只维护调用封装，不维护整份解码源码。它是否足够替代当前本地补丁，需要对 v0.8.0 的路径安全、重复输出和流式预算逐项验证，不能现在就保证可直接删补丁。

建议先给官方 xt 一个独立验证阶段。若仍不满足第 5 节关键项，再选第二条。Go 主程序重构可以使用统一 ExtractBackend，不需要等解码工具选型结束后才设计接口。

## 8. 更新与回滚设计

- 为每个版本使用独立目录，记录 xt 精确版本、所含 xtractr 版本、平台、摘要、验证状态。
- 每日检查官方发行版本，不在生产执行 go install @latest。
- 下载后验证可信签名 / 摘要、架构、版本输出和测试样本，通过后才允许切换。
- 同一任务固定工具路径；更新后新任务使用新版，旧任务继续旧版，旧进程结束前不删除文件。
- 至少保留上一验证版本，可切回；无可用版本时阻止新解压，不影响 UI、下载管理和 p115 服务。
- 二进制放在有执行权限的持久化位置；NAS noexec 挂载要明确报错，不自动改宿主挂载配置。

## 9. 正式切换前的验证

本次完成的是在线源码、接口和发布产物清单核查，没有下载安装 xt 二进制，也没有执行解压样本；当前结论不能标记为生产兼容认证。

下一步实测应覆盖：普通 ZIP、加密 RAR/7z、损坏包、错误密码、不存在输入、分卷缺失、空包、CUE/音轨、长中文路径、ISO、嵌套包；观察退出码、日志和实际输出。另需测试路径穿越、链接、重复输出、预算、取消与超时。

尤其验证失败能否无歧义识别，输出限制能否在运行中强制执行。若失败仍返回 0 且缺乏结构化结果，不应使用文本关键词判断来自动删除原包或发布成功通知。

## 10. 核查依据

- 当前来源声明：THIRD_PARTY_NOTICES.md、extractor/README.md、extractor/go.mod。
- 本地参考：.references/unpackflow/README.md（声明仅发布完整 Docker 应用）。
- xtractr 仓库：https://github.com/golift/xtractr
- xtractr v0.8.0：https://github.com/golift/xtractr/releases/tag/v0.8.0
- CLI 跳转说明：https://github.com/golift/xtractr/blob/v0.8.0/cmd/xt/README.md
- xt 仓库与安装：https://github.com/Unpackerr/xt
- xt 发布资产：https://github.com/Unpackerr/xt/releases/tag/v0.3.0
- 固定版本 CLI：https://github.com/Unpackerr/xt/blob/v0.3.0/main.go
- 固定版本作业参数：https://github.com/Unpackerr/xt/blob/v0.3.0/pkg/xt/job.go
- 固定版本执行与错误处理：https://github.com/Unpackerr/xt/blob/v0.3.0/pkg/xt/xt.go
- 固定版本手册：https://github.com/Unpackerr/xt/blob/v0.3.0/MANUAL.md
- 固定版本依赖：https://github.com/Unpackerr/xt/blob/v0.3.0/go.mod

本文更新了此前 third-party-updates-design.md 对“没有现成 CLI”的不完整判断。旧文档中的复制源码及自建发布方案作为备选，不作为已确定路线。
