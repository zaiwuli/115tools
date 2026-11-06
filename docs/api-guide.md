# 115tools API 接入说明

本文面向第三方工具，说明如何提交磁链、上传种子和查询批次。接口与网页共用地址和端口，无需另设后端端口。

## 1. 地址与认证

示例服务地址：`http://192.168.31.2:9056`。请替换为自己的地址，跨公网使用 HTTPS 或私人 VPN。

在网页“连接管理 → 添加 → API 令牌”创建令牌，可填写名称、备注，选择有效期或长期。

| 权限 | 能力 |
|---|---|
| 操作 | 可提交下载、修改设置及管理后端。属于完整管理权限，仅交给可信工具 |
| 只读 | 查看允许的状态接口，不能提交下载或修改设置 |

所有请求推荐使用请求头：

```http
Authorization: Bearer YOUR_API_TOKEN
```

`YOUR_API_TOKEN` 是占位文字，替换成实际令牌；Bearer 后有一个空格。令牌只在创建时显示一次，删除后失效，不应放进 URL、公开仓库或日志。不要提交真实令牌。

创建操作令牌后，导入接口要求认证；删除最后一个令牌不会恢复免密导入。后台页面是否免登录仍遵循原有配置，API 令牌不自动开启整个后台的登录保护。旧版仅导入令牌仍只允许提交和查询其对应批次。

## 2. 提交磁链

`POST /api/v1/offline/import`

请求头：Authorization 和 `Content-Type: application/json`。

| JSON 字段 | 类型 | 说明 |
|---|---|---|
| text | 字符串，必填 | 磁链文本，多条用换行分隔 |
| auto_extract | 布尔值，默认 true | 离线下载完成后是否进入自动解压流程 |

```json
{
  "text": "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567",
  "auto_extract": true
}
```

上面的磁链哈希仅用于说明格式，不是可下载资源。

Linux/macOS curl 示例：

```bash
curl --fail-with-body 'http://192.168.31.2:9056/api/v1/offline/import' \
  -H 'Authorization: Bearer YOUR_API_TOKEN' \
  -H 'Content-Type: application/json' \
  --data '{"text":"magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567","auto_extract":true}'
```

也可提交 ED2K 及后端支持的 HTTP/FTP 下载链接；具体链接有效性由后端解析和 115 服务判定，不是任意网页都能直接下载。

## 3. 上传种子文件

同一个接口：`POST /api/v1/offline/import`，正文改为 `multipart/form-data`。

| 表单字段 | 类型 | 说明 |
|---|---|---|
| file | 文件，必填 | 一个以 .torrent 结尾的有效种子，最大 2 MiB |
| auto_extract | 文本 | true 或 false，默认 true |

```bash
curl --fail-with-body 'http://192.168.31.2:9056/api/v1/offline/import' \
  -H 'Authorization: Bearer YOUR_API_TOKEN' \
  -F 'file=@example.torrent' \
  -F 'auto_extract=true'
```

Windows 使用 `curl.exe`，可以将上述命令合并成一行。使用 `-F` 时不要手动填写 Content-Type，客户端需要自动生成 multipart boundary。

每次上传一个种子；不要在同一个请求中同时上传种子和非空 text。不要把种子二进制直接塞进 JSON。

## 4. Python 调用示例

依赖 `requests`，从环境变量读取令牌，避免把令牌写进代码。

```python
import os
import requests

base = "http://192.168.31.2:9056"
headers = {"Authorization": "Bearer " + os.environ["TOOLS115_API_TOKEN"]}

# 提交磁链
response = requests.post(
    base + "/api/v1/offline/import",
    headers=headers,
    json={"text": "这里替换为实际磁链", "auto_extract": True},
    timeout=30,
)
response.raise_for_status()
print(response.json())

# 上传种子（二选一，按需调用）
with open("example.torrent", "rb") as torrent:
    response = requests.post(
        base + "/api/v1/offline/import",
        headers=headers,
        files={"file": ("example.torrent", torrent, "application/x-bittorrent")},
        data={"auto_extract": "true"},
        timeout=60,
    )
response.raise_for_status()
print(response.json())
```

## 5. 提交响应

成功接收返回 HTTP **202**，例如：

```json
{
  "batch_id": "示例批次ID",
  "queued": 1,
  "duplicates": 0,
  "invalid": 0,
  "message": "Queued 1; duplicates 0; invalid 0",
  "operation_id": "示例批次ID"
}
```

| 字段 | 含义 |
|---|---|
| batch_id | 本次导入的批次标识，用于查询 |
| queued | 本次新登记的数量 |
| duplicates | 已存在、没有新建的数量 |
| invalid | 无效链接数量 |
| message | 人类可读说明，不要依赖其文本格式判断状态 |
| operation_id | 初次提交返回的操作标识；幂等重放响应可能不含此字段，查询请使用 batch_id |

202 只表示登记排队，不代表远端下载、解压成功。queued 为 0 时检查 duplicates 和 invalid。实际下载需要配置有效 115 连接及必要的云目录，并启用任务调度。

## 6. 查询批次

`GET /api/v1/import/batches/{batch_id}`

携带与提交时相同的令牌：

```bash
curl --fail-with-body 'http://192.168.31.2:9056/api/v1/import/batches/YOUR_BATCH_ID' \
  -H 'Authorization: Bearer YOUR_API_TOKEN'
```

响应：

```json
{
  "batch_id": "示例批次ID",
  "items": [
    {"id": "示例任务ID", "name": "示例文件", "status": "downloading", "stage": "offline_poll"}
  ]
}
```

该接口只返回由同一令牌提交的批次。其他令牌、匿名提交或不同认证主体的批次可能返回 BATCH_NOT_FOUND，操作权限也不绕过该归属检查。建议间隔 10—30 秒查询，不要高频轮询。status 和 stage 分别是当前离线任务状态与处理阶段，应结合网页详情判断，不要把下载成功视为所有后处理均已完成。

## 7. 幂等与重试

提交时可附加 `Idempotency-Key` 请求头，值为每次业务请求生成的唯一字符串，最长 128 字符。

- 同一令牌、同一键、相同内容：返回原批次结果，避免重复登记。
- 同一键改成不同内容：返回 HTTP 409、IDEMPOTENCY_KEY_CONFLICT。
- 网络超时后重试：保持原键、原内容及 auto_extract 值，不要生成新键。
- 发起新的业务请求：使用新键。不要依赖后端自动过期清除旧键。

## 8. 常见错误

错误通常以如下 JSON 返回：

```json
{"error":{"code":"INVALID_TOKEN"}}
```

| HTTP / 错误码 | 含义与处理 |
|---|---|
| 401 INVALID_TOKEN | 令牌错误、过期或已删除 |
| 401 LOGIN_REQUIRED | 没有提供所需的认证信息 |
| 403 | 权限不足或跨站请求被拒绝 |
| 409 CLOUD_ROOT_REQUIRED | 配置 115 连接后仍未设置必要云目录，先在后台配置 |
| 409 IDEMPOTENCY_KEY_CONFLICT | 幂等键对应的请求内容发生变化 |
| 413 | 请求正文或文件过大；默认全请求限制约 5 MiB，种子限制 2 MiB |
| 422 INVALID_TORRENT | 种子无效或内容不完整 |
| 422 TORRENT_IMPORT_SEPARATELY | 种子与链接文本混传 |
| 422 NO_LINKS / VALIDATION_ERROR | 没有可解析的内容或字段类型不正确 |
| 404 BATCH_NOT_FOUND | 批次不存在或不属于当前令牌 |
| 500 INTERNAL_ERROR | 后端异常，先检查是否已经登记，再按原幂等键重试 |

## 9. 状态查看

`GET /api/status` 可查看应用版本、构建编号、当前令牌权限和组件状态；携带同样的 Authorization 请求头。响应含业务信息，不应公开转发完整内容。

本说明覆盖第三方提交与查询所需接口，其他管理接口不作为此文档的稳定契约。


### 内存诊断（只读）

`GET /api/v1/system/metrics` 沿用令牌鉴权，新增 `memory`：

- `process.rss_bytes` / `peak_rss_bytes`：当前进程驻留内存与峰值。
- `process.private_bytes` / `pss_bytes`：私有内存与按比例分摊的驻留内存。
- `container.usage_bytes` / `limit_bytes`：所属 cgroup 内存用量与上限。
- `container.anonymous_bytes` / `file_cache_bytes`：匿名内存与文件缓存。
- `container.working_set_bytes`：用量减去 inactive_file 的估算值，并非进程私有内存。

大小单位均为字节；不支持或读取不可用时字段为 null、available 为 false。Linux 支持 cgroup v1/v2。进程指标仅包含当前 Python 进程，容器指标包含同组子进程与文件缓存；父级 cgroup 限额未单独遍历。原有 cache_bytes 是数据库记录的磁盘下载缓存大小，不是内存。

部署后应在刚启动、空闲轮询一段时间及处理任务后分别采样，比较 RSS、私有内存和文件缓存，不能仅用容器总量判定泄漏。
