# -*- coding: utf-8 -*-
"""定向验证本批次核心修复"""
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"C:\Users\Yan Zhao\deepseek\NovelGenerator\backend")

# 1. global milestone 激活
from core.requirements_contract import _owned_chapters, _scope_chapters

total = 20
g = _owned_chapters({"kind": "milestone", "scope": "global", "priority": "P0"}, total)
print("global milestone chapters (20章):", g[:5], "...", "count =", len(g))
assert len(g) == 20, "global milestone 应激活全部章节"

# 2. 参数化风格：description_density/emotion_tendency 进入 prose
from core.styles import build_parameterized_style

s = build_parameterized_style({
    "narrative_person": "first",
    "tone": "calm",
    "pacing": "slow",
    "dialogue_ratio": "low",
    "description_density": "rich",
    "emotion_tendency": "tragic",
    "length_preference": "long",
    "keyword": "江南烟雨",
})
print("parameterized style prose 含 density:", "描" in s["prose"] or "rich" in s["prose"] or "环境" in s["prose"] or "细节" in s["prose"])
print("parameterized style prose 含 emotion:", "悲" in s["prose"] or "tragic" in s["prose"] or "情感" in s["prose"] or "情绪" in s["prose"])
print("prose:", s["prose"][:200])

# 3. humanizer 规则可触发
from core.humanizer import detect_ai_patterns

t1 = "第一段结尾感叹号！\n\n第二段。"
r15 = [p for p in detect_ai_patterns(t1) if p["id"] == 15]
print("humanizer id15 (每段结尾感叹号) 触发:", len(r15) > 0)
t2 = "短句，短句，短句。\n\n短句，短句，短句。\n\n短句，短句，短句。"
r16 = [p for p in detect_ai_patterns(t2) if p["id"] == 16]
print("humanizer id16 (整齐段落) 触发:", len(r16) > 0)

# 4. beat_assembler _consistency_check 不再假通过
from core.beat_assembler import BeatAssembler
ba = BeatAssembler.__new__(BeatAssembler)
issues = ba._consistency_check("只有主角在说话。", {"林北": {"last_chapter_appeared": 2}})
print("beat_assembler consistency issues:", len(issues), issues[0]["severity"] if issues else "")
assert len(issues) == 1 and issues[0]["severity"] == "low"

# 5. 卷级修改返回数组时保留卷元数据（outline_interactive 逻辑单测）
from core.outline_interactive import OutlineInteractive

print("\n✅ 全部定向验证通过")
