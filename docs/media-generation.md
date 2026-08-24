# Media generation

Cyrene runs image, video, and music generation in a durable subsystem outside the Agent turn. The main Agent submits up to eight independent jobs through `media.generate`; `MediaDaemon` workers execute them in parallel, project every attachment or failure into the owning chat, and only then allow `MediaWakeBridge` to resume the Agent.

The ordering guarantee is:

```text
provider completes → register managed attachment → project chat message
→ mark job reported → make batch wake ready → wake Agent
```

`wake_agent=true` / `attach_then_wake_agent` is the only completion behavior. The Agent must end its turn after a successful submission and must not poll the database, wait, or create a terminal watcher.

## Runtime components

- `MediaJobManager` owns the SQLite-backed `media_batches`, `media_jobs`, and `media_wakes` lifecycle, including leases, retries, idempotency, and recovery.
- `MediaWorker` resolves a provider, executes it, registers output as Cyrene attachments, and projects a stable-ID chat message.
- `MediaDaemon` owns the independently sized worker pool.
- `MediaWakeBridge` waits until the chat is not busy and dispatches one trusted `system_media_wake` after every job in the batch is terminal and reported.
- `media_tools` is a main-Agent-only package exposing `media.generate`.

Expired leases are recoverable after a process restart, and stable media message IDs make delivery reconciliation idempotent. Cancelling a job immediately projects a cancellation result. Whether already-submitted remote compute can be stopped depends on the provider's cancellation API.

## Settings and providers

Use **Settings → Media generation** to configure concurrency, retry and polling policy, download limits, default routing, provider models, endpoints, and write-only API keys. Portable backups strip media credentials. Provider URLs require HTTPS, except loopback HTTP, and may not contain credentials.

Supported adapters:

| Provider | Media | Default / protocol |
| --- | --- | --- |
| OpenAI GPT Image | Image generation and edits | `gpt-image-2`; `/images/generations` and `/images/edits` |
| Seedream | Images | `doubao-seedream-5-0-260128`; Ark `/images/generations` |
| Seedance | Videos | `doubao-seedance-2-0-pro-260128`; async `/contents/generations/tasks` |
| MiniMax | Video and music | `MiniMax-H3`, `music-3.0`; H3 v2 create/query flow and `/v1/music_generation` |
| Google | Images and videos | `gemini-3.1-flash-image`, `gemini-omni-flash-preview`; official `google-genai>=2.10.0` SDK. An explicit Veo model remains supported. |
| ComfyUI MCP | Workflow-defined image, video, or music | Configured MCP server; local defaults to `run_workflow → job → fetch_outputs` |

Cyrene does not bundle Comfy MCP. It connects to an MCP server already configured by the operator. Workflows and custom nodes are executable code and must be trusted. The official Comfy MCP project is dual-licensed under AGPL-3.0-or-later or a commercial license; this integration remains an external MCP client boundary.

Model availability, billing, regions, quotas, and content policies remain provider-account concerns. Model identifiers are configurable so provider releases do not require changes to the durable media lifecycle.

MiniMax stopped offering paid music-generation APIs to new users on 2026-08-20. Accounts that already had paid API access may continue to use them. `music-3.0` is therefore a forward-compatible Cyrene configuration assumption using the existing `/v1/music_generation` contract, not a claim that every MiniMax account currently exposes that model. If an account only exposes an older documented model such as `music-2.6`, set that exact identifier in Settings.

## Reference inputs

References can come from chat attachment IDs, workspace paths, or public HTTPS URLs, subject to the selected provider's rules. Cyrene validates the media kind and provider compatibility before submitting paid work. It also projects the source chat attachments as a compact **References** section in the generated message; generated images, videos, and audio are rendered as playable/viewable chat attachments.

| Provider / route | Accepted references | Cyrene behavior and current limits |
| --- | --- | --- |
| OpenAI GPT Image | Images; optional image mask | One or more reference images switch the request to `/images/edits`. The mask is only valid for an edit request. |
| Seedream | One or more images | Images are sent through the Seedream `image` input for reference-based generation. |
| Google Gemini image | One or more local images | Images are supplied to the Gemini image model together with the prompt. |
| Google Gemini Omni video | Local images, or one local short video | The two modes cannot be mixed. Uploaded audio is currently unsupported. A short-video edit is limited to one local file and remains subject to Google model, region, and preview limitations. |
| Google Veo video | First-frame and optional last-frame images | Explicit `first_frame` / `last_frame` roles are preserved. Compatible Veo models may also accept up to three asset-reference images; those cannot be mixed with first/last-frame mode. |
| Seedance | Local/data-URL or public HTTPS images; public HTTPS videos; audio where the selected model supports it | Image references can be assigned first/last-frame roles. Video references must be public HTTPS URLs; unsupported local-video/data-URL inputs are rejected before submission. |
| MiniMax H3 video | Up to 9 images, 3 videos, and 3 audio clips; 12 files total | General reference mode cannot be mixed with first/last-frame mode. Local inputs are encoded for the API and public HTTPS inputs are passed as URLs. |
| ComfyUI MCP | Workflow-defined images, videos, audio, and optional mask | Each input must be bound by a Cyrene placeholder in the configured workflow JSON. Local files can only be uploaded through a local MCP profile; cloud mode accepts public HTTPS references only. |

Provider capabilities can differ by model revision. Choosing an explicit provider never silently drops an incompatible reference; the job fails validation instead.

## ComfyUI workflow placeholder contract

Each ComfyUI media setting points to an existing JSON workflow file (1 byte–10 MiB). Cyrene renders a temporary copy and never modifies the configured workflow. The following placeholders are supported anywhere inside JSON string values:

- Core fields: `{{prompt}}`, `{{negative_prompt}}`, `{{lyrics}}`, `{{seed}}`, `{{duration}}`, `{{number_of_outputs}}`, `{{aspect_ratio}}`, `{{resolution}}`, `{{size}}`, `{{quality}}`, and `{{output_format}}`.
- Extra request parameters: `{{parameter.foo}}` binds the `foo` entry from `parameters`. Operator controls such as `confirm_spend` and generation timeouts are not injectable parameters.
- Ordered references: `{{reference_1}}`, `{{reference_2}}`, and so on, in the same order as the normalized request references.
- Mask: `{{mask}}` binds the normalized local mask input.

When a JSON string value consists of exactly one token, Cyrene preserves its JSON type. For example, `"seed": "{{seed}}"` becomes a number when the request seed is numeric, and a standalone `{{parameter.foo}}` can become a boolean, array, or object. A token embedded in surrounding text, such as `"seed={{seed}}"`, is rendered as a string (arrays and objects use compact JSON text).

```json
{
  "6": {
    "inputs": {
      "text": "{{prompt}}",
      "negative": "{{negative_prompt}}",
      "seed": "{{seed}}",
      "reference": "{{reference_1}}",
      "custom_options": "{{parameter.foo}}"
    }
  }
}
```

Every input actually supplied by the request must be consumed by the workflow. Unknown placeholders, or supplied values/references with no matching placeholder, fail the job instead of silently ignoring creative inputs.

In `local` mode, local reference files and masks are copied to a temporary staging directory and uploaded with the configured `upload_file` tool; the placeholder receives the uploaded filename. Public HTTPS references pass through unchanged. In `cloud` mode, Cyrene currently has no Comfy Cloud asset-upload implementation, so only public HTTPS references are accepted and masks/local files are rejected. `confirm_spend` is an operator-owned setting only: a media request cannot opt itself into paid partner/API nodes.

## References (checked 2026-08-24)

- [OpenAI GPT Image 2](https://developers.openai.com/api/docs/models/gpt-image-2)
- [OpenAI image generation guide](https://developers.openai.com/api/docs/guides/image-generation)
- [BytePlus Seedream image generation](https://docs.byteplus.com/api/docs/ModelArk/1824121)
- [BytePlus Seedance video generation](https://docs.byteplus.com/en/docs/byteplus_las/video_gen_enhanced)
- [Google image generation](https://ai.google.dev/gemini-api/docs/image-generation)
- [Google Gemini Omni video generation](https://ai.google.dev/gemini-api/docs/omni)
- [Google video generation](https://ai.google.dev/gemini-api/docs/video)
- [MiniMax video generation](https://platform.minimax.io/docs/guides/video-generation)
- [MiniMax music generation](https://platform.minimax.io/docs/guides/music-generation)
- [MiniMax music API](https://platform.minimax.io/docs/api-reference/music-generation)
- [Comfy Org Comfy MCP](https://github.com/Comfy-Org/comfy-mcp)
- [Comfy Cloud MCP](https://docs.comfy.org/agent-tools/mcp)
