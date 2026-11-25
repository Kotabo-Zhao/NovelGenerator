import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from core.roleplay.anti_slop import ROLEPLAY_HUMAN_VOICE_RULES, detect_recent_rp_cliches
from core.roleplay.prompt import build_messages, build_system_prompt


class TestRoleplayAndUIPolish(unittest.TestCase):
    def test_roleplay_prompt_includes_human_voice_and_anti_impersonation_rules(self):
        card = {"name": "苏晚", "description": "冷静的茶馆老板娘"}
        settings = {"user_name": "林川"}
        sys_prompt = build_system_prompt(card, settings)
        self.assertIn("自然人声准则", sys_prompt)
        self.assertIn("严禁替 林川 说话", sys_prompt)

    def test_roleplay_rolling_anti_cliche_triggers_on_recent_assistant_turns(self):
        history = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "她嘴角勾起一抹浅笑，眼中闪过一丝戏谑，缓缓开口道。"},
            {"role": "user", "content": "继续说"},
        ]
        note = detect_recent_rp_cliches(history)
        self.assertIn("嘴角勾起一抹", note)
        self.assertIn("眼中闪过一丝", note)
        msgs = build_messages({"name": "苏晚"}, {"user_name": "林川"}, history)
        self.assertTrue(any("[防复读提醒]" in m["content"] for m in msgs if m["role"] == "system"))

    def test_frontend_removed_dead_xiaohongshu_ui_and_fixed_legibility_floor(self):
        idx = (ROOT / "web/index.html").read_text(encoding="utf-8")
        self.assertNotIn("小红书爆款短篇", idx)
        self.assertNotIn("xhsMode", idx)
        self.assertIn("短剧化快餐模式", idx)

        for css_name in ("workshop.css", "roleplay.css", "scene.css", "cards.css", "tools.css"):
            css = (ROOT / "web/assets" / css_name).read_text(encoding="utf-8")
            tiny = re.findall(r"font-size\s*:\s*([0-9]|10)px", css)
            self.assertEqual(tiny, [], f"{css_name} still has <=10px font sizes: {tiny}")
            self.assertNotIn("100vh", css, f"{css_name} should use 100dvh instead of 100vh")


if __name__ == "__main__":
    unittest.main()
