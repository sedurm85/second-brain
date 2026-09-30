"""세컨드브레인 일정 어댑터 (v0.6, 2차 1단계 「시간」).

표준 라이브러리만 사용. 두 종류의 소스를 읽어 오늘·이번 주 일정을 한 목록으로 만든다.

- ics: iCalendar(.ics) 파일 또는 URL. 구글 캘린더의 "iCal 형식의 비공개 주소"를 파일에 저장해 두고
       읽는 용도. URL은 15분 캐시(~/.cache/second-brain/), 실패 시 마지막 캐시를 stale로 표시.
- eventkit: 맥 캘린더 앱에 붙어 있는 계정을 JXA(osascript -l JavaScript)로 읽는다. 최초 1회 권한 허용.

설정(~/.config/second-brain/config.json):
  "calendar": {"sources": [
      {"kind": "ics", "name": "구글", "url_file": "~/.config/second-brain/google.ics.url"},
      {"kind": "ics", "name": "가족", "path": "~/Downloads/family.ics"},
      {"kind": "eventkit", "name": "맥 캘린더", "calendars": ["집", "회사"]}
  ]}
소스가 없으면 eventkit 하나를 기본으로 시도한다(권한이 없으면 안내만 남기고 빈 목록).

일정 스키마: {id, title, start, end, all_day, location, calendar, source}
  start/end는 로컬 시간 ISO 문자열(all_day면 날짜만). 종료는 exclusive.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import urllib.request
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ICS_CACHE_SEC = 15 * 60
ICS_TIMEOUT_SEC = 20
EVENTKIT_TIMEOUT_SEC = 25
UNCONFIGURED_HINT = ("구글 캘린더만 쓰면: 구글 캘린더 설정에서 'iCal 형식의 비공개 주소'를 복사해 "
                     "~/.config/second-brain/google.ics.url 파일 첫 줄에 저장한 뒤 "
                     "`brain.py calendar add ics 구글 --url-file ~/.config/second-brain/google.ics.url`. "
                     "맥 캘린더 앱에 계정이 붙어 있으면 `brain.py calendar add eventkit 맥`.")
MAX_RRULE_INSTANCES = 400
WEEKDAY_CODES = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


def local_tz():
    try:
        return datetime.now().astimezone().tzinfo or ZoneInfo("Asia/Seoul")
    except Exception:  # noqa: BLE001 - 시스템 시간대를 못 읽으면 서울
        return ZoneInfo("Asia/Seoul")


def cache_dir():
    d = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")) / "second-brain"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ───────────────────────── ICS 파서 ─────────────────────────

def _unfold(text: str):
    """RFC 5545 줄 접힘 해제(다음 줄이 공백/탭으로 시작하면 이어붙임)."""
    lines = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _split_prop(line: str):
    """'DTSTART;TZID=Asia/Seoul:20260930T100000' → ('DTSTART', {'TZID': 'Asia/Seoul'}, '2026...')."""
    if ":" not in line:
        return None
    # 파라미터 값 안의 ':'(예: mailto:)를 피하려면 첫 ':'가 아니라 따옴표 밖의 첫 ':'를 찾는다
    quoted = False
    for i, ch in enumerate(line):
        if ch == '"':
            quoted = not quoted
        elif ch == ":" and not quoted:
            head, value = line[:i], line[i + 1:]
            break
    else:
        return None
    parts = head.split(";")
    name = parts[0].upper()
    params = {}
    for pr in parts[1:]:
        if "=" in pr:
            k, v = pr.split("=", 1)
            params[k.upper()] = v.strip('"')
    return name, params, value


def _unescape(s: str):
    return (s.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",")
            .replace("\\;", ";").replace("\\\\", "\\"))


def _parse_dt(value: str, params: dict, tz):
    """DATE 또는 DATE-TIME → (datetime|date, all_day). 시간대는 TZID > Z(UTC) > 로컬."""
    value = value.strip()
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
        return date(int(value[:4]), int(value[4:6]), int(value[6:8])), True
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})?(Z?)", value)
    if not m:
        raise ValueError(f"날짜 형식 아님: {value}")
    y, mo, d, hh, mm, ss, z = m.groups()
    naive = datetime(int(y), int(mo), int(d), int(hh), int(mm), int(ss or 0))
    if z == "Z":
        dt = naive.replace(tzinfo=ZoneInfo("UTC"))
    elif params.get("TZID"):
        try:
            dt = naive.replace(tzinfo=ZoneInfo(params["TZID"]))
        except Exception:  # noqa: BLE001 - 모르는 TZID는 로컬로
            dt = naive.replace(tzinfo=tz)
    else:
        dt = naive.replace(tzinfo=tz)
    return dt.astimezone(tz), False


def _parse_rrule(value: str):
    out = {}
    for part in value.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.upper()] = v
    return out


def _expand_rrule(start, rrule: dict, window_start, window_end, exdates: set, all_day: bool, tz):
    """RRULE 확장(FREQ DAILY/WEEKLY/MONTHLY/YEARLY, INTERVAL, COUNT, UNTIL, BYDAY(주간)). 창 안의 시작 시각만 돌려준다."""
    freq = rrule.get("FREQ", "").upper()
    if freq not in ("DAILY", "WEEKLY", "MONTHLY", "YEARLY"):
        return [start] if window_start <= start < window_end else []
    interval = max(1, int(rrule.get("INTERVAL", "1") or 1))
    count = int(rrule["COUNT"]) if rrule.get("COUNT") else None
    until = None
    if rrule.get("UNTIL"):
        u, u_all = _parse_dt(rrule["UNTIL"], {}, tz)
        until = (datetime.combine(u, time.max, tzinfo=tz) if u_all else u)
    bydays = [WEEKDAY_CODES[d[-2:]] for d in rrule.get("BYDAY", "").split(",") if d[-2:] in WEEKDAY_CODES]

    def key(dt):
        return dt.date() if all_day else dt

    out, produced = [], 0
    if freq == "WEEKLY" and bydays:
        # 시작 주의 월요일부터 주 단위로, 각 주에서 BYDAY 요일들
        week0 = start - timedelta(days=start.weekday())
        w = 0
        while produced < (count or MAX_RRULE_INSTANCES) and w < 520:
            base = week0 + timedelta(weeks=w * interval)
            for wd in sorted(bydays):
                inst = base + timedelta(days=wd)
                if inst < start:
                    continue
                if until and inst > until:
                    return out
                produced += 1
                if count and produced > count:
                    return out
                if window_start <= inst < window_end and key(inst) not in exdates:
                    out.append(inst)
                if len(out) >= MAX_RRULE_INSTANCES:
                    return out
            if base > window_end:
                break
            w += 1
        return out
    inst, i = start, 0
    while produced < (count or MAX_RRULE_INSTANCES) and i < 5000:
        if until and inst > until:
            break
        produced += 1
        if window_start <= inst < window_end and key(inst) not in exdates:
            out.append(inst)
        if inst >= window_end or len(out) >= MAX_RRULE_INSTANCES:
            break
        i += 1
        if freq == "DAILY":
            inst = start + timedelta(days=i * interval)
        elif freq == "WEEKLY":
            inst = start + timedelta(weeks=i * interval)
        elif freq == "MONTHLY":
            mo = start.month - 1 + i * interval
            y, m = start.year + mo // 12, mo % 12 + 1
            try:
                inst = start.replace(year=y, month=m)
            except ValueError:  # 31일 → 없는 달은 건너뜀
                continue
        else:
            try:
                inst = start.replace(year=start.year + i * interval)
            except ValueError:
                continue
    return out


def parse_ics(text: str, window_start: datetime, window_end: datetime, source: str = "ics", calendar: str | None = None, tz=None):
    """ICS 본문 → 창(window_start ≤ start < window_end) 안의 일정 목록(반복 확장, EXDATE·RECURRENCE-ID 반영)."""
    tz = tz or local_tz()
    lines = _unfold(text)
    cal_name = calendar
    events, overrides = [], set()
    cur = None
    for line in lines:
        if line == "BEGIN:VEVENT":
            cur = {"exdates": set()}
            continue
        if line == "END:VEVENT" and cur is not None:
            events.append(cur)
            cur = None
            continue
        pr = _split_prop(line)
        if not pr:
            continue
        name, params, value = pr
        if cur is None:
            if name == "X-WR-CALNAME" and not cal_name:
                cal_name = _unescape(value)
            continue
        if name == "DTSTART":
            cur["start"], cur["all_day"] = _parse_dt(value, params, tz)
        elif name == "DTEND":
            cur["end"], _ = _parse_dt(value, params, tz)
        elif name == "DURATION":
            cur["duration"] = value
        elif name == "SUMMARY":
            cur["title"] = _unescape(value)
        elif name == "LOCATION":
            cur["location"] = _unescape(value)
        elif name == "UID":
            cur["uid"] = value
        elif name == "RRULE":
            cur["rrule"] = _parse_rrule(value)
        elif name == "EXDATE":
            for v in value.split(","):
                try:
                    d, a = _parse_dt(v, params, tz)
                    cur["exdates"].add(d if a else d)
                except ValueError:
                    pass
        elif name == "RECURRENCE-ID":
            try:
                cur["recurrence_id"], _ = _parse_dt(value, params, tz)
            except ValueError:
                pass
        elif name == "DESCRIPTION":
            cur["description"] = _unescape(value)
        elif name == "URL":
            cur["url"] = value.strip()
        elif name == "ATTENDEE":
            cur.setdefault("attendees", []).append(params.get("CN") or value.replace("mailto:", "").replace("MAILTO:", ""))
        elif name == "STATUS":
            cur["status"] = value.upper()
        elif name == "TRANSP":
            cur["transp"] = value.upper()
    # RECURRENCE-ID가 있는 항목은 원본 반복의 해당 회차를 대체
    for ev in events:
        if ev.get("recurrence_id") and ev.get("uid"):
            overrides.add((ev["uid"], ev["recurrence_id"]))

    out = []
    for ev in events:
        if "start" not in ev or ev.get("status") == "CANCELLED":
            continue
        all_day = ev.get("all_day", False)
        start = ev["start"]
        if all_day:
            start_dt = datetime.combine(start, time.min, tzinfo=tz)
            end_d = ev.get("end") or (start + timedelta(days=1))
            dur = timedelta(days=max(1, (end_d - start).days)) if isinstance(end_d, date) else timedelta(days=1)
        else:
            start_dt = start
            end = ev.get("end")
            dur = (end - start) if isinstance(end, datetime) else _parse_duration(ev.get("duration")) or timedelta(hours=1)
        exd = {(x if isinstance(x, date) and not isinstance(x, datetime) else x) for x in ev.get("exdates", set())}
        if ev.get("rrule") and not ev.get("recurrence_id"):
            starts = _expand_rrule(start_dt, ev["rrule"], window_start - dur, window_end, exd, all_day, tz)
        else:
            starts = [start_dt] if (window_start - dur) <= start_dt < window_end else []
        for st in starts:
            if ev.get("rrule") and (ev.get("uid"), st) in overrides:
                continue
            en = st + dur
            uid_base = f"{ev.get('uid') or ev.get('title', '')}|{st.isoformat()}"
            out.append({
                "id": hashlib.sha1(uid_base.encode("utf-8")).hexdigest()[:12],
                "title": ev.get("title") or "(제목 없음)",
                "start": st.date().isoformat() if all_day else st.isoformat(timespec="minutes"),
                "end": en.date().isoformat() if all_day else en.isoformat(timespec="minutes"),
                "all_day": all_day,
                "location": ev.get("location") or "",
                "calendar": cal_name or source,
                "source": source,
                "busy": ev.get("transp") != "TRANSPARENT",
                "description": (ev.get("description") or "").strip()[:800],
                "url": ev.get("url") or "",
                "attendees": (ev.get("attendees") or [])[:12],
                "_start": st, "_end": en,
            })
    return out


def _parse_duration(value):
    if not value:
        return None
    m = re.fullmatch(r"-?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", value)
    if not m:
        return None
    w, d, h, mi, s = (int(x or 0) for x in m.groups())
    return timedelta(weeks=w, days=d, hours=h, minutes=mi, seconds=s)


# ───────────────────────── 소스 읽기 ─────────────────────────

def _read_url_file(p: str):
    path = Path(os.path.expanduser(p))
    if not path.is_file():
        raise FileNotFoundError(f"주소 파일 없음: {path}")
    url = path.read_text(encoding="utf-8").strip().splitlines()
    url = next((u.strip() for u in url if u.strip() and not u.startswith("#")), "")
    if not url.startswith(("http://", "https://", "webcal://")):
        raise ValueError("주소 파일 첫 줄이 http(s)://로 시작해야 합니다")
    return url.replace("webcal://", "https://", 1)


def fetch_ics(src: dict, now: datetime):
    """ICS 소스 본문과 상태. URL은 캐시(15분), 실패 시 캐시 폴백(stale)."""
    if src.get("path"):
        p = Path(os.path.expanduser(src["path"]))
        return p.read_text(encoding="utf-8"), "ok", None
    url = _read_url_file(src["url_file"]) if src.get("url_file") else src.get("url")
    if not url:
        raise ValueError("ics 소스에 path/url_file/url 중 하나가 필요합니다")
    key = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    cache = cache_dir() / f"ics-{key}.ics"
    if cache.is_file() and now.timestamp() - cache.stat().st_mtime < ICS_CACHE_SEC:
        return cache.read_text(encoding="utf-8"), "ok", None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "second-brain-agenda/0.6"})
        with urllib.request.urlopen(req, timeout=ICS_TIMEOUT_SEC) as r:
            body = r.read().decode("utf-8", "replace")
        if "BEGIN:VCALENDAR" not in body[:2000]:
            raise ValueError("응답이 iCalendar가 아닙니다(주소가 비공개 ICS 주소인지 확인)")
        cache.write_text(body, encoding="utf-8")
        return body, "ok", None
    except Exception as e:  # noqa: BLE001 - 네트워크·형식 오류는 상태로 보고
        if cache.is_file():
            age_min = int((now.timestamp() - cache.stat().st_mtime) // 60)
            return cache.read_text(encoding="utf-8"), "stale", f"{age_min}분 전 캐시 사용 ({type(e).__name__}: {e})"
        raise


EVENTKIT_JXA = r"""
ObjC.import('EventKit'); ObjC.import('Foundation');
function run(argv) {
  var startIso = argv[0], endIso = argv[1], wanted = JSON.parse(argv[2] || "[]");
  var store = $.EKEventStore.alloc.init;
  var status = $.EKEventStore.authorizationStatusForEntityType(0); // 0=event
  var granted = (status === 3 || status === 4); // authorized | fullAccess
  if (!granted) {
    var done = false, ok = false;
    var cb = function (g, err) { ok = g; done = true; };
    if (store.requestFullAccessToEventsWithCompletion) store.requestFullAccessToEventsWithCompletion(cb);
    else store.requestAccessToEntityTypeCompletion(0, cb);
    var deadline = Date.now() + 20000;
    while (!done && Date.now() < deadline) $.NSRunLoop.currentRunLoop.runUntilDate($.NSDate.dateWithTimeIntervalSinceNow(0.1));
    granted = ok;
  }
  if (!granted) return JSON.stringify({error: "permission", status: status});
  var fmt = $.NSISO8601DateFormatter.alloc.init;
  var start = fmt.dateFromString(startIso), end = fmt.dateFromString(endIso);
  var cals = store.calendarsForEntityType(0);
  var use = [], names = [];
  for (var i = 0; i < cals.count; i++) {
    var c = cals.objectAtIndex(i), t = ObjC.unwrap(c.title);
    names.push(t);
    if (!wanted.length || wanted.indexOf(t) >= 0) use.push(c);
  }
  var pred = store.predicateForEventsWithStartDateEndDateCalendars(start, end, $(use));
  var evs = store.eventsMatchingPredicate(pred);
  var out = [];
  fmt.formatOptions = 1907; // 내부 표준 옵션(날짜+시간+시간대)
  for (var j = 0; j < evs.count; j++) {
    var e = evs.objectAtIndex(j);
    out.push({
      id: ObjC.unwrap(e.eventIdentifier), title: ObjC.unwrap(e.title) || "", notes: ObjC.unwrap(e.notes) || "", url: e.URL ? ObjC.unwrap(e.URL.absoluteString) : "",
      start: ObjC.unwrap(fmt.stringFromDate(e.startDate)), end: ObjC.unwrap(fmt.stringFromDate(e.endDate)),
      all_day: !!e.allDay, location: ObjC.unwrap(e.location) || "", calendar: ObjC.unwrap(e.calendar.title) || "",
      busy: e.availability !== 1
    });
  }
  return JSON.stringify({events: out, calendars: names});
}
"""


def fetch_eventkit(src: dict, window_start: datetime, window_end: datetime, tz):
    """맥 캘린더 앱(EventKit) 읽기. 권한 없으면 error='permission'."""
    wanted = json.dumps(src.get("calendars") or [], ensure_ascii=False)
    args = ["osascript", "-l", "JavaScript", "-e", EVENTKIT_JXA,
            window_start.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ"),
            window_end.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ"), wanted]
    r = subprocess.run(args, capture_output=True, text=True, timeout=EVENTKIT_TIMEOUT_SEC)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[:300] or "osascript 실패")
    data = json.loads(r.stdout.strip() or "{}")
    if data.get("error") == "permission":
        raise PermissionError("캘린더 접근 권한이 없어요. 시스템 설정 → 개인정보 보호 및 보안 → 캘린더에서 터미널(또는 Claude/Orca)을 허용해 주세요.")
    out = []
    for e in data.get("events", []):
        try:
            st = datetime.fromisoformat(e["start"].replace("Z", "+00:00")).astimezone(tz)
            en = datetime.fromisoformat(e["end"].replace("Z", "+00:00")).astimezone(tz)
        except (KeyError, ValueError):
            continue
        all_day = bool(e.get("all_day"))
        if all_day:
            en = en if en > st else st + timedelta(days=1)
        out.append({
            "id": hashlib.sha1(f"{e.get('id')}|{st.isoformat()}".encode("utf-8")).hexdigest()[:12],
            "title": e.get("title") or "(제목 없음)",
            "start": st.date().isoformat() if all_day else st.isoformat(timespec="minutes"),
            "end": en.date().isoformat() if all_day else en.isoformat(timespec="minutes"),
            "all_day": all_day, "location": e.get("location") or "",
            "calendar": e.get("calendar") or src.get("name") or "맥 캘린더", "source": src.get("name") or "eventkit",
            "busy": bool(e.get("busy", True)), "description": (e.get("notes") or "")[:800], "url": e.get("url") or "", "attendees": [],
            "_start": st, "_end": en,
        })
    return out, data.get("calendars", [])


# ───────────────────────── 수집·요약 ─────────────────────────

def normalize_sources(cfg: dict):
    cal = cfg.get("calendar") if isinstance(cfg, dict) else None
    srcs = (cal or {}).get("sources") if isinstance(cal, dict) else None
    if not srcs:
        return []  # 설정이 없으면 아무 것도 시도하지 않는다(EventKit 권한 창이 갑자기 뜨지 않도록). collect_agenda가 안내를 남긴다
    out = []
    for i, s in enumerate(srcs):
        if not isinstance(s, dict) or s.get("kind") not in ("ics", "eventkit"):
            continue
        s = dict(s)
        s.setdefault("name", f"{s['kind']}-{i + 1}")
        out.append(s)
    return out


def collect_agenda(cfg: dict, now: datetime | None = None, days: int = 7):
    """오늘부터 days일의 일정. 반환: {today, upcoming, sources, now, generated_at, next}."""
    tz = local_tz()
    now = (now or datetime.now(tz)).astimezone(tz)
    day0 = datetime.combine(now.date(), time.min, tzinfo=tz)
    window_end = day0 + timedelta(days=max(1, days))
    events, sources = [], []
    srcs = normalize_sources(cfg)
    if not srcs:
        sources.append({"name": "캘린더", "kind": "-", "status": "unconfigured", "count": 0, "error": "연결된 캘린더가 없어요.",
                        "hint": UNCONFIGURED_HINT})
    for src in srcs:
        rec = {"name": src["name"], "kind": src["kind"], "status": "ok", "count": 0, "error": None}
        try:
            if src["kind"] == "ics":
                body, status, note = fetch_ics(src, now)
                evs = parse_ics(body, day0, window_end, source=src["name"], calendar=src.get("calendar"), tz=tz)
                rec["status"], rec["error"] = status, note
            else:
                evs, cal_names = fetch_eventkit(src, day0, window_end, tz)
                rec["calendars"] = cal_names
            rec["count"] = len(evs)
            events.extend(evs)
        except PermissionError as e:
            rec.update(status="permission", error=str(e))
            if src.get("_default"):
                rec["hint"] = "구글 캘린더만 쓰면 `brain.py calendar add ics 구글 --url-file ~/.config/second-brain/google.ics.url`로 비공개 ICS 주소를 등록하는 방법도 있어요."
        except FileNotFoundError as e:
            rec.update(status="missing", error=str(e))
        except Exception as e:  # noqa: BLE001 - 소스 하나가 죽어도 나머지는 보여준다
            rec.update(status="fail", error=f"{type(e).__name__}: {e}")
        sources.append(rec)
    # 중복 제거(같은 제목·시작이 두 소스에 있으면 하나만)
    seen, uniq = set(), []
    for e in sorted(events, key=lambda x: (x["_start"], not x["all_day"], x["title"])):
        k = (e["title"], e["start"], e["end"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(e)
    for e in uniq:
        e["key"] = event_key(e["start"][:10], e["title"])
        e["days_left"] = (e["_start"].date() - now.date()).days
    today_end = day0 + timedelta(days=1)
    today = [e for e in uniq if e["_start"] < today_end and e["_end"] > day0]
    upcoming = [e for e in uniq if e["_start"] >= today_end]
    nxt = next((e for e in today if not e["all_day"] and e["_start"] > now), None)
    cur = [e for e in today if not e["all_day"] and e["_start"] <= now < e["_end"]]
    conflicts = _conflicts(today)
    gaps = _tight_gaps(today)
    by_day = {}
    for e in upcoming:
        by_day.setdefault(e["_start"].date(), []).append(e)
    for day in sorted(by_day):
        for g in _tight_gaps(by_day[day]):
            g["day"] = day.isoformat()
            gaps.append(g)
    def strip(e):
        return {k: v for k, v in e.items() if not k.startswith("_")}
    return {
        "now": now.isoformat(timespec="minutes"),
        "generated_at": now.isoformat(timespec="seconds"),
        "days": days,
        "today": [strip(e) for e in today],
        "upcoming": [strip(e) for e in upcoming],
        "next": strip(nxt) if nxt else None,
        "current": [strip(e) for e in cur],
        "conflicts": conflicts,
        "gaps": gaps,
        "sources": sources,
        "total": len(uniq),
    }


def event_key(day: str, title: str):
    """볼트 일정 노트와 캘린더 일정을 잇는 키. 'YYYY-MM-DD|제목'(공백 정리, 소문자 아님)."""
    return f"{day[:10]}|{' '.join(str(title).split())}"


def _conflicts(evs):
    timed = [e for e in evs if not e["all_day"]]
    out = []
    for i in range(len(timed)):
        for j in range(i + 1, len(timed)):
            a, b = timed[i], timed[j]
            if a["_start"] < b["_end"] and b["_start"] < a["_end"] and a.get("busy", True) and b.get("busy", True):
                out.append([a["id"], b["id"]])
    return out


def _norm_loc(loc):
    """장소 비교용 정규화: 소문자 + 공백/괄호 제거."""
    return re.sub(r"[\s()]+", "", (loc or "").lower())


def _tight_gaps(evs, min_gap=15, travel_gap=45):
    """연속된 시간 지정 일정 사이 여유 확인. 겹치는 일정은 conflicts가 다루므로 여기서는 건너뛴다.
    장소가 서로 다르고 여유가 travel_gap 미만이면 "travel"(이동 빠듯), 그 외 여유가 min_gap
    미만이면 "tight"(빠듯)."""
    timed = sorted((e for e in evs if not e["all_day"] and e.get("busy", True)), key=lambda e: e["_start"])
    out = []
    for a, b in zip(timed, timed[1:]):
        if a["_end"] > b["_start"]:
            continue  # 겹침은 conflicts에서 다룬다
        gap_min = int((b["_start"] - a["_end"]).total_seconds() // 60)
        loc_a, loc_b = _norm_loc(a.get("location")), _norm_loc(b.get("location"))
        if loc_a and loc_b and loc_a != loc_b and gap_min < travel_gap:
            kind = "travel"
        elif gap_min < min_gap:
            kind = "tight"
        else:
            continue
        out.append({"a": a["id"], "b": b["id"], "gap_min": gap_min, "kind": kind,
                    "from": a["title"], "to": b["title"], "at": hhmm(a["start"])})
    return out


def hhmm(iso: str):
    return iso[11:16] if len(iso) >= 16 else ""


def agenda_sentence(agenda: dict):
    """보고 문장 한 개: 오늘 일정 요약. 없으면 None."""
    today = agenda.get("today") or []
    timed = [e for e in today if not e["all_day"]]
    all_day = [e for e in today if e["all_day"]]
    if not today:
        srcs = agenda.get("sources") or []
        if srcs and all(s["status"] in ("permission", "missing", "fail", "unconfigured") for s in srcs):
            return None  # 연결이 안 된 것은 문장으로 말하지 않고 소스 상태로 보여준다
        up = agenda.get("upcoming") or []
        tomorrow = [e for e in up if e.get("days_left") == 1]
        if tomorrow:
            names = ", ".join((("종일 " if e["all_day"] else hhmm(e["start"]) + " ") + e["title"]) for e in tomorrow[:2])
            return f"오늘은 잡힌 일정이 없고, 내일은 {names}" + (f" 외 {len(tomorrow) - 2}개" if len(tomorrow) > 2 else "") + "이 있어요."
        nxt = up[0] if up else None
        if nxt:
            return f"오늘은 잡힌 일정이 없어요. 다음 일정은 {nxt['days_left']}일 뒤 {nxt['title']}이에요."
        return "오늘은 잡힌 일정이 없어요."
    parts = [f"{hhmm(e['start'])} {e['title']}" for e in timed[:2]]  # 문장은 두 개까지, 나머지는 시간표가 보여준다
    s = f"오늘 일정 {len(today)}개"
    if parts:
        s += ": " + ", ".join(parts) + (" 등" if len(timed) > 2 else "")
    if all_day:
        s += (", " if parts else ": ") + "종일 " + ", ".join(e["title"] for e in all_day[:2])
    s += "."
    if agenda.get("conflicts"):
        s += f" 겹치는 일정 {len(agenda['conflicts'])}쌍이 있어요."
    today_gaps = [g for g in (agenda.get("gaps") or []) if "day" not in g]
    if today_gaps:
        g = today_gaps[0]
        s += f" · {g['at']} {g['from']} 다음 {g['gap_min']}분 뒤 {g['to']}라 빠듯해요."
    return s


def agenda_kakao(agenda: dict, limit: int = 90):
    """카톡용 짧은 조각(예: '일정 10:00 주간회의, 15:00 치과')."""
    today = agenda.get("today") or []
    if not today:
        return ""
    bits = []
    for e in today[:4]:
        bits.append((("종일 " if e["all_day"] else hhmm(e["start"]) + " ") + e["title"]))
    s = "일정 " + ", ".join(bits) + (f" 외 {len(today) - 4}" if len(today) > 4 else "")
    today_gaps = [g for g in (agenda.get("gaps") or []) if "day" not in g]
    if today_gaps:
        g = today_gaps[0]
        s += " · " + f"빠듯: {g['from']}→{g['to']} {g['gap_min']}분"[:25]
    return s[:limit]


def agenda_human(agenda: dict):
    lines = []
    today = agenda.get("today") or []
    if today:
        lines.append(f"오늘 일정 {len(today)}개")
        for e in today:
            when = "종일     " if e["all_day"] else f"{hhmm(e['start'])}-{hhmm(e['end'])}"
            loc = f" @ {e['location']}" if e.get("location") else ""
            note = e.get("note")
            mark = (f"  ✎ 준비 {note['done']}/{note['total']}" if note and note["total"] else ("  ✎" if note else ""))
            lines.append(f"  {when}  {e['title']}{loc}  [{e['calendar']}]{mark}")
            if e.get("description"):
                lines.append("             " + e["description"].split("\n")[0][:80])
        today_gaps = [g for g in (agenda.get("gaps") or []) if "day" not in g]
        if today_gaps:
            lines.append("여유 경고: " + ", ".join(f"{g['from']}→{g['to']} {g['gap_min']}분" for g in today_gaps))
    else:
        lines.append("오늘 일정 없음")
    up = agenda.get("upcoming") or []
    if up:
        lines.append(f"다가오는 {agenda.get('days', 7)}일: {len(up)}개")
        by_day = {}
        for e in up:
            by_day.setdefault(e["start"][:10], []).append(e)
        for d in sorted(by_day)[:7]:
            items = by_day[d]
            lines.append(f"  {d}  " + " · ".join((("종일 " if e["all_day"] else hhmm(e["start"]) + " ") + e["title"]) for e in items[:4])
                         + (f" 외 {len(items) - 4}" if len(items) > 4 else ""))
    for s in agenda.get("sources") or []:
        if s["status"] != "ok":
            lines.append(f"  ! {s['name']}({s['kind']}): {s['status']} — {s.get('error') or ''}".rstrip(" —"))
            if s.get("hint"):
                lines.append(f"    {s['hint']}")
    return "\n".join(lines)
