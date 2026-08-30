#!/usr/bin/env python3
"""探针：验证 DeepSeek 是否把 `tools` 参数计入 prompt cache 的前缀。

这个结论决定要不要做「Phase1 统一传 FULL 工具集」的改造：
  - tools 进前缀  → 换工具集让整段前缀失效 → Phase1(LIGHT) 是缓存孤岛 → 改造有收益
  - tools 不进前缀 → 换工具集不影响 messages 前缀命中 → 孤岛不存在 → 不必改

方法（受控实验，唯一变量 = tools）：
  固定同一段足够长的 messages M（开头埋唯一 nonce 保证每次运行冷启动），
  连续发若干次，messages 始终字节一致，只改变 tools——

    R1  FULL   冷启动，建立 M+FULL 缓存          hit 应 ≈ 0
    R2  FULL   同工具复发                         hit 基线（应高）
    R3  LIGHT  换工具                             ★ 关键测量
    R4  LIGHT  再发                               确认 LIGHT 也能独立缓存（应高）
    R5  FULL   换回                               FULL 缓存是否仍在
    R6  none   完全不带 tools                      附加信号

  判定看 R3 相对 R2：
    R3 ≈ R2  → tools 不进前缀（换 tools 不掉命中）
    R3 ≪ R2  → tools 进前缀（换 tools 把前缀打回 MISS）

安全：--dry-run 不联网、不花钱，先确认配置解析对、前缀够长再真跑。
真正测量会联网并消耗少量 token（一遍约 2~3 万输入 token，几分钱量级），请自行运行。
配置来源：优先复用新 Plugin 模型目录解析出的候选（即 app 里配的模型/Key/BaseURL），
否则回退到环境变量 OPENAI_MODEL / OPENAI_BASE_URL / OPENAI_API_KEY。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

import httpx

# 让脚本在仓库任意位置都能 import 到 cyrene（包根在 src/）
_SRC = Path(__file__).resolve().parent.parent / "src"
if _SRC.is_dir():
    sys.path.insert(0, str(_SRC))

DEFAULT_BASE_URL = "https://api.deepseek.com/v1"


def resolve_endpoint() -> tuple[str, str, str]:
    """返回 (model, api_key, endpoint)。优先用项目配置，回退环境变量。"""
    import os

    try:
        from agent.plugin.model_catalog import configured_model_candidates
        from cyrene.model.protocol_adapters import protocol_endpoints

        cands = configured_model_candidates(route="primary")
        if cands:
            c = cands[0]
            endpoints = protocol_endpoints(
                str(c.get("adapter") or c.get("provider") or "openai"),
                str(c.get("base_url") or ""),
                str(c.get("model") or ""),
            )
            endpoint = endpoints[0] if endpoints else str(c["base_url"]).rstrip("/") + "/chat/completions"
            return c["model"], c["api_key"], endpoint
    except Exception as exc:  # pragma: no cover - 仅在 cyrene 不可导入时
        print(f"[warn] 复用项目配置失败，回退环境变量：{exc}", file=sys.stderr)

    model = (os.environ.get("OPENAI_MODEL") or "deepseek-chat").strip()
    base_url = (os.environ.get("OPENAI_BASE_URL") or DEFAULT_BASE_URL).strip().rstrip("/")
    api_key = (os.environ.get("OPENAI_API_KEY") or "").strip().strip('"').strip("'")
    return model, api_key, base_url + "/chat/completions"


def build_messages(prefix_chars: int) -> list[dict]:
    """一段足够长、字节固定的 messages，开头埋唯一 nonce 保证本次运行冷启动。

    缓存只认 token 前缀长度，所以用一个长 system（承载前缀）+ 一句固定 user 即可。
    """
    nonce = uuid.uuid4().hex
    block = (
        "你是一个严谨的工程助手。下面是一段用于把上下文前缀撑到足够长度的稳定说明文，"
        "内容本身不重要，重要的是它在多次调用之间逐字节一致，从而可以被前缀缓存命中。"
        "请忽略这段文字的语义，只把它当作占位上下文。"
    )
    filler = ""
    while len(filler) < prefix_chars:
        filler += block
    system = f"[probe-nonce {nonce}]\n" + filler[:prefix_chars]
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "请只回复一个字：好。"},
    ]


def build_tools(full_count: int) -> tuple[list[dict], list[dict]]:
    """LIGHT = 项目里的 3 个决策工具同形；FULL = LIGHT + 若干带较大 schema 的合成工具。

    工具的具体身份不影响实验结论（只需 LIGHT 与 FULL 的序列化明显不同），
    合成可让脚本自包含、确定，且不触发 registry/MCP 初始化。
    """
    light = [
        {"type": "function", "function": {"name": "use_tools", "description": "进入完整工具能力的唯一入口。任何需要动手做事（文件、搜索、网页、代码、命令、调度等）都调它。", "parameters": {"type": "object", "properties": {"task": {"type": "string"}}, "required": ["task"]}}},
        {"type": "function", "function": {"name": "ask_user", "description": "向用户提一个澄清问题；信息不足或有分叉选择时主动使用。", "parameters": {"type": "object", "properties": {"text": {"type": "string"}, "options": {"type": "array", "items": {"type": "string"}}}, "required": ["text"]}}},
        {"type": "function", "function": {"name": "quit", "description": "交互结束时调用。", "parameters": {"type": "object", "properties": {}}}},
    ]
    full = [dict(t) for t in light]
    for i in range(full_count):
        full.append({
            "type": "function",
            "function": {
                "name": f"synthetic_tool_{i:02d}",
                "description": (
                    f"合成工具 #{i}，用于把 FULL 工具集的序列化体积撑到接近真实规模。"
                    "它接收若干参数并返回结果，这里的描述刻意写长以模拟真实工具 schema 的体量。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "目标路径"},
                        "query": {"type": "string", "description": "查询或输入文本"},
                        "limit": {"type": "integer", "description": "返回数量上限"},
                        "flags": {"type": "array", "items": {"type": "string"}, "description": "可选开关"},
                    },
                    "required": ["query"],
                },
            },
        })
    return light, full


def call(endpoint: str, api_key: str, model: str, messages: list[dict], tools, max_tokens: int) -> dict:
    payload: dict = {"model": model, "messages": messages, "temperature": 0, "max_tokens": max_tokens, "stream": False}
    if tools is not None:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    r = httpx.post(endpoint, json=payload, headers=headers, timeout=120.0)
    r.raise_for_status()
    return r.json().get("usage", {}) or {}


def cache_stats(usage: dict) -> tuple[int, int, int, float]:
    """从 usage 里提取 (prompt, hit, miss, hit_rate)，兼容 DeepSeek 与 *_details 两种字段。"""
    prompt = int(usage.get("prompt_tokens") or 0)
    hit = usage.get("prompt_cache_hit_tokens")
    if hit is None:
        details = usage.get("prompt_tokens_details") or {}
        hit = details.get("cached_tokens")
    hit = int(hit or 0)
    miss = usage.get("prompt_cache_miss_tokens")
    miss = int(miss) if isinstance(miss, int) else max(0, prompt - hit)
    rate = (hit / prompt) if prompt else 0.0
    return prompt, hit, miss, rate


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prefix-chars", type=int, default=6000, help="固定前缀字符数（越长命中越明显，默认 6000）")
    ap.add_argument("--full-tools", type=int, default=25, help="FULL 工具集里的合成工具数量（默认 25）")
    ap.add_argument("--max-tokens", type=int, default=4, help="输出 token 上限（我们不关心输出，默认 4）")
    ap.add_argument("--sleep", type=float, default=1.5, help="每次调用之间的等待秒数（默认 1.5）")
    ap.add_argument("--repeats", type=int, default=1, help="整个 6 步序列重复几遍（默认 1）")
    ap.add_argument("--dry-run", action="store_true", help="只解析配置、构造请求并打印体量，不联网")
    args = ap.parse_args()

    model, api_key, endpoint = resolve_endpoint()
    messages = build_messages(args.prefix_chars)
    light, full = build_tools(args.full_tools)

    masked = (api_key[:5] + "…" + api_key[-4:]) if len(api_key) >= 9 else ("<空>" if not api_key else "***")
    prefix_text = messages[0]["content"] + messages[1]["content"]
    print(f"模型      : {model}")
    print(f"端点      : {endpoint}")
    print(f"API Key   : {masked}")
    print(f"前缀字符  : {len(prefix_text)}（粗估 ~{len(prefix_text)//2}~{len(prefix_text)} token）")
    print(f"LIGHT 工具: {len(light)} 个，序列化 {len(json.dumps(light, ensure_ascii=False))} 字符")
    print(f"FULL  工具: {len(full)} 个，序列化 {len(json.dumps(full, ensure_ascii=False))} 字符")

    if args.dry_run:
        print("\n[dry-run] 未联网。确认以上端点/Key/前缀长度无误后，去掉 --dry-run 即开始测量。")
        return 0
    if not api_key:
        print("\n[error] 没有解析到 API Key。在 app 里配置模型，或设 OPENAI_API_KEY 环境变量。", file=sys.stderr)
        return 2

    plan = [("R1", "FULL", full), ("R2", "FULL", full), ("R3", "LIGHT", light),
            ("R4", "LIGHT", light), ("R5", "FULL", full), ("R6", "none", None)]

    print("\n step  tools   prompt    hit    miss   hit_rate")
    print(" ----  -----   ------   ----   ----   --------")
    last: dict[str, float] = {}
    for rep in range(args.repeats):
        for tag, label, tools in plan:
            try:
                usage = call(endpoint, api_key, model, messages, tools, args.max_tokens)
            except httpx.HTTPStatusError as exc:
                print(f" {tag}    {label:<6} HTTP {exc.response.status_code}: {exc.response.text[:200]}")
                return 1
            except Exception as exc:
                print(f" {tag}    {label:<6} 调用失败: {exc}")
                return 1
            prompt, hit, miss, rate = cache_stats(usage)
            print(f" {tag}    {label:<6} {prompt:>6}  {hit:>5}  {miss:>5}    {rate*100:5.1f}%")
            last[tag] = rate
            time.sleep(args.sleep)

    print("\n=== 判定 ===")
    r2, r3 = last.get("R2", 0.0), last.get("R3", 0.0)
    if r2 < 0.2:
        print(f"基线 R2 命中率仅 {r2*100:.1f}%，实验无效：前缀可能太短或缓存未生效。")
        print("→ 加大 --prefix-chars（如 12000）、或确认端点确实是 DeepSeek 且支持上下文缓存后重试。")
    elif r3 >= r2 * 0.8:
        print(f"R3({r3*100:.1f}%) ≈ R2({r2*100:.1f}%)：换工具集不掉命中 → tools 不进前缀。")
        print("→ Phase1 缓存孤岛不存在，统一工具集的改造无收益；40% 的 miss 要去别处找。")
    else:
        print(f"R3({r3*100:.1f}%) ≪ R2({r2*100:.1f}%)：换工具集把命中打回去了 → tools 进前缀。")
        print("→ Phase1(LIGHT) 缓存孤岛属实，让 Phase1 与 Phase2 统一传 FULL 的改造有收益。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
