"""동선(steps)·출발 알림: 파서, 일정 붙이기, step 쓰기(여러 날), due_reminders, remind 중복 방지."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import agenda  # noqa: E402
import brain  # noqa: E402

KST = ZoneInfo("Asia/Seoul")
BODY = """# 제주도

## 준비
- [ ] 렌터카

## 동선
- 14:00 집 출발 (자가용, 50분)
- 16:25 KE1355 김포 출발 (85분)
### 10-04
- 09:30 성산일출봉 (2시간)
- 13:00 점심 해녀의 집
### 2026-10-05
- 11:00 공항 이동 (40분)

## 메모
- 2026-09-30 12:00  주차 P1
"""


class StepsTest(unittest.TestCase):
    def test_parse_steps(self):
        st = brain.parse_steps(BODY, "2026-10-03")
        self.assertEqual([(s["day"], s["time"], s["minutes"]) for s in st],
                         [("2026-10-03", "14:00", 50), ("2026-10-03", "16:25", 85), ("2026-10-04", "09:30", 30), ("2026-10-04", "13:00", 30), ("2026-10-05", "11:00", 40)])
        self.assertEqual(st[0]["text"], "집 출발 (자가용, 50분)")
        self.assertEqual(brain.parse_steps("# x\n\n## 메모\n- 14:00 이건 메모", "2026-10-03"), [])


class PlanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._old = {k: os.environ.get(k) for k in ("HOME", "XDG_CACHE_HOME", "SECOND_BRAIN_VAULT", "SECOND_BRAIN_OFFLINE")}
        os.environ["HOME"] = str(self.home)
        os.environ["XDG_CACHE_HOME"] = str(self.home / ".cache")
        os.environ.pop("SECOND_BRAIN_VAULT", None)
        os.environ["SECOND_BRAIN_OFFLINE"] = "1"  # 동선 테스트는 날씨와 무관 - 실제 네트워크 금지
        ics = ("BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:j1\nDTSTART;VALUE=DATE:20261003\nDTEND;VALUE=DATE:20261004\nSUMMARY:제주도\nEND:VEVENT\n"
               "BEGIN:VEVENT\nUID:m1\nDTSTART;TZID=Asia/Seoul:20261003T150000\nDTEND;TZID=Asia/Seoul:20261003T160000\nSUMMARY:치과\nLOCATION:강남\nEND:VEVENT\nEND:VCALENDAR\n")
        (self.home / "cal.ics").write_text(ics, encoding="utf-8")
        cfgdir = self.home / ".config" / "second-brain"
        cfgdir.mkdir(parents=True)
        self.vault = self.home / "brain"
        fake = self.home / "kakao.sh"
        fake.write_text("#!/bin/sh\necho \"$1\" >> \"$HOME/sent.txt\"\n", encoding="utf-8")
        fake.chmod(0o755)
        (cfgdir / "config.json").write_text(json.dumps({"vault": str(self.vault), "kakao_cmd": str(fake),
                                                         "calendar": {"sources": [{"kind": "ics", "name": "t", "path": str(self.home / "cal.ics")}]}}), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            brain.main(["init", "--vault", str(self.vault)])

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def test_step_write_and_attach(self):
        key = "2026-10-03|제주도"
        brain.event_note_action(self.vault, {"action": "step", "key": key, "text": "14:00 집 출발 (자가용 50분)", "end": "2026-10-05"})
        brain.event_note_action(self.vault, {"action": "step", "key": key, "text": "16:25 KE1355 김포 출발 (85분)"})
        r = brain.event_note_action(self.vault, {"action": "step", "key": key, "text": "09:30 성산일출봉 (120분)", "day": "2026-10-04"})
        self.assertEqual([(s["day"], s["time"]) for s in r["note"]["steps"]], [("2026-10-03", "14:00"), ("2026-10-03", "16:25"), ("2026-10-04", "09:30")])
        text = (self.vault / r["note"]["path"]).read_text(encoding="utf-8")
        self.assertIn("## 동선\n- 14:00 집 출발 (자가용 50분)\n- 16:25 KE1355 김포 출발 (85분)\n### 10-04\n- 09:30 성산일출봉 (120분)", text)
        with self.assertRaises(brain.BrainError):
            brain.event_note_action(self.vault, {"action": "step", "key": key, "text": "출발 시간 없음"})
        now = datetime(2026, 10, 3, 13, 55, tzinfo=KST)
        ag = agenda.collect_agenda(brain.load_config(), now=now, days=3)
        brain.attach_event_notes(self.vault, ag)
        self.assertEqual([s["time"] for s in ag["steps_today"]], ["14:00", "16:25"])
        self.assertEqual(ag["steps_today"][0]["event_title"], "제주도")
        self.assertEqual([s["day"] for s in ag["steps_upcoming"]], ["2026-10-04"])
        due = brain.due_reminders(ag, now)
        ids = [d["id"].split("|")[0] for d in due]
        self.assertEqual(ids, ["step"])  # 14:00 단계는 5분 뒤 → 알림, 치과 15:00은 65분 뒤 → 아직
        due2 = brain.due_reminders(ag, datetime(2026, 10, 3, 14, 40, tzinfo=KST))
        self.assertEqual([d["id"].split("|")[0] for d in due2], ["event"])  # 치과 20분 전

    def test_cli_step_and_remind_dedupe(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(brain.main(["event", "step", "2026-10-03|제주도", "14:00 집 출발 (50분)"]), 0)
        # remind: 지금은 일정이 없으니 '알릴 것 없음'
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(brain.main(["remind", "--kakao"]), 0)
        self.assertIn("알릴 것 없음", buf.getvalue())
        # due_reminders 직접 + 상태 파일 중복 방지 검증
        now = datetime(2026, 10, 3, 13, 55, tzinfo=KST)
        ag = agenda.collect_agenda(brain.load_config(), now=now, days=2)
        brain.attach_event_notes(self.vault, ag)
        due = brain.due_reminders(ag, now)
        self.assertEqual(len(due), 1)
        sp = brain._remind_state_path()
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text(json.dumps({due[0]["id"]: now.isoformat(timespec="minutes")}), encoding="utf-8")
        state = json.loads(sp.read_text(encoding="utf-8"))
        self.assertIn(due[0]["id"], state)
        self.assertFalse((self.home / "sent.txt").exists())


if __name__ == "__main__":
    unittest.main()
