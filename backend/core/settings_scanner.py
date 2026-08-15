"""NovelGenerator — 跨章设定一致性扫描（创作者工具 M5）

策略：实体属性矛盾检测，batch 化 LLM 判断。
1. 读取全部已写章节文本
2. 用 bible.json 的实体名单（角色/地点/名词）+ 高频词提取候选实体
3. 对每对相邻/隔章抽取同名实体上下文，LLM 判断属性矛盾
4. 返回冲突报告（实体 + 章节对 + 原句 + 类型）
"""
import json
import logging
import os
import re

log = logging.getLogger(__name__)


class SettingsScanner:
    def __init__(self, client=None, model=None):
        self.client = client
        self.model = model

    def _load_chapters(self, novel_id: str, novel_dir: str) -> dict:
        chapters = {}
        cd = os.path.join(novel_dir, "chapters")
        if not os.path.isdir(cd):
            return chapters
        for fname in sorted(os.listdir(cd)):
            if not fname.endswith(".md"):
                continue
            try:
                ch_num = int("".join(c for c in fname if c.isdigit()) or "0")
                with open(os.path.join(cd, fname), "r", encoding="utf-8") as f:
                    chapters[ch_num] = f.read()
            except Exception:
                continue
        return chapters

    def _load_entities(self, novel_id: str, novel_dir: str) -> list:
        """从 bible.json + plan.json 收集实体名单。"""
        entities = set()
        bp = os.path.join(novel_dir, "bible.json")
        if os.path.exists(bp):
            try:
                with open(bp, "r", encoding="utf-8") as f:
                    bible = json.load(f)
                entities.update(bible.get("characters", {}).keys())
                entities.update(bible.get("locations", {}).keys())
                entities.update(bible.get("terms", {}).keys())
            except Exception:
                pass
        # 高频 2-4 字词兜底（仅当 bible 为空时）
        if not entities:
            text = "\n".join(self._load_chapters(novel_id, novel_dir).values())
            stop = {"我们", "他们", "自己", "这个", "那个", "一个", "没有", "什么", "就是", "知道",
                    "已经", "突然", "然后", "因为", "所以", "如果", "可以", "起来", "时候",
                    "觉得", "还是", "但是", "不过", "非常", "一定", "终于", "现在", "这里", "那里"}
            from collections import Counter
            for w, cnt in Counter(re.findall(r"[\u4e00-\u9fff]{2,4}", text)).most_common(40):
                if w not in stop and cnt >= 3:
                    entities.add(w)
        return sorted(entities)

    def _extract_mentions(self, chapters: dict, entity: str) -> list:
        """返回 {chapter, sentence} 列表。"""
        mentions = []
        for ch_num, text in sorted(chapters.items()):
            for line in text.splitlines():
                if entity in line:
                    mentions.append({"chapter": ch_num, "sentence": line.strip()[:150]})
        return mentions

    def _judge_batch(self, entity: str, mentions: list) -> list:
        """对同一实体的多章描述做矛盾判断（LLM，非批量场景可退化为规则）。"""
        if len(mentions) < 2:
            return []
        # 无 LLM 客户端时用规则：同实体同句出现互斥年龄/状态词
        if not self.client or not self.model:
            return self._rule_judge(mentions)
        try:
            prompt = (
                f"以下是同一角色/设定「{entity}」在不同章节中的描述，请判断是否存在**属性矛盾**"
                f"（如年龄前后不一致、关系矛盾、地点状态冲突、能力设定冲突）。\n\n"
                + "\n".join(f"[第{m['chapter']}章] {m['sentence']}" for m in mentions[:12])
                + "\n\n只输出 JSON 数组，每个元素："
                '{"chapter_a": int, "chapter_b": int, "claim_a": "A章描述", "claim_b": "B章描述", "type": "年龄/关系/地点/能力/时间线/其他"}'
                "无矛盾时输出 []。只输出 JSON。"
            )
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "你是小说设定一致性审核员，只输出合法 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_tokens=1500,
            )
            raw = (resp.choices[0].message.content or "").strip()
            if raw.startswith("```"):
                raw = raw.strip("`")
                if raw.startswith("json"):
                    raw = raw[4:]
            conflicts = json.loads(raw)
            if not isinstance(conflicts, list):
                return []
            out = []
            for c in conflicts[:10]:
                if isinstance(c, dict) and c.get("chapter_a") and c.get("chapter_b"):
                    out.append({
                        "entity": entity,
                        "chapter_a": int(c["chapter_a"]),
                        "chapter_b": int(c["chapter_b"]),
                        "claim_a": str(c.get("claim_a", ""))[:120],
                        "claim_b": str(c.get("claim_b", ""))[:120],
                        "type": str(c.get("type", "其他")),
                    })
            return out
        except Exception as e:
            log.warning(f"LLM consistency judge failed for {entity}: {e}")
            return []

    def _rule_judge(self, mentions: list) -> list:
        """规则兜底：互斥数值/状态词检测。"""
        conflicts = []
        age_pattern = re.compile(r"(\d{1,2})\s*[岁岁]")
        ages = {}
        for m in mentions:
            m_age = age_pattern.search(m["sentence"])
            if m_age:
                v = int(m_age.group(1))
                if m["chapter"] in ages and ages[m["chapter"]] != v:
                    conflicts.append({
                        "entity": "未知实体", "chapter_a": m["chapter"], "chapter_b": m["chapter"],
                        "claim_a": m["sentence"][:80], "claim_b": m["sentence"][:80], "type": "数值冲突",
                    })
                ages[m["chapter"]] = v
        return conflicts

    def scan(self, novel_id: str, plan: dict) -> dict:
        novel_dir = os.path.join(
            os.environ.get("NOVELS_DIR", ""), novel_id
        ) if os.environ.get("NOVELS_DIR") else os.path.join("novels", novel_id)
        # 优先从 engine.memory 获取目录（兼容测试注入）
        try:
            from api.deps import engine
            novel_dir = engine.memory.get_novel_dir(novel_id)
        except Exception:
            pass
        chapters = self._load_chapters(novel_id, novel_dir)
        entities = self._load_entities(novel_id, novel_dir)
        all_conflicts = []
        for entity in entities[:15]:  # 每本最多扫 15 个实体，控制耗时
            mentions = self._extract_mentions(chapters, entity)
            conflicts = self._judge_batch(entity, mentions)
            all_conflicts.extend(conflicts)
        all_conflicts.sort(key=lambda c: (c.get("chapter_a", 0), c.get("chapter_b", 0)))
        return {
            "status": "ok",
            "scanned_chapters": len(chapters),
            "scanned_entities": len(entities),
            "conflict_count": len(all_conflicts),
            "conflicts": all_conflicts[:50],
        }
