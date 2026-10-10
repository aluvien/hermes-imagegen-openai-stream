# hermes-imagegen-openai-stream

[English](README.md) | **简体中文**

为 **Hermes Agent** 提供基于 OpenAI Images 兼容接口的 **SSE 流式图片生成与编辑**。

本项目是 [duu261/hermes-imagegen-openai-compatible](https://github.com/duu261/hermes-imagegen-openai-compatible)
的 fork。在保留原插件网关配置、密钥隔离、错误分类和参考图片编辑能力的基础上，
将 GPT 图片模型的请求改为流式模式，支持接收并保存中间预览图与最终图片。

适用于支持 Images 流式接口的 CLIProxyAPI、New API 或其他 OpenAI 兼容网关。
**SSE 预览仍要求网关与上游支持流式生图。2.0.5 也能接收同一次流式请求返回的完整 JSON 图片。**

## 与原插件有什么不同？

以下对比以原插件 **2.0.2** 为基准，本 fork 当前版本为 **2.0.5**。

| 功能 | 原插件 2.0.2 | 本项目 |
|---|---|---|
| GPT 图片请求 | 等待完整 JSON 响应 | 接收 SSE，或兼容同一次请求返回的完整 JSON |
| 中间预览 | 不请求 | 请求最多 2 张并保存 |
| 图片尺寸 | 按宽高比指定固定尺寸 | `size: auto`，由后端决定 |
| 最终结果解析 | JSON 的 `data[0]` | 流中的 completed 事件 |
| 自动重试 | 使用 SDK 默认行为 | 关闭，减少重复生成风险 |
| 网关、密钥与模型配置 | 独立于聊天配置 | 保留原有方式 |
| 参考图片编辑 | 支持 | 保留，GPT 模型使用流式响应 |
| 非 GPT 网关模型 | 非流式 JSON | 保留原有方式 |

**这不是 Hermes 界面实时预览功能。** 插件会在生成过程中接收并保存预览图，
但同步的 `image_generate` 工具仍在最终图片完成后返回结果。

## 安装

从本 fork 仓库安装，而不是通过原插件的目录条目安装：

```bash
hermes plugins install aluvien/hermes-imagegen-openai-stream
hermes plugins enable openai-compatible
```

当前仍保留插件 ID 与 provider ID：**`openai-compatible`**，以兼容已有配置。
本项目是原插件的替代版本，不应以相同 ID 与原插件同时启用。

如果已经安装原插件，请使用下方迁移步骤，而不是直接执行首次安装命令。
运行中的 Hermes 进程需要重新加载插件：新 CLI 进程可以加载新版，
运行中的网关可由操作人员执行 `hermes gateway restart`。

## 从原插件迁移

两个仓库都声明 `name: openai-compatible`，所以迁移会替换同一个插件目录。
`hermes plugins update openai-compatible` **不会**把原作者的安装来源切换到这个 fork。
需要从 fork 强制重新安装；如果旧插件锁定了提交，切换来源时还必须显式指定
完整的 40 位提交 SHA。

以下命令使用 macOS/Linux 的 Bash 兼容语法。请在目标 Hermes 配置档案的上下文中运行，
所有命令（包括重启）都要使用相同的档案选择方式。

### 1. 替换前先备份

通过 Hermes 查询当前配置档案目录，不假定一定是 `~/.hermes`。
备份放在 `plugins/` 外，防止被发现为另一个插件。

```bash
(
  set -eu
  config_path="$(hermes config path)"
  env_path="$(hermes config env-path)"
  hermes_home="$(dirname "$config_path")"
  backup_dir="$hermes_home/plugins-backup/openai-compatible-before-stream-$(date +%Y%m%d-%H%M%S)"
  umask 077
  mkdir -p "$backup_dir"
  cp -Rp "$hermes_home/plugins/openai-compatible" "$backup_dir/"
  cp -p "$config_path" "$backup_dir/config.yaml"
  if [ -f "$env_path" ]; then cp -p "$env_path" "$backup_dir/.env"; fi
  if [ -f "$hermes_home/plugins/.install-metadata.json" ]; then
    cp -p "$hermes_home/plugins/.install-metadata.json" "$backup_dir/.install-metadata.json"
  fi
  printf 'Backup saved to: %s\n' "$backup_dir"
)
```

记下打印的备份位置。备份可能包含密钥，请只保存在本地，禁止上传。
切换到不同仓库会使用干净的插件目录，不要依赖跨来源重装保留本地修改或未跟踪文件。
如果插件目录是符号链接，安装器会拒绝替换；请单独处理这种开发安装，不要盲目删除链接。

### 2. 安装已验证的 fork 提交

```bash
hermes plugins install aluvien/hermes-imagegen-openai-stream --force --ref ffdbf3558c12e02080f9a0cfd824b4385a945982 --enable
```

这会安装指定的已验证提交，不一定是 `main` 上最新的提交。
它适用于从锁定或未锁定的原插件安装切换来源，也适用于将旧版 fork 升级到 2.0.5。请阅读 Hermes 的依赖与安全提示，
不要用 `--no-deps` 替换正在启用的插件。

插件 ID 仍为 `openai-compatible`，已有网关地址、密钥变量和密钥继续保留。
如果同一个 Hermes 档案已经使用这个后端和模型，配置不用重新设置。
只有需要将选择项改为本文示例时，才执行：

```bash
hermes config set image_gen.provider openai-compatible
hermes config set image_gen.model gpt-image-2.5-flare
```

迁移时**不要**把正常工作的网关替换成 `https://gateway.example/v1`，也不要覆盖已有的
自定义 `key_env`。下方配置章节用于首次配置或主动更改路由。

### 3. 检查并重新加载

```bash
hermes plugins list
hermes plugins show openai-compatible
hermes config get image_gen.provider
hermes config get image_gen.model
```

确认安装来源是这个 fork、锁定提交是 `ffdbf35…`、插件版本是 `2.0.5`。
CLI 会话请关闭后重新启动。如果使用消息网关，执行：

```bash
hermes gateway restart
```

这会重启对应网关，请在可以中断当前工作的时机执行。
插件加载成功不代表网关已支持 SSE；真实生图验证可能产生费用。

### 后续升级与回退

将已有安装升级到 **2.0.5**：先备份，再执行上面第 2 步的锁定安装命令，最后按第 3 步
检查并重新加载。安装命令也适用于 Windows PowerShell；上方 Bash 备份脚本用于
macOS/Linux/WSL，不能直接在原生 PowerShell 中执行。原生 Windows 请先用本地备份方式
备份当前档案的插件目录、配置、密钥文件及安装元数据，再替换插件。所有命令使用同一个
Hermes 档案；不要上传包含密钥的备份。

上述迁移命令会锁定提交。以后升级时，从 fork 重新安装，使用 `--force --ref`
指定新的、已验证的完整 SHA。普通更新命令不能移动锁定的安装。
如果是从这个 fork 首次安装的未锁定版本，则可以使用：

```bash
hermes plugins update openai-compatible
```

若要回到原插件 `2.0.2` 代码，可以显式安装其已知提交，然后重新加载 Hermes：

```bash
hermes plugins install duu261/hermes-imagegen-openai-compatible --force --ref 5e20ebbcfe5c6a44fd8685f16e729221dcaad829 --enable
```

这会同时恢复原仓库的安装来源记录和代码。原插件中的本地修改需要从备份中单独恢复；
如果期间修改过其他插件或设置，不要盲目覆盖整份配置或共享安装来源记录文件。

## 配置

```bash
hermes config set image_gen.provider openai-compatible
hermes config set image_gen.model gpt-image-2.5-flare
hermes config set plugins.entries.openai-compatible.settings.base_url https://gateway.example/v1
hermes config set plugins.entries.openai-compatible.settings.quality auto
```

`https://gateway.example/v1` 是占位地址，请换成自己的 API 根地址，通常以 `/v1` 结尾。

将网关密钥保存到 Hermes 当前配置档案的秘密变量 `OPENAI_COMPAT_IMAGE_API_KEY`。
默认档案通常使用 `~/.hermes/.env`；不要把实际密钥写入仓库或 `config.yaml`。
以下命令显式指定默认密钥变量名（默认值已经相同，也可以省略）：

```bash
hermes config set plugins.entries.openai-compatible.settings.key_env OPENAI_COMPAT_IMAGE_API_KEY
```

`key_env` 填的是保存密钥的变量名，不是实际密钥。设置变量名不会自动保存密钥，
仍需在 Hermes 当前配置档案中设置 `OPENAI_COMPAT_IMAGE_API_KEY` 的值。
只有已经把密钥保存在其他变量中时，才应将 `key_env` 改成那个变量的名称。

| 设置（`plugins.entries.openai-compatible.settings.*`） | 默认值 | 说明 |
|---|---|---|
| `base_url` | 无，必须配置 | 网关 API 根地址，通常以 `/v1` 结尾 |
| `key_env` | `OPENAI_COMPAT_IMAGE_API_KEY` | Hermes 秘密变量名称，不是密钥本身 |
| `quality` | `medium` | `auto`、`low`、`medium`、`high`、`xhigh`、`max` |

本介绍统一使用 `gpt-image-2.5-flare` 作为示例模型；这不会修改插件代码的默认模型。
模型通过 `image_gen.model` 选择。支持的 GPT 模型包括 `gpt-image-1`、`gpt-image-1.5`、
`gpt-image-2`、`gpt-image-2.5`、`gpt-image-2.5-flare`、`gpt-image-2.5-sunburst`。
这些名称仅表示插件能够转发，不保证你的网关或账号已提供相应模型。

画质后缀优先于 `quality` 设置，例如 `gpt-image-2.5-flare-high` 会发送
`model: gpt-image-2.5-flare` 和 `quality: high`。实际支持的画质由网关和上游决定。
其他网关模型名称原样传递，不附加 GPT 专用的尺寸、画质和流式参数。

## 请求与响应

GPT 图片模型的文生图请求示例：

```json
{
  "model": "gpt-image-2.5-flare",
  "prompt": "一只布偶小猫讲解如何做包子",
  "n": 1,
  "size": "auto",
  "quality": "auto",
  "stream": true,
  "partial_images": 2
}
```

`quality: auto` 是上述配置的示例，并非插件强制使用的画质。
`stream`、`partial_images` 和 GPT 模型的 `size` 当前是固定请求行为。

- 无参考图片时调用 `/images/generations`；提供参考图片时调用 `/images/edits`。
- 忽略 keepalive 保活事件，接收并保存最多 2 张 partial_image 预览图。
- 接收 completed 事件后保存最终图片；没有预览图也可以成功。
- 流提前结束且没有最终图片时返回错误，不把预览图误当成成品。
- 结果包含 `partial_images` 预览路径、`requested_size`、`requested_quality` 和实际 `output_size`。

`partial_images: 2` 不代表生成两张成品，成品数量仍由 `n: 1` 控制。
图片通过 Hermes 的保存函数写入 `$HERMES_HOME/cache/generated/images/`。

## 限制与安全边界

- **不自动降级或重发**：GPT 流式请求失败后不会自动改成非流式请求。
  SDK 自动重试也已关闭；人工重新生成仍可能产生额外费用。
- **尺寸自动选择**：GPT 请求使用 `size: auto`，`aspect_ratio` 保留为结果元数据，
  不再映射为固定请求尺寸。可在提示词中描述构图，但不能保证精确宽高比。
- **超时**：SDK 超时为每次网络操作 240 秒，不是整个生成过程的总时限；
  网关、代理或上游仍可能提前终止连接。
- **参考图片**：支持本地文件、图片 data URL 和公开 HTTPS 地址，最多 16 张。
  HTTPS 来源使用 Hermes 的 SSRF 防护、禁用重定向、检查图片文件签名，并限制为 50 MB。
- **网关连接**：默认要求 HTTPS；纯 HTTP 仅允许所有解析地址都位于明确支持的
  回环、局域网、CGNAT/Tailscale 或 IPv6 ULA 网络，拒绝公网与元数据地址。
- **输出 URL**：网关返回的图片 URL 由 Hermes 标准下载函数处理，
  与参考图片的严格 URL 加载器不是同一安全边界。只连接可信网关。
- **密钥隔离**：不读写聊天使用的 `OPENAI_API_KEY` 或 `OPENAI_BASE_URL`，
  不向网关转发 OpenAI 组织或项目标识，错误信息中的密钥会脱敏。

## 审核拒绝与空结果

### JSON 响应兼容（2.0.5）

部分网关在请求 `stream: true` 后仍返回普通 JSON 的 `data[0].b64_json`，甚至将响应头
错误标为 `text/event-stream`。插件按实际响应内容识别 SSE 或完整 JSON，不重新发送请求。
结果中的 `response_format` 标明实际格式 `sse` / `json`；`stream: true` 仅表示请求了流式模式，
不保证网关实际进行了流式输出。JSON 响应没有中间预览图。损坏的响应仍报错，JSON 响应体
或单个 SSE 事件超过 64 MiB 会被拒绝。原有配置和请求参数不变。
已通过真实 SDK 离线回放已捕获的“错误 SSE 响应头 + JSON 图片”数据，使用一次模拟请求
恢复出字节完全一致的图片；未重复发送收费请求。

### 错误处理建议

- 上游明确返回 `moderation_blocked`、`content_policy_violation` 等审核错误码时，插件向
  Hermes 返回 `content_policy_violation`；HTTP 403 和流式失败也适用，不再误报为密钥错误。
- 错误说明要求 Hermes 解释拒绝原因，并用用户的语言提供真正安全的替代提示词，只保留
  合规的主题、画风和构图。附带通用的宁静风景提示词作为兜底，不是个性化改写，也不保证
  审核通过。输入或生成结果都可能触发审核，插件不会直接断言原提示词违规。
- 空结果或缺失最终图片仍返回 `empty_response` / `invalid_response`，不等于确认违规。
  建议 Hermes 提醒检查提示词、参考图片，经用户同意后重试；重复失败则检查网关日志和流式支持。
- 流中的失败、错误或不完整事件保留脱敏后的上游说明。插件不自动改写、重发或重试；重试可能再次计费。
- **前置检查建议**：Hermes 调用生图工具之前应检查提示词和参考图片，必要时提供安全改写并请用户确认。
  这是智能体工作流建议，不代表插件已安装自动前置检测器。插件没有简单违禁词黑名单，也没有新增
  Moderation API 请求。生图服务的安全审核不保证能提前判断，更不保证第三方网关完整转发拒绝原因。
  如需独立 Moderation API 前置审核，必须先验证网关及密钥是否支持，不能因为走 Codex 生图就假定可用。

## 开发与验证

在具有 Hermes 运行依赖的 Python 环境中运行：

```bash
PYTHONPATH=/path/to/hermes-agent PYTHONDONTWRITEBYTECODE=1 \
  python -m unittest discover -s tests -v
hermes plugins doctor . --ci
hermes plugins validate .
```

本地修改版本已通过 65 项自动化测试、插件验证与 Plugin Doctor，
并完成已捕获真实 SSE 响应的离线回放，解码后的最终图片内容一致。
测试覆盖文生图、参考图片编辑、预览图、保活、提前结束、错误脱敏、
资源关闭，以及非 GPT 模型的原有 JSON 路径。

README 命令语法已通过本机 Hermes CLI 解析器检查。五条配置写入命令在临时档案中
实际执行，备份脚本也用测试文件执行过。锁定来源迁移及回退的控制流程在该档案中
验证，Git 克隆和依赖发布使用模拟实现；没有替换正在使用的插件、重启网关或发送收费生图请求。
命令参考：[Hermes CLI](https://hermes-agent.nousresearch.com/docs/reference/cli-commands/)
与[插件安装说明](https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins/)。

这些结果不代表所有网关都已完成在线兼容性测试。`scripts/live_e2e.py`
可以进行在线检查，但会调用实际生成接口并可能产生费用，不应无授权运行。

## 致谢与许可证

感谢 [duu261/hermes-imagegen-openai-compatible](https://github.com/duu261/hermes-imagegen-openai-compatible)
提供原始实现。本 fork 的主要修改是 GPT 图片请求的流式处理、预览图保存和相关测试。

采用 MIT 许可证，原项目的版权声明保留在 [LICENSE](LICENSE) 中。
更新记录见 [CHANGELOG.md](CHANGELOG.md)。
