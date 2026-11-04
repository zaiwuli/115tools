# 官方 xtractr 模块 + 薄 helper 验证

日期：2026-11-04。结论：路线可行，但尚不具备无功能损失替换当前实现的条件。

## 实际执行

在 .local/xtractr-module-probe 创建独立模块，require golift.io/xtractr v0.8.0；没有复制上游解码源码，也没有 replace 本地库。使用 Go 1.27.2 Windows amd64 下载、校验模块并编译。

sum.golang.org 直连超时后，通过 goproxy.cn 的模块与校验服务镜像完成下载，未关闭 Go 模块校验。

将现有 helper 源文件直接切换到官方模块后，编译明确失败：上游没有 NameMapper、ValidateOutput、WrapOutput、SquashRootName 字段，也没有 CUEInputs 函数。这些是本项目当前依赖的扩展。

为了验证基础可行性，隔离原型改用上游 MaxBytes / MaxFiles，暂时移除上述 hooks，CUE 输入枚举明确返回不支持。保留 JSON 请求、清单输出、SHA-256 和额外一轮嵌套逻辑。这个原型不是完整迁移实现。

## 同输入对照（20 次执行）

| 样本 | 官方模块薄封装 | 当前生产 helper |
| --- | --- | --- |
| 普通 ZIP、中文名 | 成功，JSON、文件清单和摘要正确 | 成功 |
| 嵌套 ZIP | 封装驱动额外解包成功 | 成功 |
| 加密 7z 正确密码 | 成功 | 成功 |
| 加密 7z 错误密码 | 非零退出，JSON 失败 | 非零退出，JSON 失败 |
| 路径穿越 | 拒绝，未产生越界文件 | 拒绝 |
| ZIP 链接条目 | 样本被拒绝 | 明确拒绝特殊文件 |
| MaxBytes=1 | 上游阻止写出，非零退出 | 预算阻止继续写出 |
| MaxFiles=1 | 上游阻止写出，非零退出 | 预算阻止继续写出 |
| 重复 same.txt 条目 | 返回成功，后条目覆盖前条目 | 返回 DUPLICATE_ARCHIVE_ENTRY |
| 280 字符文件名 | 失败 | 映射为短文件名并成功，返回映射清单 |

额外基础探针验证了不存在输入的非零失败行为。为排除薄封装统一错误映射的影响，诊断构建暴露测试错误，MaxBytes=1 的上游原始错误为 extracted size exceeds maximum bytes，确认限制来自上游而非最终输出统计。

## 证明了什么

1. 不复制解码库源码，直接用 Go module 编译独立 helper，技术上成立。
2. 与官方 xt CLI 不同，薄封装能利用上游 error 返回值构造可靠的 JSON 成功/失败和退出码。
3. 最新官方库已有字节及条目预算，不需要为了这两项继续维护原来的写入 hook。
4. 我们仍需维护少量协议、错误映射和调度外围逻辑，但不用因此维护整份解码实现。

## 尚未解决

- 长文件名映射：最新上游未提供当前 NameMapper hook，实测有回归。不能把后处理重命名当成解决方法，因为写出阶段已失败。
- 重复条目：现有“拒绝重复”的策略无法通过当前上游写入 hook 直接接入。ZIP 可做预检，但不能将 ZIP 预检外推为 RAR/7z/ISO 等全部格式保障。
- CUE 输入枚举：现有缓存和完整性流程依赖 CUEInputs，上游没有这个公开接口；隔离原型未实现，不算通过。
- 累计预算：本次只验证单包限制。封装额外嵌套时必须跨包共享剩余额度，不能每个内层包重新获得完整预算。
- 错误分类：原有统一映射会把新预算错误映射为损坏包。正式迁移需按上游导出错误类型适配，而不是依赖错误文本。
- Windows 路径及特殊条目的行为不能外推为 Linux 安全认证。

未测试 RAR、ISO/UDF、实际 CUE 音轨、缺卷恢复、Linux/arm64、取消恢复和资源峰值。当前的成功样本不构成全部格式兼容结论。

## 建议决策

不要马上删除 third_party/xtractr 或替换当前 helper。

推荐以“官方模块 + 独立薄 helper”为目标继续推进，但先解决长文件名、重复条目、CUE 枚举和嵌套累计预算四项阻塞：优先采用上游公开 API 或推动上游支持；无法满足时明确选择保留最小补丁或接受功能变化，不能静默降级。

主程序 Go 重构可先定义 ExtractBackend，继续调用当前 helper，避免将语言迁移和解码行为变化绑在一次发布。组件版本管理、更新检查和回滚也可先围绕当前 helper 建立。

## 材料

- 原型模块、锁文件和源码：.local/xtractr-module-probe。
- 对照脚本：.local/xtractr-module-probe/compare.py。
- 对照结果：.local/xtractr-module-probe/comparison-results.json。
- 上游固定版本：https://github.com/golift/xtractr/tree/v0.8.0。
- 上一轮 CLI 测试：extract-validation-results.md。

所有原型均隔离存放；本次未修改生产实现、依赖锁文件或现有二进制，未推送 Git。
