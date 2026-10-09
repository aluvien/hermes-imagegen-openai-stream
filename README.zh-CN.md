# hermes-imagegen-openai-stream

[English](README.md) | **简体中文**

为 **Hermes Agent** 提供基于 OpenAI Images 兼容接口的 **SSE 流式图片生成与编辑**。

本项目是 [duu261/hermes-imagegen-openai-compatible](https://github.com/duu261/hermes-imagegen-openai-compatible)
的 fork。在保留原插件网关配置、密钥隔离、错误分类和参考图片编辑能力的基础上，
将 GPT 图片模型的请求改为流式模式，支持接收并保存中间预览图与最终图片。

适用于支持 Images 流式接口的 CLIProxyAPI、New API 或其他 OpenAI 兼容网关。
**支持非流式 Images API 并不代表支持 SSE，请先确认网关与上游支持流式生图。**

## 与原插件有什么不同？

以下对比以原插件 **2.0.2** 为基准，本 fork 当前版本为 **2.0.3**。

| 功能 | 原插件 2.0.2 | 本项目 |
|---|---|---|
| GPT 图片请求 | 等待完整 JSON 响应 | SSE 流式接收 |
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

如果已经安装原插件，请先备份插件目录和相关配置，再替换安装；不要盲目覆盖本地修改。
运行中的 Hermes 进程需要重新加载插件：新 CLI 进程可以加载新版，
运行中的网关可由操作人员执行 `hermes gateway restart`。

## 配置

```bash
hermes config set image_gen.provider openai-compatible
hermes config set image_gen.model gpt-image-2.5-flare
hermes config set plugins.entries.openai-compatible.settings.base_url https://gateway.example/v1
hermes config set plugins.entries.openai-compatible.settings.quality auto
```

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

## 开发与验证

在具有 Hermes 运行依赖的 Python 环境中运行：

```bash
PYTHONPATH=/path/to/hermes-agent PYTHONDONTWRITEBYTECODE=1 \
  python -m unittest discover -s tests -v
hermes plugins doctor . --ci
hermes plugins validate .
```

本地修改版本已通过 46 项自动化测试、插件验证与 Plugin Doctor，
并完成已捕获真实 SSE 响应的离线回放，解码后的最终图片内容一致。
测试覆盖文生图、参考图片编辑、预览图、保活、提前结束、错误脱敏、
资源关闭，以及非 GPT 模型的原有 JSON 路径。

这些结果不代表所有网关都已完成在线兼容性测试。`scripts/live_e2e.py`
可以进行在线检查，但会调用实际生成接口并可能产生费用，不应无授权运行。

## 致谢与许可证

感谢 [duu261/hermes-imagegen-openai-compatible](https://github.com/duu261/hermes-imagegen-openai-compatible)
提供原始实现。本 fork 的主要修改是 GPT 图片请求的流式处理、预览图保存和相关测试。

采用 MIT 许可证，原项目的版权声明保留在 [LICENSE](LICENSE) 中。
更新记录见 [CHANGELOG.md](CHANGELOG.md)。
