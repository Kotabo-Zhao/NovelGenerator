"""Async OpenAI-compatible LLM gateway with built-in zero-key Demo mode."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, AsyncGenerator

import httpx

from .models import LLMSettings

log = logging.getLogger(__name__)


class LLMConfigStore:
    """Persists LLM settings locally without ever exposing plaintext API keys in API reads."""

    def __init__(self, path: Path):
        self.path = path

    def load(self) -> LLMSettings:
        if not self.path.is_file():
            return LLMSettings()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return LLMSettings(
                mode=raw.get("mode", "demo"),
                base_url=raw.get("base_url", "https://api.deepseek.com/v1"),
                model=raw.get("model", "deepseek-chat"),
                api_key=raw.get("api_key", ""),
                temperature=float(raw.get("temperature", 0.8)),
                max_tokens=int(raw.get("max_tokens", 4096)),
            )
        except Exception as exc:
            log.warning("Failed to read llm_config.json, falling back to demo mode: %s", exc)
            return LLMSettings()

    def save(self, updates: dict[str, Any]) -> LLMSettings:
        current = self.load()
        new_key = updates.get("api_key")
        resolved_key = current.api_key if (new_key is None or new_key == "") else str(new_key).strip()
        if updates.get("clear_api_key"):
            resolved_key = ""
        settings = LLMSettings(
            mode="openai_compatible" if updates.get("mode") == "openai_compatible" else "demo",
            base_url=str(updates.get("base_url") or current.base_url).rstrip("/"),
            model=str(updates.get("model") or current.model).strip() or "deepseek-chat",
            api_key=resolved_key,
            temperature=max(0.0, min(1.5, float(updates.get("temperature", current.temperature)))),
            max_tokens=max(512, min(16384, int(updates.get("max_tokens", current.max_tokens)))),
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(settings.__dict__, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)
        return settings


class AsyncLLMGateway:
    """Non-blocking async HTTPX client supporting OpenAI-compatible SSE + offline Demo."""

    def __init__(self, config_store: LLMConfigStore):
        self.config_store = config_store

    async def complete_json(self, system_prompt: str, user_prompt: str, *, task: str = "generic") -> dict[str, Any]:
        settings = self.config_store.load()
        if settings.mode != "openai_compatible" or not settings.api_key:
            return self._demo_json(task, user_prompt)

        payload = {
            "model": settings.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": min(settings.temperature, 0.5),
            "max_tokens": min(settings.max_tokens, 3000),
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {settings.api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=15.0)) as client:
            resp = await client.post(f"{settings.base_url}/chat/completions", headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            raw_content = data["choices"][0]["message"]["content"]
            return _extract_json_object(raw_content)

    async def stream_text(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        task: str = "chapter",
        meta: dict[str, Any] | None = None,
    ) -> AsyncGenerator[str, None]:
        settings = self.config_store.load()
        if settings.mode != "openai_compatible" or not settings.api_key:
            async for chunk in self._demo_stream(task, user_prompt, meta or {}):
                yield chunk
            return

        payload = {
            "model": settings.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": settings.temperature,
            "max_tokens": settings.max_tokens,
            "stream": True,
        }
        headers = {
            "Authorization": f"Bearer {settings.api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0)) as client:
            async with client.stream("POST", f"{settings.base_url}/chat/completions", headers=headers, json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk_obj = json.loads(data_str)
                        delta = chunk_obj["choices"][0].get("delta", {})
                        content = delta.get("content")
                        if content:
                            yield content
                    except Exception:
                        continue

    async def test_connection(self) -> dict[str, Any]:
        settings = self.config_store.load()
        if settings.mode != "openai_compatible":
            return {"ok": True, "mode": "demo", "message": "当前处于内置演示模式，无需外部 API Key 即可完整体验四阶段写作流。"}
        if not settings.api_key:
            return {"ok": False, "mode": "openai_compatible", "message": "请先填写 API Key"}
        headers = {"Authorization": f"Bearer {settings.api_key}", "Content-Type": "application/json"}
        payload = {
            "model": settings.model,
            "messages": [{"role": "user", "content": "回复OK"}],
            "max_tokens": 8,
            "temperature": 0.0,
        }
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0)) as client:
                resp = await client.post(f"{settings.base_url}/chat/completions", headers=headers, json=payload)
                resp.raise_for_status()
                return {"ok": True, "mode": "openai_compatible", "message": f"连接成功：{settings.model}"}
        except Exception as exc:
            return {"ok": False, "mode": "openai_compatible", "message": f"连接失败：{exc}"}

    def _demo_json(self, task: str, prompt: str) -> dict[str, Any]:
        if task == "outline":
            return {
                "entities": [
                    {
                        "kind": "character",
                        "name": "沈砚",
                        "profile": "前档案修复师，擅长从旧纸墨迹中辨认伪造日期，左腕有旧烫伤。",
                        "current_state": "刚接手江城旧钟楼遗嘱清点，手中握有半张缺失火漆印的信纸。",
                        "location": "江城老码头·三号仓库",
                        "voice_notes": "句短克制，习惯先问细节再表态，不轻易许诺。",
                    },
                    {
                        "kind": "character",
                        "name": "周明霁",
                        "profile": "航运公司法务顾问，表面礼貌克制，暗中追查三年前沉船账册。",
                        "current_state": "在雨夜主动现身试探沈砚是否见过编号 07 的旧航运单。",
                        "location": "江城老码头·三号仓库",
                        "voice_notes": "语速平稳，善用反问转移焦点。",
                    },
                ],
                "foreshadows": [
                    {
                        "title": "缺失半枚火漆印的07号航运单",
                        "detail": "信纸背面隐约有低温显影的钢印编号，指向三年前的沉船保险理赔。",
                        "status": "planted",
                        "planted_chapter": 1,
                        "target_chapter": 4,
                    }
                ],
                "world_entries": [
                    {
                        "category": "rule",
                        "title": "江城旧档案封存条例",
                        "keywords": "档案,火漆,码头,账册",
                        "content": "凡涉及航运保险争议的原始提单，均须加盖双联火漆印并由公证处留存副页。",
                    }
                ],
                "chapters": [
                    {
                        "chapter_num": 1,
                        "title": "雨夜来客与半枚火漆",
                        "summary": "沈砚在旧码头仓库清点遗物，周明霁不请自来，两人围绕07号航运单展开试探，窗外突发异常响动。",
                        "goal": "保住刚发现的07号航运单原件，摸清周明霁的真实来意。",
                        "conflict": "周明霁以合法委托名义索要全部档案，沈砚怀疑其真正目的是销毁沉船证据。",
                        "characters": ["沈砚", "周明霁"],
                        "must_happen": ["沈砚在旧铁箱夹层发现半枚火漆信纸", "周明霁上门试探并亮出委托函", "仓库外铁门传来撬锁声"],
                        "hook_direction": "停在仓库断电瞬间，手电光扫过门口留下的半枚湿鞋印。",
                        "target_words": 1200,
                    },
                    {
                        "chapter_num": 2,
                        "title": "断电三分钟",
                        "summary": "仓库陷入黑暗，沈砚借地形转移原件，发现来人并非周明霁的手下，两人被迫短暂联手脱身。",
                        "goal": "在断电混乱中保住原件并确认偷袭者身份。",
                        "conflict": "敌暗我明，沈砚既要防闯入者又要防身边的周明霁。",
                        "characters": ["沈砚", "周明霁"],
                        "must_happen": ["仓库断电三分钟", "沈砚用旧账本调包", "在门缝捡到一枚铜制船票夹"],
                        "hook_direction": "铜票夹背面刻着沈砚失踪兄长的缩写。",
                        "target_words": 1200,
                    },
                    {
                        "chapter_num": 3,
                        "title": "公证处副页",
                        "summary": "天亮后沈砚前往老城区公证档案室核对副页，却发现三年前的记录在两周前刚被借阅过。",
                        "goal": "查出是谁提前动了公证处副页。",
                        "conflict": "借阅登记簿上的签名与一位早已去世的船长完全一致。",
                        "characters": ["沈砚", "周明霁"],
                        "must_happen": ["查阅公证处借阅登记", "发现已故船长签名", "周明霁提出交换情报"],
                        "hook_direction": "公证员压低声音提醒沈砚，借阅人离开时打听过他的住址。",
                        "target_words": 1200,
                    },
                ],
            }
        return {
            "bridge_summary": "沈砚在旧码头三号仓库发现07号航运单残页，与上门索要档案的周明霁正面交锋；随后仓库突发断电，门外出现第三拨闯入者的痕迹。",
            "ending_anchor": "手电光束猛地一晃，水泥地上赫然多出半枚带着机油味的湿鞋印，鞋尖正对着铁箱。",
            "entity_updates": [
                {
                    "kind": "character",
                    "name": "沈砚",
                    "current_state": "已将07号残页贴身藏入内袋，高度戒备门外闯入者。",
                    "location": "江城老码头·三号仓库",
                },
                {
                    "kind": "character",
                    "name": "周明霁",
                    "current_state": "未能拿走档案，面对突发断电同样露出警觉，暂留仓库内。",
                    "location": "江城老码头·三号仓库",
                },
            ],
            "foreshadow_updates": [
                {
                    "title": "带机油味的湿鞋印",
                    "detail": "断电瞬间出现在三号仓库门口的第三拨闯入者留下的鞋印，带有轮机舱机油气味。",
                    "status": "planted",
                    "target_chapter": 3,
                }
            ],
            "canon_events": [
                "沈砚在旧铁箱夹层找到带半枚火漆印的07号航运单残页",
                "周明霁持委托函上门索要档案未果，三号仓库突发断电并出现未知闯入者鞋印",
            ],
        }

    async def _demo_stream(self, task: str, prompt: str, meta: dict[str, Any]) -> AsyncGenerator[str, None]:
        if task == "surgical":
            demo_text = "雨水顺着铁皮屋檐砸在窗台上，敲出凌乱的脆响。沈砚没抬头，指腹在泛黄的纸角停了半秒，火漆边缘那道细小的裂口在台灯光下像一道旧疤。「委托函我看了，」他把铁箱扣回原位，金属搭扣发出清脆的一声，「但清单里没写夹层里的东西。」"
        else:
            ch_title = meta.get("title", "雨夜来客")
            demo_text = (
                f"雨点砸在三号仓库的铁皮顶上，闷响连成一片。空气里浮着潮湿的木箱霉味和淡淡的铁锈气，吊灯在穿堂风里微微摇晃，把桌面上的旧档案照得明暗不定。\n\n"
                f"沈砚把台灯往铁箱边缘挪了半寸，用竹签挑开箱底翘起的牛皮衬纸。夹层里静静躺着一张折叠过的薄纸，右下角残着半枚暗红火漆，纸背透出极浅的「07」钢印。他的指尖蹭过纸面，微凉，边缘还带着海盐受潮后的粗糙颗粒。\n\n"
                f"「沈先生，打扰了。」\n\n"
                f"仓库侧门不知何时推开了一条缝。周明霁收起黑伞，水珠顺着伞尖滴在水泥地上，洇出几个深色圆点。他手里捏着一份牛皮纸公文袋，步子迈得不紧不慢，目光却先落在了桌上的铁箱。\n\n"
                f"「航运公司已经办妥托管手续。」周明霁把委托函推到台灯下，纸角压住一粒灰尘，「按照约定，三号箱里的全部旧单据今晚由我带回封存。」\n\n"
                f"沈砚没有碰那张委托函，顺手将竹签搁在砚台边，把那张薄纸不动声色地压进了牛皮封套下。「清单上列的是四十二份提单，」他抬眼看对方，声音不高，「周顾问来得这么急，是怕少了一页，还是怕多了一页？」\n\n"
                f"周明霁停顿了两秒，指节在公文袋边缘轻轻敲了一下。「沈先生是做修复的，应该清楚有些旧账翻出来，对谁都没好处。」\n\n"
                f"话音未落，仓库外侧那扇生锈的铁门忽然发出一声极轻的金属刮擦声——不是风吹的，是铁丝探进锁芯时绷紧的脆响。\n\n"
                f"头顶的吊灯啪地熄灭，整个仓库瞬间跌进漆黑。沈砚反手摸向桌沿的手电筒，拇指推开开关的刹那，惨白的光束扫过半开的侧门：水泥地上赫然多出半枚带着机油味的湿鞋印，鞋尖正对着铁箱。"
            )
        step = 18
        for i in range(0, len(demo_text), step):
            yield demo_text[i:i + step]
            await asyncio.sleep(0.012)


def _extract_json_object(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines and lines[-1].strip().startswith("```") else lines[1:]).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        text = text[start:end + 1]
    return json.loads(text)
