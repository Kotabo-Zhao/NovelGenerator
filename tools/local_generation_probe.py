"""本地生成质量探针。

用途：用当前真实模型生成一章内存样本，验证章节契约是否真正进入正文。
默认只输出评测结果，不写入 novels/，避免调试污染用户作品。

示例：
    python -X utf8 tools/local_generation_probe.py --candidates 2
"""
import argparse
import json
import sys
from pathlib import Path

from openai import OpenAI

# 直接以 tools/脚本运行时，Python 默认只把 tools/放进 sys.path。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import config
from backend.core.atomic_writer import AtomicWriter
from backend.core.beat_decomposer import BeatDecomposer
from backend.core.evaluation_system import evaluate_chapter


def build_probe_outline() -> dict:
    return {
        "title": "旧码头的名单",
        "genre": "悬疑",
        "summary": "沈砚调查被替换的封条，拿到失踪案名单后决定赴旧码头见匿名证人",
        "hook": "匿名证人知道沈砚父亲失踪前的最后一个名字",
        "characters": ["沈砚", "周岚"],
        "scene_beats": [
            {
                "name": "发现假封条",
                "key_action": "沈砚撬开档案柜，发现封条编号被替换",
                "opposition": "保安突然回到档案室",
                "reject_outcome": "交出钥匙就会让真正的证据被销毁",
                "entry_hook": "门外传来停顿的脚步声",
                "state_output": {"flags": ["发现假封条"], "relations": {}},
            },
            {
                "name": "留下证据",
                "key_action": "沈砚利用打印机卡纸拍下真正编号并藏走名单",
                "opposition": "周岚要求他立即离开并交出证物",
                "reject_outcome": "继续调查会暴露两人的身份",
                "entry_hook": "周岚从暗门里出现",
                "state_output": {"flags": ["拿到名单"], "relations": {"周岚": -1}},
            },
            {
                "name": "被迫选择",
                "key_action": "沈砚拒绝销毁名单，决定接受匿名账号的旧码头邀约",
                "opposition": "匿名账号发来倒计时和父亲的旧照片",
                "reject_outcome": "拒绝赴约就永远失去唯一证人",
                "entry_hook": "手机屏幕亮起倒计时",
                "state_output": {"flags": ["决定赴约"], "relations": {}},
            },
            {
                "name": "离开档案室",
                "key_action": "沈砚带着名单从后门离开，发现有人跟踪",
                "opposition": "跟踪者没有露面，只留下父亲的旧打火机",
                "reject_outcome": "回头追查会错过约定时间",
                "entry_hook": "后门外停着一辆没有车牌的车",
                "state_output": {"flags": ["遭人跟踪"], "relations": {}},
            },
            {
                "name": "章末钩子",
                "key_action": "沈砚在名单上看见父亲失踪前写下的最后一个名字",
                "opposition": "这个名字正是他以为已经死去的周岚",
                "reject_outcome": "明晚之前必须确认周岚是否在说谎",
                "entry_hook": "名单背面渗出一行新墨迹",
                "reader_question": "周岚究竟是证人还是幕后人",
                "state_output": {"flags": ["发现父亲最后名字"], "relations": {"周岚": -2}},
            },
        ],
    }


def run_probe(candidates: int = 2, max_beats: int = 0) -> dict:
    outline = build_probe_outline()
    client = OpenAI(api_key=config.DEEPSEEK_API_KEY, base_url=config.DEEPSEEK_BASE_URL)
    writer = AtomicWriter(client, config.DEEPSEEK_MODEL)
    beats = BeatDecomposer(seed=812).decompose(
        outline, 1, is_first_chapter=True, available_characters=outline["characters"]
    )
    if max_beats > 0:
        beats = beats[:max_beats]

    previous = ""
    parts = []
    selections = []
    for beat in beats:
        result = writer.write_beat(
            beat,
            prev_full_text=previous,
            char_snapshots="沈砚：29岁，档案修复师，父亲失踪；周岚：调查组成员，与沈砚互不完全信任。",
            blueprint=outline["summary"],
            chapter_context=json.dumps(outline, ensure_ascii=False),
            num_candidates=candidates,
        )
        if result.get("failed"):
            raise RuntimeError(result.get("error") or "beat generation failed")
        parts.append(result["text"])
        previous = (previous + "\n\n" + result["text"]).strip()
        selections.append({
            "beat": beat.index + 1,
            "function": beat.function,
            "event": beat.key_event,
            "selected": result.get("selected_index"),
            "selected_score": result.get("selected_score", 0),
            "chars": len(result["text"]),
        })

    text = "\n\n".join(parts)
    report = evaluate_chapter(text, chapter_outline=outline, genre=outline["genre"])
    return {
        "model": config.DEEPSEEK_MODEL,
        "chars": len(text),
        "selections": selections,
        "report": report,
        "head": text[:500],
        "tail": text[-500:],
    }


def evaluate_existing(root: Path) -> None:
    paths = sorted(root.glob("*/chapters/chapter_*.md"))
    if not paths:
        print(f"未找到章节: {root}")
        return
    for path in paths:
        report = evaluate_chapter(path.read_text(encoding="utf-8"))
        structure = report["structure"]
        print(
            f"{path.parent.parent.name}\t{path.stem}\t"
            f"overall={report['overall_score']}\tstructure={structure['score']}\t"
            f"hook={report['hook']['strength']}\tissues={'；'.join(structure['issues'][:2])}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="NovelGenerator 本地生成质量探针")
    parser.add_argument("--real", action="store_true", help="调用配置的真实模型生成内存样本")
    parser.add_argument("--candidates", type=int, default=2, choices=range(1, 4))
    parser.add_argument("--max-beats", type=int, default=0, help="只生成前N个节拍，0表示完整探针")
    parser.add_argument("--existing", action="store_true", help="评测 novels/ 下已有章节")
    args = parser.parse_args()

    if args.existing:
        evaluate_existing(Path(config.NOVELS_DIR))
    elif args.real:
        print(json.dumps(run_probe(args.candidates, args.max_beats), ensure_ascii=False, indent=2))
    else:
        parser.error("请指定 --real 或 --existing")


if __name__ == "__main__":
    main()
