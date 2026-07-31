"""NovelGenerator — 小红书爆款短篇 API Router"""
import json
import os
from typing import Optional
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from core.xiaohongshu import TEMPLATES, create_xhs_novel_pipeline, generate_titles, PRESETS, get_presets

router = APIRouter()

from ..deps import engine, log, _validate_novel_id, _validate_chapter_range

@router.get("/api/xiaohongshu/templates")
async def list_xhs_templates():
    """列出所有可用的小红书爆款模板"""
    result = []
    for key, tpl in TEMPLATES.items():
        result.append({
            "key": key,
            "label": tpl["label"],
            "emoji": tpl["emoji"],
            "genre": tpl["genre"],
            "style": tpl["style"],
            "target_words": tpl.get("target_words", 2500),
            "chapters": len(tpl["chapters"]),
            "emotion_curve": tpl.get("emotion_curve", ""),
            "typical_hooks": tpl.get("typical_hooks", [])[:3],
        })
    return {"ok": True, "templates": result}


@router.post("/api/xiaohongshu/create")
async def create_xhs_novel(req: dict = None):
    """SSE流式创建小红书短篇 — 实时推送进展"""
    if not req:
        req = {}
    template_key = req.get("template", "爽文_打脸逆袭")
    inspiration = req.get("inspiration", "")
    twist = req.get("twist", "")
    
    if template_key not in TEMPLATES:
        raise HTTPException(status_code=400, detail=f"未知模板: {template_key}")
    
    tpl = TEMPLATES[template_key]
    
    async def event_stream():
        try:
            # Phase 0: 开始
            yield f"data: {json.dumps({'type':'start','template':template_key,'total_chapters':len(tpl['chapters']),'label':tpl['label']})}\n\n"
            
            # Phase 1: 生成大纲
            yield f"data: {json.dumps({'type':'progress','phase':'planning','message':'生成大纲中…','pct':5})}\n\n"
            
            creative_input = {
                "genre": tpl["genre"], "style": tpl["style"],
                "inspiration": inspiration or tpl["typical_hooks"][0],
                "target_words": tpl.get("target_words", 2500),
                "normal_pacing": False, "fast_food": True,
            }
            if twist:
                creative_input["inspiration"] += f"\n\n必须包含的剧情反转：{twist}"
            
            plan = engine.planner.plan_stream(creative_input)
            full_plan = None
            async for event in plan:
                if isinstance(event, dict):
                    if event.get("type") == "plan_complete":
                        full_plan = event.get("plan")
                    elif event.get("type") == "progress":
                        yield f"data: {json.dumps({'type':'progress','phase':'planning','message':event.get('label',''),'pct':min(10+event.get('pct',0)//5,25)})}\n\n"
            
            if not full_plan:
                full_plan = {"title": inspiration[:20] or "未命名"}
            
            # Override with template structure
            for i, ch_tpl in enumerate(tpl["chapters"]):
                volumes = full_plan.get("outline", {}).get("volumes", [])
                if volumes and volumes[0].get("chapters"):
                    chapters = volumes[0]["chapters"]
                    if i < len(chapters):
                        chapters[i]["title"] = ch_tpl["title"]
                        chapters[i]["hook"] = ch_tpl["hook"]
                        chapters[i]["target_words"] = ch_tpl["words"]
                        chapters[i]["_function"] = ch_tpl["function"]
            
            yield f"data: {json.dumps({'type':'progress','phase':'planning','message':'大纲完成','pct':25})}\n\n"
            
            # Phase 2: 创建小说
            novel = engine.create_novel(full_plan)
            novel_id = novel.get("title", full_plan.get("title", "xhs_novel"))
            yield f"data: {json.dumps({'type':'novel_created','novel_id':novel_id,'title':full_plan.get('title','')})}\n\n"
            
            # Phase 3: 逐章生成（实时推送文本）
            chapters_result = []
            total_words = 0
            for ch_tpl in tpl["chapters"]:
                ch_num = ch_tpl["number"]
                func = ch_tpl["function"]
                pct_base = 25 + int((ch_num - 1) / len(tpl["chapters"]) * 50)
                
                yield f"data: {json.dumps({'type':'chapter_start','number':ch_num,'title':ch_tpl['title'],'function':func,'pct':pct_base})}\n\n"
                
                feedback = None
                if "★" in func or ch_num == 3:
                    feedback = f"⚠️ 本章是付费卡点章节。开头立即回应钩子，500字内给第一个爽点，反转层层递进，结尾留悬念，至少3次打脸/反转"
                elif ch_num == 2:
                    feedback = f"⚠️ 付费卡点前最后一章。结尾埋最强钩子让读者非看不可。参考：{ch_tpl['hook']}"
                
                full_text = ""
                last_pct = pct_base
                async for chunk in engine.generate_chapter_stream(
                    novel_id=novel_id, chapter_num=ch_num, writing_mode="webnovel", feedback=feedback,
                ):
                    if isinstance(chunk, dict):
                        if chunk.get("type") == "chunk":
                            t = chunk.get("text", "")
                            full_text += t
                            yield f"data: {json.dumps({'type':'chunk','chapter':ch_num,'text':t})}\n\n"
                        elif chunk.get("type") == "status":
                            new_pct = pct_base + 5
                            if new_pct > last_pct:
                                yield f"data: {json.dumps({'type':'progress','phase':'writing','message':f'撰写第{ch_num}章…','pct':new_pct})}\n\n"
                                last_pct = new_pct
                    elif isinstance(chunk, str):
                        full_text += chunk
                        if len(full_text) % 200 < 10:
                            yield f"data: {json.dumps({'type':'chunk','chapter':ch_num,'text':chunk})}\n\n"
                
                if full_text:
                    chapters_result.append({
                        "number": ch_num, "title": ch_tpl["title"], "function": func,
                        "text": full_text, "word_count": len(full_text),
                        "is_cliffhanger": "★" in func or "付费" in func,
                    })
                    total_words += len(full_text)
                
                pct_done = pct_base + 12
                yield f"data: {json.dumps({'type':'chapter_done','number':ch_num,'title':ch_tpl['title'],'words':len(full_text),'pct':min(pct_done,90)})}\n\n"
            
            # Phase 4: 生成标题
            yield f"data: {json.dumps({'type':'progress','phase':'titles','message':'生成爆款标题…','pct':92})}\n\n"
            titles = generate_titles(engine.client, engine.model, full_plan,
                                     " ".join([c["text"][:100] for c in chapters_result if c.get("text")]))
            
            # Phase 5: 完成
            yield f"data: {json.dumps({'type':'done','ok':True,'novel_id':novel_id,'template':template_key,'plan':full_plan,'chapters':chapters_result,'titles':titles,'cliffhanger_chapter':3,'total_words':total_words,'pct':100})}\n\n"
        
        except Exception as e:
            import traceback
            traceback.print_exc()
            yield f"data: {json.dumps({'type':'error','message':str(e)})}\n\n"
    
    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/api/xiaohongshu/presets")
async def list_xhs_presets(template: str = None):
    """列出小红书预设组合。可选按模板筛选"""
    presets = get_presets(template)
    return {"ok": True, "presets": presets, "total": len(presets)}


@router.post("/api/xiaohongshu/titles")
async def generate_titles_endpoint(req: dict = None):
    """为已有小说生成5个小红书风格标题"""
    if not req:
        req = {}
    novel_id = req.get("novel_id", "")
    if not novel_id:
        raise HTTPException(status_code=400, detail="缺少 novel_id")
    
    try:
        plan = engine.memory.read("plan", novel_id)
        if not plan:
            raise HTTPException(status_code=404, detail=f"小说 '{novel_id}' 不存在")
        
        # 获取章节摘要
        ch1 = engine.memory.read_chapter(novel_id, 1) or ""
        summary = ch1[:300] if ch1 else ""
        
        titles = generate_titles(engine.client, engine.model, plan, summary)
        return {"ok": True, "titles": titles}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

