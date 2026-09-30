"""세컨드브레인 미리알림 어댑터 (v0.28). 표준 라이브러리만 사용, 읽기 전용.

맥 미리알림(Reminders.app)을 JXA(osascript -l JavaScript)로 읽는다. EventKit 캘린더(agenda.py)와
같은 패턴: 최초 1회 권한 허용, 권한이 없으면 안내만 남기고 빈 목록, 짧은 캐시로 반복 호출을 줄인다.

쓰기는 하지 않는다 — 완료 체크·수정은 미리알림 앱에서 직접 한다.

설정(~/.config/second-brain/config.json):
  "reminders": {"enabled": false, "lists": ["장보기", "회사"]}
기본은 꺼짐(권한 창이 갑자기 뜨지 않도록, EventKit 캘린더와 같은 이유). lists를 비우면 모든 목록.

미리알림 스키마: {id, title, list, due, due_time, priority, notes, completed, url}
  due는 "YYYY-MM-DD" 또는 None, due_time은 "HH:MM" 또는 None(시간 없는 마감).

환경변수:
  SECOND_BRAIN_REMINDERS_CMD - osascript 호출을 대체할 명령(테스트용, 고정 JSON을 출력하는 가짜 스크립트)
  SECOND_BRAIN_OFFLINE=1     - osascript를 부르지 않고 캐시만 사용
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time

import agenda as agenda_mod  # noqa: E402 - cache_dir()를 같은 폴더 모듈에서 공유

CACHE_SEC = 5 * 60
TIMEOUT_SEC = 20
MAX_REMINDERS = 200
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

PERMISSION_HINT = "시스템 설정 → 개인정보 보호 → 미리알림에서 터미널/파이썬 허용"

LAST_ERROR = None  # 마지막 조회 실패 사유(성공하면 None). 호출자가 CLI/대시보드에 보여준다.

# 미리알림 앱을 JXA로 읽는다. 목록별로 완료되지 않은 항목만 가져오고(느린 dueDate() 접근을
# 줄이기 위해 항목당 try/catch, 전체 200개 상한), 실패한 항목 하나가 전체를 죽이지 않게 한다.
REMINDERS_JXA = r"""
function run(argv) {
  var wanted = JSON.parse(argv[0] || "[]");
  var includeCompleted = argv[1] === "true";
  var MAX = 200;
  var app = Application("Reminders");
  var lists;
  try {
    lists = app.lists();
  } catch (e) {
    return JSON.stringify({error: "permission", message: String(e)});
  }
  var out = [];
  for (var i = 0; i < lists.length && out.length < MAX; i++) {
    var lst = lists[i];
    var lname;
    try { lname = lst.name(); } catch (e) { continue; }
    if (wanted.length && wanted.indexOf(lname) < 0) continue;
    var items;
    try {
      items = includeCompleted ? lst.reminders() : lst.reminders.whose({completed: false})();
    } catch (e) { continue; }
    for (var j = 0; j < items.length && out.length < MAX; j++) {
      var r = items[j];
      try {
        var due = null, dueTime = null;
        try {
          var dd = r.dueDate();
          if (dd) {
            var y = dd.getFullYear(), mo = dd.getMonth() + 1, da = dd.getDate();
            due = y + "-" + (mo < 10 ? "0" + mo : String(mo)) + "-" + (da < 10 ? "0" + da : String(da));
            var hh = dd.getHours(), mi = dd.getMinutes();
            if (hh !== 0 || mi !== 0) dueTime = (hh < 10 ? "0" + hh : String(hh)) + ":" + (mi < 10 ? "0" + mi : String(mi));
          }
        } catch (e2) {}
        var pr = 0;
        try { pr = r.priority() || 0; } catch (e3) {}
        var notes = "";
        try { notes = r.body() || ""; } catch (e4) {}
        out.push({
          id: String(r.id()), title: r.name() || "", list: lname,
          due: due, due_time: dueTime, priority: pr, notes: notes.slice(0, 200),
          completed: !!r.completed(), url: ""
        });
      } catch (e5) { continue; }
    }
  }
  return JSON.stringify({reminders: out});
}
"""


def cache_path():
    return agenda_mod.cache_dir() / "reminders.json"


def _read_cache():
    cp = cache_path()
    if not cp.is_file():
        return None
    try:
        data = json.loads(cp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, list) else None


def _write_cache(items):
    try:
        cache_path().write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _filter(items, lists, include_completed):
    wanted = set(lists) if lists else None
    out = []
    for it in items:
        if not include_completed and it.get("completed"):
            continue
        if wanted is not None and it.get("list") not in wanted:
            continue
        out.append(it)
    return out


def fetch_reminders(lists=None, include_completed=False, timeout=TIMEOUT_SEC, force=False):
    """미리알림 목록을 읽는다. 5분 캐시(force=True면 무시), 실패하면 []에 LAST_ERROR."""
    global LAST_ERROR
    LAST_ERROR = None
    cp = cache_path()
    fresh = cp.is_file() and (time.time() - cp.stat().st_mtime) < CACHE_SEC
    if (not force and fresh) or os.environ.get("SECOND_BRAIN_OFFLINE") == "1":
        cached = _read_cache()
        if cached is not None:
            return _filter(cached, lists, include_completed)
        if os.environ.get("SECOND_BRAIN_OFFLINE") == "1":
            LAST_ERROR = "오프라인 모드(SECOND_BRAIN_OFFLINE=1)라 캐시만 사용해요. 아직 캐시가 없어요."
        return []
    wanted_json = json.dumps(list(lists or []), ensure_ascii=False)
    include_arg = "true" if include_completed else "false"
    override = os.environ.get("SECOND_BRAIN_REMINDERS_CMD")
    if override:
        args = shlex.split(override) + [wanted_json, include_arg]
    else:
        args = ["osascript", "-l", "JavaScript", "-e", REMINDERS_JXA, wanted_json, include_arg]
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        LAST_ERROR = f"미리알림 조회 시간 초과({timeout}초)"
        return []
    except OSError as e:
        LAST_ERROR = f"미리알림 실행 실패: {e}"
        return []
    if r.returncode != 0:
        text = (r.stderr or r.stdout or "").strip()
        if "-1743" in text:
            LAST_ERROR = f"미리알림 접근 권한이 없어요(-1743). {PERMISSION_HINT}"
        else:
            LAST_ERROR = text[:300] or "osascript 실패"
        return []
    try:
        data = json.loads(r.stdout.strip() or "{}")
    except ValueError:
        LAST_ERROR = "미리알림 응답을 해석할 수 없어요"
        return []
    if not isinstance(data, dict):
        data = {}
    if data.get("error"):
        text = json.dumps(data, ensure_ascii=False)
        if data.get("error") == "permission" or "-1743" in text:
            LAST_ERROR = f"미리알림 접근 권한이 없어요. {PERMISSION_HINT}"
        else:
            LAST_ERROR = str(data.get("message") or data.get("error"))[:300]
        return []
    items = []
    for it in (data.get("reminders") or [])[:MAX_REMINDERS]:
        if not isinstance(it, dict):
            continue
        due = str(it.get("due") or "").strip() or None
        if due and not DATE_RE.match(due):
            due = None
        due_time = str(it.get("due_time") or "").strip() or None
        try:
            priority = int(it.get("priority") or 0)
        except (TypeError, ValueError):
            priority = 0
        items.append({
            "id": str(it.get("id") or ""),
            "title": str(it.get("title") or "").strip() or "(제목 없음)",
            "list": str(it.get("list") or ""),
            "due": due,
            "due_time": due_time,
            "priority": priority,
            "notes": str(it.get("notes") or "")[:200],
            "completed": bool(it.get("completed")),
            "url": str(it.get("url") or ""),
        })
    _write_cache(items)
    return _filter(items, lists, include_completed)
