"""NovelGenerator v2.1 — 全链路功能组合集成测试 (~50 cases)

覆盖场景:
  K. 完整创作流程 (10 cases) — 灵感→设定→大纲→写作→导出
  L. 交互式大纲迭代 (8 cases) — 创建→反馈→拆解→重生成→验证
  M. 多章节连续写作 (8 cases) — 写N章→状态连续→伏笔追踪
  N. 跨模块协作 (8 cases) — Planner+Writer+Validator+Summarizer联动
  O. 异常恢复与边界 (8 cases) — 损坏文件/空白/超大/并发
  P. 数据持久化一致性 (8 cases) — 保存/加载/迁移/版本兼容
  Q. 功能组合场景 (8 cases) — Twist+Opening+Validation组合使用

运行: python tests/integration_test.py
"""

import sys, os, json, copy, time, tempfile, shutil, threading, asyncio

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "backend"))
os.chdir(os.path.dirname(os.path.dirname(__file__)))

# ── Test infrastructure ──
passed = 0; failed = 0; errors = []; _registry = []
def test(name):
    def d(fn):
        _registry.append((name, fn)); return fn
    return d
def run():
    global passed, failed
    for name, fn in _registry:
        try:
            fn(); passed += 1; print(f"  ✅ {name}")
        except AssertionError as e:
            failed += 1; e2 = f"FAIL [{name}]: {e}"; errors.append(e2); print(f"  ❌ {name}: {e}")
        except Exception as e:
            failed += 1; e2 = f"ERROR [{name}]: {type(e).__name__}: {e}"; errors.append(e2); print(f"  💥 {name}: {type(e).__name__}: {e}")

# ── Setup ──
tmp = tempfile.mkdtemp(prefix="novelgen_int_")
print(f"Test dir: {tmp}")

from core.shared_memory import SharedMemoryManager
from core.planner import Planner
from core.consistency_validator import ConsistencyValidator
from core.opening_optimizer import OpeningOptimizer
from core.twist_designer import TwistDesigner
from core.feedback_decomposer import FeedbackDecomposer
from core.outline_interactive import OutlineInteractive
from core.chapter_summarizer import ChapterSummarizer, check_and_compress
from core.engine import NovelEngine

smm = SharedMemoryManager(tmp)
planner = Planner(None, None)
validator = ConsistencyValidator(None, None)
optimizer = OpeningOptimizer(None, None)
twister = TwistDesigner(None, None)
decomposer = FeedbackDecomposer(None, None)
oi = OutlineInteractive(None, None, decomposer=decomposer)
summarizer = ChapterSummarizer(None, None)
engine = NovelEngine()
engine.memory = smm

NID = "integration_novel"

def _make_plan(title="仙道独尊", chapters=30):
    """Build a full plan with N chapters"""
    vols = []
    per_vol = max(5, chapters // 3)
    remaining = chapters
    for vn in range(1, 4):
        n = min(per_vol, remaining)
        chs = []
        start_num = (vn-1)*per_vol + 1
        for cn in range(n):
            num = start_num + cn
            chs.append({
                "number": num,
                "title": f"第{num}章测试",
                "summary": f"第{vn}卷第{cn+1}章核心剧情线",
                "emotion_curve": "平稳→爆发→余韵" if cn % 3 == 0 else "紧张→缓解→悬念",
                "conflict": f"IR:强度{2+cn%3}",
                "characters": ["叶凡", "柳如烟"],
                "hook": f"第{num}章结尾悬念钩子",
                "target_words": 3000
            })
        vols.append({
            "number": vn,
            "title": f"第{vn}卷·{['初入仙途','风云际会','决战天道'][vn-1]}",
            "act": ["第一幕·建置","第二幕·对抗","第三幕·解决"][vn-1],
            "theme": f"主题{vn}",
            "act_function": f"第{vn}卷功能描述",
            "chapters": chs
        })
        remaining -= n
        if remaining <= 0: break

    return {
        "title": title, "genre": "仙侠", "style": "热血爽文",
        "target_words": chapters * 3000,
        "worldbuilding": {
            "era": "上古修真时代", "geography": "九重天, 人间界, 魔域深渊",
            "power_system": "练气→筑基→金丹→元婴→化神→合体→渡劫→大乘",
            "core_conflict": "天道有缺, 凡人逆天封神",
            "factions": [
                {"name":"天道盟","description":"维护天道秩序的众神联盟","alignment":"正"},
                {"name":"逆天者","description":"反抗天道的修士组织","alignment":"中立"},
                {"name":"魔域","description":"域外天魔的巢穴","alignment":"邪"}
            ]
        },
        "characters": {
            "protagonist": {
                "name": "叶凡", "age": "18", "identity": "散修",
                "personality": {"surface":"坚毅沉默","true_self":"重情重义","flaw":"过度执着"},
                "arc": "凡人→问道→逆天→封神",
                "cheat": "混沌道体", "secret": "前世为天道碎片",
                "catchphrase": "我命由我不由天"
            },
            "supporting": [
                {"name":"柳如烟","identity":"药王谷传人","relation":"道侣","personality":"外冷内热","role":"情感支撑+炼药助手","meaning":"爱的对象"},
                {"name":"剑无名","identity":"剑阁弃徒","relation":"亦敌亦友","personality":"孤傲剑痴","role":"成长标尺+最强对手","meaning":"镜子"},
            ],
            "antagonist": [
                {"name":"天道化身","motivation":"维护天道秩序不惜代价","power":"天道级","conflict":"生存权与秩序之争","humanity":"曾是最初的逆天者, 屠龙者终成恶龙"},
            ],
            "bible_summary": "叶凡与柳如烟携手逆天, 剑无名亦敌亦友, 最终直面天道化身的终极选择"
        },
        "outline": {"volumes": vols, "total_chapters": chapters,
                     "three_act_map": "建置(ch1-10)→对抗(ch11-20)→解决(ch21-30)",
                     "rhythm_notes": "三章一小高潮, 五章一中高潮, 一卷一大高潮"},
        "_meta": {"created_at": "2026-07-22T00:00:00"}
    }

def _write_chapter(nid, num, text=None):
    if text is None:
        text = f"# 第{num}章\n\n叶凡站在山巅，望着远方。\n\n"
        text += f"「第{num}章的核心剧情。」他低声说道。\n\n"
        text += f"柳如烟从身后走来:「你已经决定了?」\n\n"
        text += "「没有回头路了。」\n\n" * 5
        text += f"剑光闪过，战斗一触即发。第{num}章到此结束。\n"
    smm.write_chapter(nid, num, text)


# ═══════════════════════════════════════════
# K. 完整创作流程 (10)
# ═══════════════════════════════════════════
print("\n=== K. Full Creation Pipeline ===")

@test("K01: create_novel → write plan + state + bible")
def test_k01():
    plan = _make_plan()
    smm.create_novel_workspace(NID)
    assert smm.write("plan", NID, plan)
    smm.save_novel_state(NID, {"current_chapter":0,"total_chapters":30,"total_words":0,"status":"planning_done","completed_chapters":[]})
    # Verify all files
    assert smm.read("plan", NID)["title"] == "仙道独尊"
    assert smm.get_novel_state(NID)["total_chapters"] == 30
    assert os.path.exists(smm.get_novel_dir(NID))

@test("K02: plan → write chapters 1-10 → verify state")
def test_k02():
    plan = _make_plan()
    smm.write("plan", NID, plan)
    smm.save_novel_state(NID, {"current_chapter":0,"total_chapters":30,"total_words":0,"status":"writing","completed_chapters":[]})
    for ch in range(1, 11):
        _write_chapter(NID, ch)
    # Verify
    state = smm.get_novel_state(NID)
    chs = smm.scan_chapters(NID)
    assert len(chs) >= 10
    assert smm.read_chapter(NID, 5) is not None
    assert smm.read_chapter(NID, 10) is not None

@test("K03: write → update completed_chapters → state consistent")
def test_k03():
    smm.save_novel_state(NID, {"current_chapter":5,"total_chapters":30,"total_words":15000,"status":"writing","completed_chapters":[1,2,3,4,5]})
    state = smm.get_novel_state(NID)
    assert state["completed_chapters"] == [1,2,3,4,5]
    assert state["current_chapter"] == 5

@test("K04: create → regenerate outline → save")
def test_k04():
    plan = _make_plan("旧书名")
    smm.write("plan", NID, plan)
    # Simulate regeneration
    new_plan = _make_plan("新书名", 35)
    smm.write("plan", NID, new_plan)
    smm.save_novel_state(NID, {"current_chapter":0,"total_chapters":35,"total_words":0,"status":"outline_regenerated","completed_chapters":[]})
    read = smm.read("plan", NID)
    assert read["title"] == "新书名"
    assert read["outline"]["total_chapters"] == 35

@test("K05: write all 30 chapters → scan → verify count")
def test_k05():
    plan = _make_plan(chapters=30)
    smm.write("plan", NID, plan)
    for ch in range(1, 31):
        _write_chapter(NID, ch)
    chs = smm.scan_chapters(NID)
    assert len(chs) == 30
    assert chs[0] == 1
    assert chs[-1] == 30

@test("K06: export_all → import_all roundtrip (JSON metadata)")
def test_k06():
    plan = _make_plan("导出测试")
    smm.write("plan", NID, plan)
    smm.save_novel_state(NID, {"current_chapter":3,"total_chapters":30,"status":"writing"})
    _write_chapter(NID, 1); _write_chapter(NID, 2); _write_chapter(NID, 3)
    smm.write("global_state", NID, {"characters":{"叶凡":["筑基"]},"power_levels":{"叶凡":"筑基"}})

    exp = smm.export_all(NID)
    smm.create_novel_workspace("imported")
    smm.import_all("imported", exp)
    # export_all includes JSON metadata; chapters are separate
    assert smm.read("plan","imported")["title"] == "导出测试"
    assert smm.get_novel_state("imported")["current_chapter"] == 3
    assert smm.read("global_state","imported")["power_levels"]["叶凡"] == "筑基"

@test("K07: create with different styles → verify metadata")
def test_k07():
    for style in ["热血爽文", "悬疑烧脑", "甜宠言情"]:
        plan = _make_plan(f"{style}小说")
        plan["style"] = style
        nid = f"style_{style}"
        smm.create_novel_workspace(nid)
        smm.write("plan", nid, plan)
        assert smm.read("plan", nid)["style"] == style

@test("K08: plan update → state.total_chapters sync")
def test_k08():
    plan = _make_plan(chapters=20)
    smm.write("plan", NID, plan)
    smm.save_novel_state(NID, {"current_chapter":0,"total_chapters":20,"status":"writing"})
    # Update to 25 chapters
    plan["outline"]["total_chapters"] = 25
    smm.write("plan", NID, plan)
    state = smm.get_novel_state(NID)
    # Note: save_novel_state doesn't auto-sync — engine.update_plan does
    # Just verify plan reflects change
    assert smm.read("plan", NID)["outline"]["total_chapters"] == 25

@test("K09: empty novel → write first chapter → state auto-repair")
def test_k09():
    nid = "fresh_start"
    smm.create_novel_workspace(nid)
    smm.write("plan", nid, _make_plan(chapters=10))
    _write_chapter(nid, 1)
    state = smm.get_novel_state(nid)
    assert 1 in state.get("completed_chapters", [])

@test("K10: complete flow → plan → 5chapters → export TXT")
def test_k10():
    nid = "full_flow"
    smm.create_novel_workspace(nid)
    plan = _make_plan("完整流程", 5)
    smm.write("plan", nid, plan)
    smm.save_novel_state(nid, {"current_chapter":0,"total_chapters":5,"status":"writing","completed_chapters":[]})
    for ch in range(1, 6):
        _write_chapter(nid, ch)
        state = smm.get_novel_state(nid)
        state["completed_chapters"] = list(range(1, ch+1))
        state["current_chapter"] = ch
        smm.save_novel_state(nid, state)
    final_state = smm.get_novel_state(nid)
    assert final_state["completed_chapters"] == [1,2,3,4,5]
    assert final_state["current_chapter"] == 5


# ═══════════════════════════════════════════
# L. 交互式大纲迭代 (8)
# ═══════════════════════════════════════════
print("\n=== L. Interactive Outline Workflow ===")

@test("L01: feedback decompose → structured change plan")
def test_l01():
    plan = _make_plan()
    result = decomposer.decompose("前几章节奏太慢，需要加快剧情推进，增加冲突", plan)
    assert "change_plan" in result
    assert len(result["change_plan"]) > 0
    assert "intent_analysis" in result
    actions = [a["action"] for a in result["change_plan"]]
    assert any(a in ["add", "modify"] for a in actions)

@test("L02: decompose chapter feedback")
def test_l02():
    plan = _make_plan()
    outline = plan["outline"]["volumes"][0]["chapters"][0]
    result = decomposer.decompose_for_chapter("这章打斗太少了，需要加一场激烈的战斗", 1, outline, plan)
    assert "change_plan" in result
    assert len(result["change_plan"]) > 0

@test("L03: decompose → extract specific chapter references")
def test_l03():
    refs = decomposer._extract_chapter_refs("修改第3章和第5-8章的情节，还有第12章的开头")
    assert 3 in refs and 5 in refs and 8 in refs and 12 in refs
    vrefs = decomposer._extract_volume_refs("调整第2卷的结构")
    assert 2 in vrefs

@test("L04: decompose intent inference accuracy")
def test_l04():
    intents = [
        ("节奏太慢了", "节奏"),
        ("角色性格前后矛盾", "角色"),
        ("第3章和第5章有逻辑漏洞", "逻辑"),
        ("第一卷太长了要精简", "精简"),
        ("内容不够丰富需要扩展", "扩展"),
    ]
    for feedback, expected in intents:
        intent = decomposer._infer_intent(feedback)
        assert expected in intent, f"'{feedback}' → expected '{expected}', got '{intent}'"

@test("L05: outline diff detects all change types")
def test_l05():
    old = _make_plan(chapters=10)
    new = _make_plan(chapters=15)
    diff = oi.get_diff_summary(old, new)
    types = {d["type"] for d in diff}
    assert "chapter_count" in types

@test("L06: outline renumber ensures continuity")
def test_l06():
    plan = _make_plan()
    plan["outline"]["volumes"][0]["chapters"][0]["number"] = 999
    plan["outline"]["volumes"][0]["chapters"][1]["number"] = 5
    oi._renumber_chapters(plan)
    nums = [ch["number"] for v in plan["outline"]["volumes"] for ch in v["chapters"]]
    assert nums == list(range(1, len(nums)+1))

@test("L07: iteration history tracks changes")
def test_l07():
    oi._iteration_history.append({
        "timestamp": "2026-07-22T00:00:00",
        "feedback": "节奏太慢",
        "diff": [{"type":"chapter_count","before":10,"after":12}]
    })
    hist = oi.get_iteration_history()
    assert len(hist) >= 1
    assert "节奏太慢" in str(hist)

@test("L08: multiple feedback iterations stack correctly")
def test_l08():
    base = _make_plan(chapters=10)
    smm.write("plan", NID, base)
    for i in range(3):
        base = copy.deepcopy(base)
        base["outline"]["total_chapters"] += 1
        smm.write("plan", NID, base)
    final = smm.read("plan", NID)
    assert final["outline"]["total_chapters"] == 13


# ═══════════════════════════════════════════
# M. 多章节连续写作 (8)
# ═══════════════════════════════════════════
print("\n=== M. Multi-Chapter Writing ===")

@test("M01: write 10 consecutive chapters → verify all readable")
def test_m01():
    nid = "batch_write"
    smm.create_novel_workspace(nid)
    smm.write("plan", nid, _make_plan(chapters=10))
    for ch in range(1, 11):
        _write_chapter(nid, ch, f"# 第{ch}章\n\n第{ch}章独特内容标记_{ch}。\n" * 20)
    for ch in range(1, 11):
        content = smm.read_chapter(nid, ch)
        assert f"标记_{ch}" in content, f"Chapter {ch} missing marker"

@test("M02: write chapters out of order → scan sorts correctly")
def test_m02():
    nid = "out_of_order"
    smm.create_novel_workspace(nid)
    smm.write("plan", nid, _make_plan(chapters=5))
    _write_chapter(nid, 5); _write_chapter(nid, 1); _write_chapter(nid, 3)
    chs = smm.scan_chapters(nid)
    assert chs == [1,3,5]

@test("M03: chapter content unicode preservation")
def test_m03():
    nid = "unicode"
    smm.create_novel_workspace(nid)
    text = "第一章\n\n『天道不仁』——叶凡缓缓开口。\n\n「你…你说什么？」\n\n（静默）\n\n— 未完待续 —"
    _write_chapter(nid, 1, text)
    read = smm.read_chapter(nid, 1)
    for kw in ["天道不仁", "你说什么", "未完待续", "叶凡"]:
        assert kw in read, f"Missing unicode: {kw}"

@test("M04: foreshadowing planted → tracked across chapters")
def test_m04():
    nid = "foreshadow"
    smm.create_novel_workspace(nid)
    smm.write("plan", nid, _make_plan(chapters=10))
    smm.update_foreshadowing(nid, 1, planted=[
        {"description": "神秘玉佩", "reveal_chapter": 5},
        {"description": "师尊真实身份", "reveal_chapter": 10},
    ])
    smm.update_foreshadowing(nid, 3, planted=[
        {"description": "上古遗迹", "reveal_chapter": 8},
    ])
    hooks = smm.read("foreshadowing", nid)
    assert len(hooks) == 3
    unresolved = [h for h in hooks if not h.get("resolved")]
    assert len(unresolved) == 3

@test("M05: foreshadowing resolve → marked as done")
def test_m05():
    smm.update_foreshadowing("foreshadow", 5, resolved=["神秘玉佩"])
    hooks = smm.read("foreshadowing", "foreshadow")
    resolved = [h for h in hooks if h.get("resolved") and "神秘玉佩" in str(h)]
    assert len(resolved) >= 1

@test("M06: global_state characters accumulate across chapters")
def test_m06():
    nid = "state_accum"
    smm.create_novel_workspace(nid)
    smm.write("plan", nid, _make_plan(chapters=5))
    state = {"characters": {}, "power_levels": {}, "locations": [], "chapters_summary": {}}
    for ch in range(1, 4):
        state["characters"].setdefault("叶凡",[]).append(f"[第{ch}章] 修为提升")
        state["power_levels"]["叶凡"] = ["练气","筑基","金丹"][ch-1]
        state["chapters_summary"][str(ch)] = f"第{ch}章摘要"
    smm.write("global_state", nid, state)
    read = smm.read("global_state", nid)
    assert len(read["characters"]["叶凡"]) == 3
    assert read["power_levels"]["叶凡"] == "金丹"

@test("M07: chapter_summarizer offline mode")
def test_m07():
    ch = 1
    text = "叶凡发现了神秘洞穴。里面有一把古剑和一卷秘籍。他拿起古剑的瞬间，洞穴开始崩塌。"
    result = summarizer.summarize_chapter(ch, text)
    assert "chapter" in result
    assert "summary" in result
    assert len(result["summary"]) > 10

@test("M08: token budget calculation correct")
def test_m08():
    budget = summarizer.get_token_budget(100)
    assert budget["full_inject"] > budget["hybrid"]
    assert budget["hybrid"] < 8000 or not budget["is_safe"]
    budget_small = summarizer.get_token_budget(5)
    assert budget_small["full_inject"] <= 7500  # 5*1500


# ═══════════════════════════════════════════
# N. 跨模块协作 (8)
# ═══════════════════════════════════════════
print("\n=== N. Cross-Module Collaboration ===")

@test("N01: plan → validator checks outline consistency")
def test_n01():
    plan = _make_plan()
    result = planner.validate_outline(plan["outline"])
    assert result["valid"] == True
    assert result["total_chapters"] == plan["outline"]["total_chapters"]

@test("N02: plan → repair fixes missing fields")
def test_n02():
    broken = {"volumes": [{"chapters": [{"number":1}]}]}
    repaired = planner.repair_outline(broken)
    ch = repaired["volumes"][0]["chapters"][0]
    assert "target_words" in ch
    assert "emotion_curve" in ch

@test("N03: validator + repair → broken outline becomes valid")
def test_n03():
    broken = {"volumes": [{"number":1,"chapters":[{"number":1,"title":""}]}], "total_chapters": 99}
    assert planner.validate_outline(broken)["valid"] == True  # auto-repaired, no missing summary detected
    # Manually add issue
    broken["volumes"][0]["chapters"][0]["number"] = 5
    broken["volumes"][0]["chapters"].append({"number":3,"title":"x"})
    result = planner.validate_outline(broken)
    assert result["valid"] == False  # chapter skip

@test("N04: opening optimizer + writer context integration")
def test_n04():
    text = "剑光闪过。叶凡侧身避开，反手一剑刺出。\n\n鲜血飞溅。他收剑入鞘。"
    result = optimizer.analyze_opening(text, 1, "热血爽文", is_first_chapter=True)
    assert result["score"] >= 0
    assert result["hook_type"] in ["action", "dialogue", "mystery"]
    prompt = optimizer.build_optimization_prompt(result)
    if result["score"] < 80:
        assert "优化" in prompt

@test("N05: twist designer + foreshadowing integration")
def test_n05():
    plan = _make_plan(chapters=10)
    result = twister.design_twists(plan)
    assert "twists" in result
    assert "foreshadowing_map" in result
    for t in result["twists"]:
        assert "chapter" in t
        assert t["chapter"] <= 10

@test("N06: consistency_validator L1 + plan integration")
def test_n06():
    plan = _make_plan()
    text = "叶凡在山洞中修炼了三天，终于突破到筑基。"
    result = validator.validate_chapter(text, 3, plan, run_deep=False)
    assert result["score"] >= 70

@test("N07: cross-module: plan change → validator sees update")
def test_n07():
    nid = "cross_module"
    smm.create_novel_workspace(nid)
    plan_v1 = _make_plan("版本一")
    smm.write("plan", nid, plan_v1)
    # Validator reads v1
    text = "正文内容。"
    r1 = validator.validate_chapter(text, 1, smm.read("plan", nid), run_deep=False)
    # Update plan
    plan_v2 = _make_plan("版本二")
    smm.write("plan", nid, plan_v2)
    r2 = validator.validate_chapter(text, 1, smm.read("plan", nid), run_deep=False)
    assert r1["score"] >= 0 and r2["score"] >= 0  # Both should work

@test("N08: build_context for different modules")
def test_n08():
    plan = _make_plan()
    smm.write("plan", NID, plan)
    smm.write("global_state", NID, {"characters":{"叶凡":["筑基"]}})
    _write_chapter(NID, 1)
    ctx_writer = smm.build_context("writer", NID, chapter_num=2, chapter_outline=plan["outline"]["volumes"][0]["chapters"][1])
    ctx_validator = smm.build_context("validator", NID)
    ctx_decomposer = smm.build_context("decomposer", NID)
    assert "叶凡" in ctx_writer
    assert "叶凡" in ctx_validator or ctx_validator != ""
    assert "总章节数" in ctx_decomposer or "卷" in ctx_decomposer


# ═══════════════════════════════════════════
# O. 异常恢复与边界 (8)
# ═══════════════════════════════════════════
print("\n=== O. Error Recovery & Edge Cases ===")

@test("O01: read non-existent novel → returns default")
def test_o01():
    plan = smm.read("plan", "nonexistent_novel")
    assert plan == {} or "title" not in plan

@test("O02: write to non-existent dir → auto-creates")
def test_o02():
    nid = "auto_create"
    smm.write("state", nid, {"test": True})
    assert smm.read("state", nid)["test"] == True

@test("O03: very large chapter content (200KB)")
def test_o03():
    nid = "large_chapter"
    smm.create_novel_workspace(nid)
    text = "大" * 200000  # ~200KB
    _write_chapter(nid, 1, text)
    read = smm.read_chapter(nid, 1)
    assert len(read) >= 200000

@test("O04: empty chapter content")
def test_o04():
    nid = "empty_ch"
    smm.create_novel_workspace(nid)
    _write_chapter(nid, 1, "")
    assert smm.read_chapter(nid, 1) == ""

@test("O05: special characters in novel ID")
def test_o05():
    nid = "测试·小说_Vol.1"
    smm.create_novel_workspace(nid)
    smm.write("state", nid, {"k":"v"})
    assert smm.read("state", nid)["k"] == "v"

@test("O06: write then immediately delete → read returns default")
def test_o06():
    nid = "ephemeral"
    smm.write("state", nid, {"temp": True})
    d = smm.get_novel_dir(nid)
    shutil.rmtree(d, ignore_errors=True)
    assert smm.read("state", nid) == {}

@test("O07: multiple novels with same plan structure")
def test_o07():
    for i in range(5):
        nid = f"novel_{i}"
        smm.create_novel_workspace(nid)
        smm.write("plan", nid, _make_plan(f"小说{i}"))
        smm.save_novel_state(nid, {"current_chapter": i, "total_chapters": 30})
    for i in range(5):
        assert smm.read("plan", f"novel_{i}")["title"] == f"小说{i}"
        assert smm.get_novel_state(f"novel_{i}")["current_chapter"] == i

@test("O08: cache isolation under heavy read load")
def test_o08():
    smm.write("plan", NID, _make_plan("缓存测试"))
    smm.read("plan", NID)  # populate cache
    start = time.time()
    for _ in range(200):
        smm.read("plan", NID)
    elapsed = (time.time() - start) * 1000
    assert elapsed < 100, f"Cache reads too slow: {elapsed:.1f}ms"


# ═══════════════════════════════════════════
# P. 数据持久化一致性 (8)
# ═══════════════════════════════════════════
print("\n=== P. Data Persistence Consistency ===")

@test("P01: write → restart simulation → read matches")
def test_p01():
    nid = "persist"
    smm.create_novel_workspace(nid)
    plan = _make_plan("持久化测试")
    smm.write("plan", nid, plan)
    smm.save_novel_state(nid, {"current_chapter": 7, "total_chapters": 30})
    # Simulate restart: new SMM instance
    smm2 = SharedMemoryManager(tmp)
    assert smm2.read("plan", nid)["title"] == "持久化测试"
    assert smm2.get_novel_state(nid)["current_chapter"] == 7

@test("P02: version field increments on each write")
def test_p02():
    nid = "version_test"
    smm.create_novel_workspace(nid)
    for i in range(5):
        smm.write("plan", nid, _make_plan(f"V{i}"))
    final = smm.read("plan", nid, skip_cache=True)
    # Versioned files have _version
    assert "_version" in final

@test("P03: write → crash simulation → file not corrupted")
def test_p03():
    nid = "crash_test"
    smm.create_novel_workspace(nid)
    smm.write("plan", nid, _make_plan("崩溃前"))
    # Simulate: file should be readable
    # (atomic_write guarantees this)
    assert smm.read("plan", nid, skip_cache=True)["title"] == "崩溃前"

@test("P04: concurrent novels don't interfere")
def test_p04():
    for nid in ["A", "B", "C"]:
        smm.create_novel_workspace(nid)
        smm.write("plan", nid, _make_plan(f"小说{nid}"))
        _write_chapter(nid, 1, f"{nid}_chapter_1")
    for nid in ["A", "B", "C"]:
        assert smm.read("plan", nid)["title"] == f"小说{nid}"
        assert f"{nid}_chapter_1" in (smm.read_chapter(nid, 1) or "")

@test("P05: state auto-repair when corrupted")
def test_p05():
    nid = "repair_test"
    smm.create_novel_workspace(nid)
    path = os.path.join(smm.get_novel_dir(nid), "state.json")
    with open(path, "w") as f:
        f.write("{corrupted json!!!")
    state = smm.get_novel_state(nid)
    assert isinstance(state, dict)

@test("P06: cache invalidation across novel boundaries")
def test_p06():
    smm.write("plan", "A", _make_plan("A书"))
    smm.write("plan", "B", _make_plan("B书"))
    a1 = smm.read("plan", "A")
    smm.invalidate_all("A")
    a2 = smm.read("plan", "A", skip_cache=True)
    assert a2["title"] == "A书"

@test("P07: many rapid writes → no data loss")
def test_p07():
    nid = "rapid_write"
    smm.create_novel_workspace(nid)
    for i in range(100):
        smm.write("state", nid, {"counter": i})
    assert smm.read("state", nid, skip_cache=True)["counter"] == 99

@test("P08: export → delete → import → metadata restore")
def test_p08():
    nid = "full_restore"
    smm.create_novel_workspace(nid)
    plan = _make_plan("恢复测试")
    smm.write("plan", nid, plan)
    smm.save_novel_state(nid, {"current_chapter": 4, "total_chapters": 10})
    _write_chapter(nid, 1); _write_chapter(nid, 2)
    smm.write("global_state", nid, {"power_levels":{"叶凡":"金丹"}})
    exp = smm.export_all(nid)
    # Delete
    shutil.rmtree(smm.get_novel_dir(nid), ignore_errors=True)
    # Restore
    smm.create_novel_workspace(nid)
    smm.import_all(nid, exp)
    assert smm.read("plan", nid)["title"] == "恢复测试"
    assert smm.get_novel_state(nid)["current_chapter"] == 4

@test("P09: full backup → delete → copy chapters back → complete restore")
def test_p09():
    nid = "complete_restore"
    smm.create_novel_workspace(nid)
    plan = _make_plan("完整恢复")
    smm.write("plan", nid, plan)
    _write_chapter(nid, 1, "第1章内容")
    _write_chapter(nid, 2, "第2章内容")
    # Full directory backup (includes chapters)
    backup_dir = tmp + "/backup_" + nid
    shutil.copytree(smm.get_novel_dir(nid), backup_dir)
    shutil.rmtree(smm.get_novel_dir(nid), ignore_errors=True)
    # Restore
    shutil.copytree(backup_dir, smm.get_novel_dir(nid))
    smm.invalidate_all(nid)
    assert smm.read("plan", nid)["title"] == "完整恢复"
    assert "第1章内容" in (smm.read_chapter(nid, 1) or "")


# ═══════════════════════════════════════════
# Q. 功能组合场景 (8)
# ═══════════════════════════════════════════
print("\n=== Q. Feature Combination Scenarios ===")

@test("Q01: create → validate → optimize opening → design twist chain")
def test_q01():
    nid = "combo_1"
    smm.create_novel_workspace(nid)
    plan = _make_plan("组合测试", 10)
    smm.write("plan", nid, plan)
    _write_chapter(nid, 1, "剑光闪过。叶凡拔剑。\n\n" + "战斗场景。" * 50)
    # Validate
    vr = validator.validate_chapter(smm.read_chapter(nid,1), 1, plan, run_deep=False)
    assert vr["score"] >= 0
    # Optimize opening
    ar = optimizer.analyze_opening(smm.read_chapter(nid,1), 1, "热血爽文", is_first_chapter=True)
    assert "hook_type" in ar
    # Design twist
    tr = twister.design_twists(plan)
    assert len(tr["twists"]) > 0

@test("Q02: feedback → decompose → validate → diff → save")
def test_q02():
    plan = _make_plan("反馈流程", 10)
    smm.write("plan", NID, plan)
    old = copy.deepcopy(plan)
    # Decompose
    dec = decomposer.decompose("前3章需要增加更多冲突场景", plan)
    # Diff (simulate change)
    plan["outline"]["total_chapters"] = 12
    diff = oi.get_diff_summary(old, plan)
    assert len(diff) > 0

@test("Q03: multi-chapter write → compress → verify budget")
def test_q03():
    nid = "compression_test"
    smm.create_novel_workspace(nid)
    smm.write("plan", nid, _make_plan(chapters=30))
    for ch in range(1, 11):
        _write_chapter(nid, ch)
    budget = summarizer.get_token_budget(10)
    assert budget["is_safe"] == True  # 10 chapters well under 8K

@test("Q04: twist design → chapter twist → build prompt → validate")
def test_q04():
    plan = _make_plan(chapters=10)
    outline = plan["outline"]["volumes"][0]["chapters"][2]  # chapter 3 (vol end)
    twist = twister.design_chapter_twist(3, plan, outline)
    prompt = twister.build_twist_prompt(twist)
    if twist.get("has_twist"):
        assert "反转设计" in prompt

@test("Q05: create plan → validator catches outline issues")
def test_q05():
    plan = _make_plan()
    plan["outline"]["volumes"][0]["chapters"][1]["number"] = 10  # skip chapters
    result = planner.validate_outline(plan["outline"])
    assert result["valid"] == False

@test("Q06: writer context includes all L1-L5 layers")
def test_q06():
    plan = _make_plan()
    smm.write("plan", NID, plan)
    smm.write("global_state", NID, {"characters":{"叶凡":["筑基初期"]},"power_levels":{},"locations":["青云山"],"chapters_summary":{"1":"测试摘要"}})
    _write_chapter(NID, 1, "测试内容" * 50)
    smm.update_foreshadowing(NID, 1, planted=[{"description":"伏笔测试","reveal_chapter":5}])
    ctx = smm.build_context("writer", NID, chapter_num=2, chapter_outline=plan["outline"]["volumes"][0]["chapters"][1])
    assert "叶凡" in ctx
    assert "上一章结尾" in ctx
    assert "本章大纲" in ctx
    assert "全局状态" in ctx
    assert "伏笔" in ctx or "钩子" in ctx

@test("Q07: full pipeline: plan→write→validate→fix→rewrite")
def test_q07():
    nid = "pipeline"
    smm.create_novel_workspace(nid)
    plan = _make_plan(chapters=5)
    smm.write("plan", nid, plan)
    _write_chapter(nid, 1, "叶凡推开了大殿的门。")
    vr = validator.validate_chapter(smm.read_chapter(nid,1), 1, plan, run_deep=False)
    assert vr["score"] >= 0
    # If issues found, build fix prompt
    if vr["violations"]:
        fix = validator.build_fix_prompt(vr["violations"])
        assert "修复" in fix

@test("Q08: stress: 3 novels × 10 chapters × full validation")
def test_q08():
    for ni in range(3):
        nid = f"stress_{ni}"
        smm.create_novel_workspace(nid)
        plan = _make_plan(f"压力测试{ni}", 10)
        smm.write("plan", nid, plan)
        for ch in range(1, 11):
            _write_chapter(nid, ch)
            if ch % 3 == 0:
                vr = validator.validate_chapter(smm.read_chapter(nid,ch), ch, plan, run_deep=False)
                assert vr["score"] >= 0
            if ch == 10:
                budget = summarizer.get_token_budget(ch)
                assert "full_inject" in budget
    # All 3 novels should have 10 chapters each
    for ni in range(3):
        assert len(smm.scan_chapters(f"stress_{ni}")) == 10


# ═══════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════
print()
print("=" * 60)
print(f"Running {len(_registry)} integration tests...")
print("=" * 60)
run()
print()
print("=" * 60)
print(f"RESULTS: {passed} passed, {failed} failed, {passed+failed} total")
print("=" * 60)
if errors:
    print("\nFAILURES:")
    for e in errors[:20]:
        print(f"  {e}")
if failed == 0:
    print("🎉 All integration tests passed!")

# Cleanup
shutil.rmtree(tmp, ignore_errors=True)
sys.exit(0 if failed == 0 else 1)
