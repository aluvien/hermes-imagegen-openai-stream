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

If the original is already installed, follow the migration steps below instead of the fresh-install command.
New CLI processes can load the updated plugin; a running gateway can be restarted by its operator
with `hermes gateway restart`.

## Migrating from the original plugin

Both repositories declare `name: openai-compatible`, so migration replaces the same plugin directory.
`hermes plugins update openai-compatible` does **not** switch an upstream installation to this fork.
Use a forced reinstall from the fork. If the old installation is pinned, Hermes also requires an
explicit full 40-character commit SHA when changing the source.

The commands below use Bash-compatible syntax on macOS/Linux. Run them in the intended Hermes
profile context; use the same profile selection for every command, including the restart.

### 1. Back up before replacing the plugin

This derives the active profile directory from Hermes, rather than assuming `~/.hermes`.
The backup stays outside `plugins/` so it is not discovered as another plugin.

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

Keep the printed location. The backup may contain credentials: keep it private and never upload it.
Switching repositories starts from a clean plugin tree, so do not rely on a different-source
reinstall to preserve local plugin edits or untracked files. If the plugin directory is a symlink,
the installer refuses replacement; handle that development install separately instead of deleting it blindly.

### 2. Install the verified fork revision

```bash
hermes plugins install aluvien/hermes-imagegen-openai-stream --force --ref a452d4160e013338f7b81935a46d089ad1c0a1f5 --enable
```

This installs an explicit verified revision, not necessarily the newest commit on `main`.
It works for switching both pinned and unpinned upstream installations. Review Hermes' dependency
and security prompts. Do not use `--no-deps` to replace an active plugin.

The plugin ID stays `openai-compatible`; keep your existing gateway URL, key variable, and credential.
If you want the model used in this guide, set only these selections:

```bash
hermes config set image_gen.provider openai-compatible
hermes config set image_gen.model gpt-image-2.5-flare
```

Do **not** replace a working gateway with `https://gateway.example/v1`, or overwrite a custom
`key_env` during migration. The configuration section below is for setting up or deliberately changing a route.

### 3. Verify and reload

```bash
hermes plugins list
hermes plugins show openai-compatible
hermes config get image_gen.provider
hermes config get image_gen.model
```

Confirm the installed source is this fork, the pinned revision is `a452d416…`, and the plugin
version is `2.0.3`. Close and reopen CLI sessions. If using the messaging gateway, run:

```bash
hermes gateway restart
```

This restarts the affected gateway; schedule it when interrupting active work is acceptable.
Loading successfully does not prove your gateway supports SSE. An actual generation test may incur charges.

### Subsequent updates and rollback

The migration command pins the installation. To upgrade later, repeat the fork install command with
`--force --ref` and a new verified full SHA. A plain update will not move a pinned installation.
For an unpinned installation originally installed from this fork, use:

```bash
hermes plugins update openai-compatible
```

To return to the original `2.0.2` code, explicitly reinstall its known revision and reload Hermes:

```bash
hermes plugins install duu261/hermes-imagegen-openai-compatible --force --ref 5e20ebbcfe5c6a44fd8685f16e729221dcaad829 --enable
```

This restores the upstream source record as well as its code. Reapply any original local edits from
your backup separately; do not blindly restore a whole configuration or shared install-metadata file
after other plugins or settings have changed.

## Configuration

```bash
hermes config set image_gen.provider openai-compatible
hermes config set image_gen.model gpt-image-2.5-flare
hermes config set plugins.entries.openai-compatible.settings.base_url https://gateway.example/v1
hermes config set plugins.entries.openai-compatible.settings.quality auto
```

`https://gateway.example/v1` is a placeholder: replace it with your own API root, usually ending in `/v1`.

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

## Safety refusals and empty results

- Explicit upstream safety codes (including `moderation_blocked` and `content_policy_violation`)
  are returned to Hermes as `content_policy_violation`, including for HTTP 403 or a failed stream.
- The error asks Hermes to explain the refusal and offer a genuinely safe alternative in the user's
  language, preserving only benign subject/style/composition. A generic peaceful-landscape prompt is
  included as a fallback, not a personalized rewrite or a guarantee of approval. Either the input
  or generated output may have triggered moderation; the plugin does not assume which one.
- An empty or incomplete image result remains `empty_response` or `invalid_response`, not proof of
  a safety violation. Hermes is advised to review the prompt/reference images and suggest retrying
  with user approval; repeated failures warrant checking gateway logs and streaming support.
- Failed/error/incomplete stream events preserve upstream error messages with credentials redacted.
  No automatic rewrite, resubmission or retry is performed; retries may incur additional charges.
- **Recommended preflight:** Hermes should review the prompt and references before calling the tool
  and ask the user to approve a safe revision when needed. This is an agent workflow recommendation,
  not an automatic preflight detector installed by this plugin. The plugin has no keyword blacklist
  and makes no separate moderation API calls. Image-generation safety checks do not guarantee a
  preflight decision or a complete refusal response through a third-party gateway. A separate
  Moderation API integration requires verifying that the configured gateway supports it; do not
  assume that a Codex-backed image route exposes that endpoint.

## Development and validation

Run in a Python environment with the Hermes runtime dependencies installed:

```bash
PYTHONPATH=/path/to/hermes-agent PYTHONDONTWRITEBYTECODE=1 \
  python -m unittest discover -s tests -v
hermes plugins doctor . --ci
hermes plugins validate .
```

The local modified version passed 55 automated tests, plugin validation, and Plugin Doctor.
An offline replay of a captured real SSE response also reproduced the final image exactly.
Tests cover generation, editing, previews, keepalives, early termination, error redaction,
resource cleanup, and the existing non-GPT JSON route.

The README command syntax was checked against the installed Hermes CLI parsers. All five configuration
writes were exercised in a temporary profile, and the backup script was run on fixture files.
Pinned-source migration and rollback control flow were tested in that profile with Git cloning and
dependency publication simulated; no live installation, gateway restart, or paid generation was performed.
Command references: [Hermes CLI](https://hermes-agent.nousresearch.com/docs/reference/cli-commands/)
and [plugin installation](https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins/).

These checks do not prove online compatibility with every gateway. `scripts/live_e2e.py` can perform
live checks, but invokes actual image endpoints and can incur charges; do not run it without authorization.

## Credits and license

Thanks to [duu261/hermes-imagegen-openai-compatible](https://github.com/duu261/hermes-imagegen-openai-compatible)
for the original implementation. This fork primarily adds GPT image streaming, preview saving, and related tests.

MIT licensed. The original copyright notice is retained in [LICENSE](LICENSE).
See [CHANGELOG.md](CHANGELOG.md) for changes.
