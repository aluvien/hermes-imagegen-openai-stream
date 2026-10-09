# hermes-imagegen-openai-stream

**English** | [简体中文](README.zh-CN.md)

**SSE streaming image generation and editing for Hermes Agent**, through OpenAI Images-compatible gateways.

This project is a fork of
[duu261/hermes-imagegen-openai-compatible](https://github.com/duu261/hermes-imagegen-openai-compatible).
It retains the original gateway configuration, isolated credentials, error categories, and reference-image
editing, while adding streaming requests for GPT image models with saved intermediate previews and final images.

Use it with CLIProxyAPI, New API, or another gateway that supports streaming Images endpoints.
**Support for non-streaming Images requests does not imply SSE support. Check both your gateway and upstream.**

## What changed from the original plugin?

This comparison uses upstream **2.0.2** as the baseline. This fork is currently **2.0.3**.

| Feature | Original 2.0.2 | This fork |
|---|---|---|
| GPT image requests | Wait for a complete JSON response | Consume SSE events |
| Intermediate previews | Not requested | Request and save up to 2 |
| Image dimensions | Fixed size derived from aspect ratio | `size: auto`, chosen by the backend |
| Final image parsing | JSON `data[0]` | Stream completed event |
| Automatic retries | SDK defaults | Disabled to reduce duplicate generation risks |
| Gateway, credentials, and model settings | Separate from chat settings | Preserved |
| Reference-image editing | Supported | Preserved; GPT models use streaming |
| Non-GPT gateway models | Non-streaming JSON | Preserved |

**This does not add live previews to the Hermes UI.** The plugin receives and saves previews during
generation, but the synchronous `image_generate` tool returns only after the final image is complete.

## Installation

Install from this fork, not from the original plugin's catalog entry:

```bash
hermes plugins install aluvien/hermes-imagegen-openai-stream
hermes plugins enable openai-compatible
```

The plugin and provider ID remains **`openai-compatible`** for configuration compatibility.
This fork replaces the original plugin; do not enable both under the same ID.

If the original is already installed, back up the plugin directory and relevant configuration before
replacing it. Do not blindly overwrite local modifications. New CLI processes can load the updated
plugin; a running gateway can be restarted by its operator with `hermes gateway restart`.

## Configuration

```bash
hermes config set image_gen.provider openai-compatible
hermes config set image_gen.model gpt-image-2.5-flare
hermes config set plugins.entries.openai-compatible.settings.base_url https://gateway.example/v1
hermes config set plugins.entries.openai-compatible.settings.quality auto
```

Store the gateway key in the current Hermes profile's secret variable `OPENAI_COMPAT_IMAGE_API_KEY`.
The default profile typically uses `~/.hermes/.env`. Never commit a real key or put it in `config.yaml`.
To explicitly select the default key variable, use the following command. It is optional because the
plugin already defaults to the same variable:

```bash
hermes config set plugins.entries.openai-compatible.settings.key_env OPENAI_COMPAT_IMAGE_API_KEY
```

`key_env` is the **name of the variable holding the key**, not the key itself. Setting its name does
not store a credential: you must still set the value of `OPENAI_COMPAT_IMAGE_API_KEY` in the current
Hermes profile. Use another variable name only when your key is actually stored under that name.

| Setting (`plugins.entries.openai-compatible.settings.*`) | Default | Meaning |
|---|---|---|
| `base_url` | None; required | Gateway API root, usually ending in `/v1` |
| `key_env` | `OPENAI_COMPAT_IMAGE_API_KEY` | Hermes secret variable name, not the credential |
| `quality` | `medium` | `auto`, `low`, `medium`, `high`, `xhigh`, or `max` |

All model examples in this guide use `gpt-image-2.5-flare`; this does not change the code's default model.
Select a model with `image_gen.model`. Recognized GPT models include `gpt-image-1`, `gpt-image-1.5`,
`gpt-image-2`, `gpt-image-2.5`, `gpt-image-2.5-flare`, and `gpt-image-2.5-sunburst`.
These names indicate what the plugin can forward, not guaranteed availability on your gateway or account.

A quality suffix overrides the `quality` setting: `gpt-image-2.5-flare-high` sends
`model: gpt-image-2.5-flare` and `quality: high`. Actual quality support depends on the gateway and upstream.
Other gateway model IDs are forwarded verbatim without GPT-specific size, quality, or streaming parameters.

## Requests and responses

Example text-to-image request for a GPT image model:

```json
{
  "model": "gpt-image-2.5-flare",
  "prompt": "A ragdoll kitten explaining how to make steamed buns",
  "n": 1,
  "size": "auto",
  "quality": "auto",
  "stream": true,
  "partial_images": 2
}
```

`quality: auto` follows the example configuration, rather than being forced by the plugin.
`stream`, `partial_images`, and the GPT request `size` are currently fixed request behaviors.

- Without references, call `/images/generations`; with references, call `/images/edits`.
- Ignore keepalive events and save up to 2 partial_image previews.
- Save the final image on the completed event. A stream without previews can still succeed.
- Return an error if the stream ends without a completed image; never treat a preview as the final result.
- Include preview paths in `partial_images`, along with `requested_size`, `requested_quality`, and actual `output_size`.

`partial_images: 2` does not request two finished images; the final count remains `n: 1`.
Images are saved through Hermes helpers under `$HERMES_HOME/cache/generated/images/`.

## Limitations and security boundaries

- **No automatic fallback or resend:** failed GPT streams are not retried as non-streaming requests.
  SDK retries are also disabled. Manually generating again can still incur additional charges.
- **Automatic dimensions:** GPT requests use `size: auto`. `aspect_ratio` remains result metadata,
  rather than determining a fixed request size. You can describe composition in the prompt, but exact ratios are not guaranteed.
- **Timeout:** the SDK timeout is 240 seconds per network operation, not a total generation deadline.
  Your gateway, proxy, or upstream may terminate a connection earlier.
- **Reference images:** local files, image data URLs, and public HTTPS URLs are supported, up to 16 references.
  HTTPS loading uses Hermes SSRF protection, disables redirects, checks image signatures, and enforces a 50 MB limit.
- **Gateway connections:** HTTPS is required by default. Plain HTTP is allowed only when all resolved
  addresses are in explicitly supported loopback, LAN, CGNAT/Tailscale, or IPv6 ULA networks; public and metadata addresses are refused.
- **Output URLs:** gateway-provided image URLs use the standard Hermes downloader, which has a different
  security boundary from the strict reference-image loader. Connect only to a gateway you trust.
- **Credential isolation:** the plugin does not read or write chat's `OPENAI_API_KEY` or `OPENAI_BASE_URL`,
  does not forward OpenAI organization or project IDs, and redacts credentials in error messages.

## Development and validation

Run in a Python environment with the Hermes runtime dependencies installed:

```bash
PYTHONPATH=/path/to/hermes-agent PYTHONDONTWRITEBYTECODE=1 \
  python -m unittest discover -s tests -v
hermes plugins doctor . --ci
hermes plugins validate .
```

The local modified version passed 46 automated tests, plugin validation, and Plugin Doctor.
An offline replay of a captured real SSE response also reproduced the final image exactly.
Tests cover generation, editing, previews, keepalives, early termination, error redaction,
resource cleanup, and the existing non-GPT JSON route.

These checks do not prove online compatibility with every gateway. `scripts/live_e2e.py` can perform
live checks, but invokes actual image endpoints and can incur charges; do not run it without authorization.

## Credits and license

Thanks to [duu261/hermes-imagegen-openai-compatible](https://github.com/duu261/hermes-imagegen-openai-compatible)
for the original implementation. This fork primarily adds GPT image streaming, preview saving, and related tests.

MIT licensed. The original copyright notice is retained in [LICENSE](LICENSE).
See [CHANGELOG.md](CHANGELOG.md) for changes.
