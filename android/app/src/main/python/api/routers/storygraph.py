"""NovelGenerator — 剧情图谱 / 弧 / 校准 / 可视化 / 快捷操作 API Router"""
import json
import os
from typing import Optional
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

router = APIRouter()

from ..deps import engine, log, _validate_novel_id, _validate_chapter_range
from core.atomic_io import atomic_write_json

def _read_novel_file(novel_id: str, filename: str) -> dict:
    """安全读取小说目录下的 JSON 文件"""
    novel_dir = engine.memory.get_novel_dir(novel_id)
    path = os.path.join(novel_dir, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"{filename} 不存在，请先生成大纲")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取 {filename} 失败: {str(e)}")


@router.get("/api/novels/{novel_id}/storygraph")
async def get_storygraph(novel_id: str, chapter: int = 0):
    """获取剧情图谱数据（剧情线/伏笔账本/角色快照/因果链）

    Query params:
        chapter: 指定章节号，仅返回该章及之前的剧情数据（时间回溯）
                 0或省略 = 返回全部
    """
    data = _read_novel_file(novel_id, "storygraph.json")
    last_updated = data.get("last_updated_chapter", 0)
    version = data.get("version", 0)

    # 如果指定了章节号，过滤到该时间点
    if chapter > 0 and chapter < last_updated:
        data = _filter_storygraph_to_chapter(data, chapter)

    return {
        "novel_id": novel_id,
        "plot_threads": data.get("plot_threads", {}),
        "foreshadow_ledger": data.get("foreshadow_ledger", {}),
        "char_snapshots": data.get("char_snapshots", {}),
        "causal_links": data.get("causal_links", []),
        "version": version,
        "last_updated_chapter": last_updated,
        "filtered_to_chapter": chapter if chapter > 0 and chapter < last_updated else None,
        # 计算摘要统计
        "stats": _compute_storygraph_stats(data),
    }


def _filter_storygraph_to_chapter(data: dict, chapter: int) -> dict:
    """将剧情图谱数据过滤到指定章节时间点"""
    import copy
    filtered = copy.deepcopy(data)

    # 过滤剧情线节点（只保留 chapter <= 指定章的）
    for tid in filtered.get("plot_threads", {}):
        t = filtered["plot_threads"][tid]
        t["key_nodes"] = [n for n in t.get("key_nodes", []) if n["chapter"] <= chapter]
        # 如果在指定章时该线程还没有节点，状态回退
        if not t["key_nodes"] and t.get("status") in ("advancing", "climax", "resolved"):
            t["status"] = "active"

    # 过滤伏笔（只保留 planted_chapter <= 指定章的）
    filtered["foreshadow_ledger"] = {
        fid: fs for fid, fs in filtered.get("foreshadow_ledger", {}).items()
        if fs.get("planted_chapter", 0) <= chapter
    }
    # 回退伏笔状态：在指定章时尚未回收的，状态恢复为hinted/planted
    for fs in filtered["foreshadow_ledger"].values():
        if fs.get("actual_payoff_chapter") and fs["actual_payoff_chapter"] > chapter:
            fs["actual_payoff_chapter"] = None
            fs["status"] = "hinted" if fs.get("hint_count", 1) > 1 else "planted"
        if fs.get("last_hint_chapter", 0) > chapter:
            fs["last_hint_chapter"] = max(fs.get("planted_chapter", 0),
                                          min(n for n in [fs.get("planted_chapter",0)] if n <= chapter))

    # 过滤角色快照（回退到指定章时的状态）
    for name in filtered.get("char_snapshots", {}):
        snap = filtered["char_snapshots"][name]
        if snap.get("last_chapter_appeared", 0) > chapter:
            snap["last_chapter_appeared"] = 0
        # 过滤关系变化
        snap["relationship_changes"] = [
            rc for rc in snap.get("relationship_changes", [])
            if rc.get("chapter", 0) <= chapter
        ]

    # 过滤因果链
    filtered["causal_links"] = [
        cl for cl in filtered.get("causal_links", [])
        if cl.get("cause_chapter", 0) <= chapter
    ]

    filtered["last_updated_chapter"] = chapter
    filtered["filtered"] = True
    return filtered


def _compute_storygraph_stats(data: dict) -> dict:
    """计算剧情图谱统计摘要"""
    return {
        "total_threads": len(data.get("plot_threads", {})),
        "active_threads": sum(1 for t in data.get("plot_threads", {}).values() if t.get("status") in ("active", "advancing", "climax")),
        "total_foreshadows": len(data.get("foreshadow_ledger", {})),
        "unresolved_foreshadows": sum(1 for f in data.get("foreshadow_ledger", {}).values() if f.get("status") in ("planted", "hinted")),
        "resolved_foreshadows": sum(1 for f in data.get("foreshadow_ledger", {}).values() if f.get("status") == "resolved"),
        "tracked_characters": len(data.get("char_snapshots", {})),
        "causal_links": len(data.get("causal_links", [])),
    }


@router.get("/api/novels/{novel_id}/arcs")
async def get_arcs(novel_id: str):
    """获取剧情弧规划数据"""
    data = _read_novel_file(novel_id, "arcplans.json")
    arcs = data.get("arcs", [])
    # 计算当前弧
    state = engine.memory.get_novel_state(novel_id)
    current_chapter = state.get("completed_chapters", 0) + 1
    current_arc = None
    for arc in arcs:
        ch_list = arc.get("chapters", [])
        if ch_list and current_chapter in ch_list:
            pos = ch_list.index(current_chapter) + 1
            current_arc = {**arc, "current_position": pos, "total_in_arc": len(ch_list)}
            break
    return {
        "novel_id": novel_id,
        "arcs": arcs,
        "current_chapter": current_chapter,
        "current_arc": current_arc,
        "stats": {
            "total_arcs": len(arcs),
            "completed_arcs": sum(1 for a in arcs if a.get("end_chapter", 0) < current_chapter),
            "type_distribution": {
                t: sum(1 for a in arcs if a.get("type") == t)
                for t in ["setup", "rising", "climax", "resolution"]
            },
        }
    }


@router.get("/api/novels/{novel_id}/calibration")
async def get_calibration(novel_id: str):
    """获取最新的剧情校准报告"""
    data = _read_novel_file(novel_id, "calibration.json")
    return data


@router.get("/api/novels/{novel_id}/storygraph/visualization")
async def get_storygraph_visualization(novel_id: str, chapter: int = 0):
    """获取剧情图谱可视化数据：人物关系图 + 剧情线图

    Query params:
        chapter: 指定章节号过滤（0=全部）
    """
    data = _read_novel_file(novel_id, "storygraph.json")
    if chapter > 0 and chapter < data.get("last_updated_chapter", 0):
        data = _filter_storygraph_to_chapter(data, chapter)

    return {
        "novel_id": novel_id,
        "character_relations": _build_character_relation_graph(data),
        "plot_timeline": _build_plot_timeline(data),
    }


def _write_novel_file(novel_id: str, filename: str, data: dict):
    """安全写入小说目录下的 JSON 文件（原子写入）"""
    from core.atomic_io import atomic_write_json
    novel_dir = engine.memory.get_novel_dir(novel_id)
    path = os.path.join(novel_dir, filename)
    atomic_write_json(path, data)


def _validate_thread_fields(body: dict, is_new: bool = False):
    """校验剧情线字段"""
    ALLOWED_TYPES = {"main_plot", "subplot", "character_arc", "mystery"}
    ALLOWED_STATUS = {"dormant", "active", "advancing", "climax", "resolved"}

    if is_new and not body.get("name"):
        raise HTTPException(400, "name 为必填字段")
    if "type" in body and body["type"] not in ALLOWED_TYPES:
        raise HTTPException(400, f"type 必须是 {ALLOWED_TYPES} 之一")
    if "status" in body and body["status"] not in ALLOWED_STATUS:
        raise HTTPException(400, f"status 必须是 {ALLOWED_STATUS} 之一")
    if "priority" in body and not (1 <= body["priority"] <= 5):
        raise HTTPException(400, "priority 必须在 1-5 之间")
    if "current_tension" in body and not (1 <= body["current_tension"] <= 10):
        raise HTTPException(400, "current_tension 必须在 1-10 之间")
    if "name" in body and len(body["name"]) > 30:
        raise HTTPException(400, "name 最长 30 字")
    if "description" in body and len(body["description"]) > 200:
        raise HTTPException(400, "description 最长 200 字")


@router.put("/api/novels/{novel_id}/storygraph/threads/{thread_id}")
async def update_thread(novel_id: str, thread_id: str, body: dict):
    """更新或创建剧情线（partial update）"""
    _validate_thread_fields(body)

    data = _read_novel_file(novel_id, "storygraph.json")
    threads = data.setdefault("plot_threads", {})

    if thread_id not in threads:
        threads[thread_id] = {
            "id": thread_id, "name": body.get("name", thread_id),
            "type": "subplot", "status": "active", "priority": 3,
            "description": "", "key_nodes": [], "next_planned": "",
            "current_tension": 5, "characters": [],
        }

    thread = threads[thread_id]
    updatable = ("name", "type", "status", "priority", "description",
                 "current_tension", "next_planned", "characters", "key_nodes")
    for key in updatable:
        if key in body:
            thread[key] = body[key]

    data["version"] = data.get("version", 0) + 1
    _write_novel_file(novel_id, "storygraph.json", data)
    engine.memory.invalidate_all(novel_id)
    return {"ok": True, "thread_id": thread_id}


@router.delete("/api/novels/{novel_id}/storygraph/threads/{thread_id}")
async def delete_thread(novel_id: str, thread_id: str):
    """软删除剧情线（标记为 resolved）"""
    data = _read_novel_file(novel_id, "storygraph.json")
    if thread_id not in data.get("plot_threads", {}):
        raise HTTPException(404, "剧情线不存在")
    data["plot_threads"][thread_id]["status"] = "resolved"
    data["version"] = data.get("version", 0) + 1
    _write_novel_file(novel_id, "storygraph.json", data)
    engine.memory.invalidate_all(novel_id)
    return {"ok": True}


@router.put("/api/novels/{novel_id}/storygraph/foreshadows/{fs_id}")
async def update_foreshadow(novel_id: str, fs_id: str, body: dict):
    """更新伏笔"""
    data = _read_novel_file(novel_id, "storygraph.json")
    ledger = data.setdefault("foreshadow_ledger", {})

    if fs_id not in ledger:
        raise HTTPException(404, "伏笔不存在")

    fs = ledger[fs_id]
    updatable = ("description", "planned_payoff_chapter", "status",
                 "importance", "thread_id")
    for key in updatable:
        if key in body:
            fs[key] = body[key]

    # 如果手动标记为 resolved，记录回收章节
    if body.get("status") == "resolved":
        fs["actual_payoff_chapter"] = body.get("actual_payoff_chapter") or data.get("last_updated_chapter", 0)

    data["version"] = data.get("version", 0) + 1
    _write_novel_file(novel_id, "storygraph.json", data)
    engine.memory.invalidate_all(novel_id)
    return {"ok": True, "fs_id": fs_id}


@router.post("/api/novels/{novel_id}/storygraph/foreshadows")
async def create_foreshadow(novel_id: str, body: dict):
    """创建新伏笔"""
    fs_id = body.get("id", "")
    if not fs_id:
        raise HTTPException(400, "id 为必填字段")

    data = _read_novel_file(novel_id, "storygraph.json")
    ledger = data.setdefault("foreshadow_ledger", {})

    ledger[fs_id] = {
        "id": fs_id,
        "description": body.get("description", ""),
        "planted_chapter": body.get("planted_chapter", 1),
        "planned_payoff_chapter": body.get("planned_payoff_chapter", 20),
        "actual_payoff_chapter": None,
        "status": body.get("status", "planted"),
        "hint_count": 1,
        "last_hint_chapter": body.get("planted_chapter", 1),
        "thread_id": body.get("thread_id", ""),
        "importance": body.get("importance", 3),
    }

    data["version"] = data.get("version", 0) + 1
    _write_novel_file(novel_id, "storygraph.json", data)
    engine.memory.invalidate_all(novel_id)
    return {"ok": True, "fs_id": fs_id}


@router.put("/api/novels/{novel_id}/storygraph/characters/{name}")
async def update_character(novel_id: str, name: str, body: dict):
    """更新角色快照"""
    data = _read_novel_file(novel_id, "storygraph.json")
    snaps = data.setdefault("char_snapshots", {})

    if name not in snaps:
        snaps[name] = {
            "name": name, "last_chapter_appeared": 0,
            "current_location": "", "current_power_level": "",
            "status_effects": [], "known_secrets": [],
            "relationship_changes": [], "current_emotion": "",
            "active_goals": [],
        }

    snap = snaps[name]
    updatable = ("current_location", "current_emotion", "current_power_level",
                 "status_effects", "active_goals", "known_secrets")
    for key in updatable:
        if key in body:
            snap[key] = body[key]

    data["version"] = data.get("version", 0) + 1
    _write_novel_file(novel_id, "storygraph.json", data)
    engine.memory.invalidate_all(novel_id)
    return {"ok": True, "name": name}


@router.post("/api/novels/{novel_id}/storygraph/quick-action")
async def quick_action(novel_id: str, body: dict):
    """执行快捷操作（升温/暂停/回收等）

    Body:
        {"type": "thread|foreshadow", "id": "...", "action": "heat_up|pause|resolve|raise_priority|lower_priority"}
    """
    action = body.get("action", "")
    item_type = body.get("type", "")
    item_id = body.get("id", "")

    if not action or not item_id:
        raise HTTPException(400, "action 和 id 为必填字段")

    data = _read_novel_file(novel_id, "storygraph.json")
    result = {"ok": True, "action": action}

    if item_type == "thread":
        threads = data.get("plot_threads", {})
        if item_id not in threads:
            raise HTTPException(404, "剧情线不存在")
        t = threads[item_id]

        if action == "heat_up":
            t["current_tension"] = min(10, t.get("current_tension", 5) + 2)
            if t.get("status") in ("active", "dormant"):
                t["status"] = "advancing"
            result["new_tension"] = t["current_tension"]
        elif action == "cool_down":
            t["current_tension"] = max(1, t.get("current_tension", 5) - 2)
            result["new_tension"] = t["current_tension"]
        elif action == "pause":
            t["status"] = "dormant"
        elif action == "resume":
            t["status"] = "active"
        elif action == "resolve":
            t["status"] = "resolved"
        elif action == "raise_priority":
            t["priority"] = min(5, t.get("priority", 3) + 1)
            result["new_priority"] = t["priority"]
        elif action == "lower_priority":
            t["priority"] = max(1, t.get("priority", 3) - 1)
            result["new_priority"] = t["priority"]
        else:
            raise HTTPException(400, f"未知操作: {action}")

    elif item_type == "foreshadow":
        ledger = data.get("foreshadow_ledger", {})
        if item_id not in ledger:
            raise HTTPException(404, "伏笔不存在")
        f = ledger[item_id]

        if action == "resolve":
            f["status"] = "resolved"
            f["actual_payoff_chapter"] = data.get("last_updated_chapter", 0)
        elif action == "delay":
            offset = body.get("offset", 5)
            f["planned_payoff_chapter"] = f.get("planned_payoff_chapter", 1) + offset
            result["new_payoff"] = f["planned_payoff_chapter"]
        elif action == "advance":
            target = body.get("target_chapter", 1)
            f["planned_payoff_chapter"] = target
            result["new_payoff"] = f["planned_payoff_chapter"]
        else:
            raise HTTPException(400, f"未知操作: {action}")

    else:
        raise HTTPException(400, "type 必须是 thread 或 foreshadow")

    data["version"] = data.get("version", 0) + 1
    _write_novel_file(novel_id, "storygraph.json", data)
    engine.memory.invalidate_all(novel_id)
    return result


def _build_character_relation_graph(data: dict) -> dict:
    """构建人物关系图数据

    Returns:
        {
            "nodes": [{"id": "name", "label": "name", "emotion": "...", "location": "...",
                        "last_chapter": N, "goals": [...]}],
            "edges": [{"source": "charA", "target": "charB", "label": "关系描述", "chapter": N}]
        }
    """
    snaps = data.get("char_snapshots", {})
    nodes = []
    edges = []
    edge_set = set()  # 去重

    for name, snap in snaps.items():
        # 节点
        nodes.append({
            "id": name,
            "label": name,
            "emotion": snap.get("current_emotion", ""),
            "location": snap.get("current_location", ""),
            "last_chapter": snap.get("last_chapter_appeared", 0),
            "goals": snap.get("active_goals", []),
            "secrets": snap.get("known_secrets", []),
            "power_level": snap.get("current_power_level", ""),
        })

        # 边（从关系变化中提取）
        for rc in snap.get("relationship_changes", []):
            target = rc.get("with", "")
            if not target or target not in snaps:
                continue
            edge_key = tuple(sorted([name, target]))
            if edge_key in edge_set:
                continue
            edge_set.add(edge_key)
            edges.append({
                "source": name,
                "target": target,
                "label": rc.get("change", "关联"),
                "chapter": rc.get("chapter", 0),
            })

    # 补充：从剧情线的角色列表中推断关系
    threads = data.get("plot_threads", {})
    for t in threads.values():
        chars = t.get("characters", [])
        for i in range(len(chars)):
            for j in range(i + 1, len(chars)):
                edge_key = tuple(sorted([chars[i], chars[j]]))
                if edge_key not in edge_set and chars[i] in snaps and chars[j] in snaps:
                    edge_set.add(edge_key)
                    edges.append({
                        "source": chars[i],
                        "target": chars[j],
                        "label": f"共同参与: {t.get('name', '')[:12]}",
                        "chapter": 0,
                    })

    return {"nodes": nodes, "edges": edges}


def _build_plot_timeline(data: dict) -> dict:
    """构建剧情线时间线图数据

    Returns:
        {
            "lanes": [{"id": "thread_id", "name": "...", "type": "...", "status": "...",
                        "events": [{"chapter": N, "event": "...", "tension": N}],
                        "color": "#..."}],
            "causal_links": [{"from": {"thread_id": "..", "chapter": N}, "to": {...}}],
            "chapter_range": {"min": 1, "max": N}
        }
    """
    threads = data.get("plot_threads", {})
    links = data.get("causal_links", [])

    thread_colors = {
        "main_plot": "#f85149",
        "subplot": "#f0883e",
        "character_arc": "#7c3aed",
        "mystery": "#58a6ff",
    }

    lanes = []
    for tid, t in threads.items():
        nodes = t.get("key_nodes", [])
        events = [{"chapter": n["chapter"], "event": n["event"], "tension": n.get("tension", 5)}
                  for n in sorted(nodes, key=lambda x: x["chapter"])]
        lanes.append({
            "id": tid,
            "name": t.get("name", ""),
            "type": t.get("type", "subplot"),
            "status": t.get("status", "active"),
            "priority": t.get("priority", 3),
            "events": events,
            "color": thread_colors.get(t.get("type", ""), "#8b949e"),
            "characters": t.get("characters", []),
        })

    # 排序：按优先级降序
    lanes.sort(key=lambda x: -x["priority"])

    # 计算章节范围
    all_chapters = []
    for lane in lanes:
        for e in lane["events"]:
            all_chapters.append(e["chapter"])
    for cl in links:
        all_chapters.append(cl.get("cause_chapter", 0))
        all_chapters.append(cl.get("effect_chapter", 0))

    ch_min = min(all_chapters) if all_chapters else 1
    ch_max = max(all_chapters) if all_chapters else 1

    return {
        "lanes": lanes,
        "causal_links": links,
        "chapter_range": {"min": ch_min, "max": ch_max},
    }

