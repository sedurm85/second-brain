"""커리어 — 이직 포지션 파이프라인·헤드헌터 연락부·내 자산을 한 탭에서.

세컨드브레인 대시보드의 별도 탭(/career). 데이터는 전부 볼트의 career/ 폴더 마크다운이고,
이 폴더는 일반 노트 처리(보드·검색·그래프·BRAIN.md 인덱스)에서 빠진다(brain.SKIP_DIRS).
재직 중 이직 정보가 다른 화면이나 세션 시작 인덱스에 섞여 보이지 않게 하기 위해서다.

- career/positions/<slug>.md : 포지션 카드(회사·포지션·상태·점수·다음 할 일·재평가 조건) + ## 기록
- career/contacts/<slug>.md  : 헤드헌터·리크루터(소속·채널·답장 기한·연결 포지션) + ## 기록
- career/profile.md          : 내 자산(이력서·경력기술서 위치, 킬러 수치, 대외비 원칙) — 자유 마크다운
- 잡스카우트 결과(~/.local/k-skill-cron/jobscout_result.md, ~/Downloads/jobscout_reports/)는 읽기만 한다.

brain.py가 자기 모듈 객체(api)를 넘겨 호출한다(studio.py와 같은 방식).
"""
from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path

STATUSES = ["발견", "검토", "진행", "지원", "면접", "오퍼", "보류", "안함", "종료"]
ACTIVE = {"발견", "검토", "진행", "지원", "면접", "오퍼"}
SOURCES = ["잡스카우트", "헤드헌터", "직접", "인바운드", "지인"]
CONTACT_STATUSES = ["응대중", "대기", "종료"]
POSITION_FIELDS = ("title", "company", "role", "status", "score", "source", "url", "location", "contact",
                   "next_action", "next_due", "revisit_trigger", "summary", "salary_note")
CONTACT_FIELDS = ("title", "firm", "channel", "status", "reply_due", "email", "phone", "summary", "positions")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
LOG_RE = re.compile(r"^- (\d{4}-\d{2}-\d{2})(?: (\d{2}:\d{2}))?\s+(.*)$")
DEFAULT_SCOUT_RESULT = Path.home() / ".local" / "k-skill-cron" / "jobscout_result.md"
DEFAULT_REPORT_DIR = Path.home() / "Downloads" / "jobscout_reports"


class CareerError(Exception):
    pass


# ───────────────────────── 경로·입출력 ─────────────────────────

def root(vault):
    return Path(vault) / "career"


def _dir(vault, kind):
    d = root(vault) / ("positions" if kind == "position" else "contacts")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _today():
    return date.today()


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _clean(v, n=400):
    return " ".join(str(v or "").split())[:n]


def _date_or_blank(v, what):
    v = _clean(v, 10)
    if v and not DATE_RE.match(v):
        raise CareerError(f"{what}는 YYYY-MM-DD 형식이에요")
    return v


def _read(api, path):
    meta, body = api.parse_frontmatter(path.read_text(encoding="utf-8"))
    return meta, body


def _logs(body):
    out = []
    in_log = False
    for ln in body.split("\n"):
        if ln.strip() == "## 기록":
            in_log = True
            continue
        if in_log and ln.startswith("## "):
            break
        if in_log:
            m = LOG_RE.match(ln.strip())
            if m:
                out.append({"date": m.group(1), "time": m.group(2) or "", "text": m.group(3)})
    return out


def _sections(body):
    """'## 제목' 단위 본문(기록 제외)."""
    out, cur, buf = {}, "", []
    for ln in body.split("\n"):
        if ln.startswith("## "):
            out[cur] = "\n".join(buf).strip()
            cur, buf = ln[3:].strip(), []
        elif not ln.startswith("# "):
            buf.append(ln)
    out[cur] = "\n".join(buf).strip()
    out.pop("기록", None)
    return {k: v for k, v in out.items() if v}


def _append_log(body, text):
    line = f"- {_now()}  {text}"
    if "\n## 기록" not in "\n" + body:
        return body.rstrip("\n") + "\n\n## 기록\n" + line + "\n"
    lines = body.rstrip("\n").split("\n")
    start = next(i for i, l in enumerate(lines) if l.strip() == "## 기록")
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("## "):
            end = j
            break
    lines.insert(end, line)
    return "\n".join(lines) + "\n"


def _days_left(d, today):
    if not d or not DATE_RE.match(str(d)):
        return None
    return (date.fromisoformat(str(d)) - today).days


def _position_payload(api, path, today, with_body=False):
    meta, body = _read(api, path)
    p = {k: meta.get(k, "") for k in POSITION_FIELDS}
    p["id"] = path.stem
    p["status"] = p["status"] if p["status"] in STATUSES else "발견"
    try:
        p["score"] = int(p["score"]) if str(p["score"]).strip() else None
    except ValueError:
        p["score"] = None
    p["created"] = str(meta.get("created") or "")
    p["updated"] = str(meta.get("updated") or meta.get("created") or "")
    p["due_left"] = _days_left(p["next_due"], today)
    logs = _logs(body)
    p["last_log"] = logs[-1] if logs else None
    p["contact"] = str(p["contact"] or "").strip("[]")
    if with_body:
        p["sections"] = _sections(body)
        p["logs"] = logs
    return p


def _contact_payload(api, path, today, with_body=False):
    meta, body = _read(api, path)
    c = {k: meta.get(k, "") for k in CONTACT_FIELDS}
    c["id"] = path.stem
    c["status"] = c["status"] if c["status"] in CONTACT_STATUSES else "응대중"
    c["reply_left"] = _days_left(c["reply_due"], today)
    raw = c["positions"]
    c["positions"] = [str(x).strip("[]") for x in (raw if isinstance(raw, list) else ([raw] if raw else []))]
    logs = _logs(body)
    c["last_log"] = logs[-1] if logs else None
    c["updated"] = str(meta.get("updated") or meta.get("created") or "")
    if with_body:
        c["sections"] = _sections(body)
        c["logs"] = logs
    return c


def _find(vault, kind, pid):
    pid = str(pid or "").strip()
    if not pid or "/" in pid or "\\" in pid or pid.startswith("."):
        raise CareerError("잘못된 id예요")
    p = _dir(vault, kind) / f"{pid}.md"
    if not p.is_file():
        raise CareerError(("포지션" if kind == "position" else "연락처") + f"을 찾을 수 없어요: {pid}")
    return p


def _unique(d, base):
    stem, n = base or "item", 2
    while (d / f"{stem}.md").exists():
        stem = f"{base}-{n}"
        n += 1
    return stem


def _commit(api, vault, msg):
    try:
        api.git_commit(Path(vault), msg)
    except Exception as e:  # noqa: BLE001 - 커밋 실패가 저장을 막지 않게
        api.log(f"경고: 커리어 커밋 실패({e})")


# ───────────────────────── 포지션 ─────────────────────────

def _norm_position_fields(patch, partial=True):
    out = {}
    for k in POSITION_FIELDS:
        if k not in patch:
            continue
        v = patch[k]
        if k == "status":
            v = _clean(v, 10)
            if v not in STATUSES:
                raise CareerError("상태는 " + " · ".join(STATUSES) + " 중 하나예요")
        elif k == "score":
            if v in ("", None):
                v = ""
            else:
                try:
                    v = int(v)
                except (TypeError, ValueError):
                    raise CareerError("점수는 0~100 숫자예요")
                if not 0 <= v <= 100:
                    raise CareerError("점수는 0~100 숫자예요")
        elif k == "next_due":
            v = _date_or_blank(v, "다음 할 일 기한")
        elif k == "source":
            v = _clean(v, 20)
        elif k == "contact":
            v = _clean(v, 80).strip("[]")
            v = f"[[{v}]]" if v else ""
        else:
            v = _clean(v, 600 if k in ("summary", "revisit_trigger", "salary_note") else 200)
        out[k] = v
    return out


def add_position(api, vault, data):
    company, role = _clean(data.get("company"), 80), _clean(data.get("role"), 120)
    if not company:
        raise CareerError("회사 이름이 필요해요")
    fields = _norm_position_fields({**data, "company": company, "role": role})
    fields.setdefault("status", "발견")
    title = fields.get("title") or (f"{company} · {role}" if role else company)
    today = _today().isoformat()
    meta = {"title": title, "type": "position", "created": today, "updated": today, "tags": ["커리어"]}
    meta.update({k: v for k, v in fields.items() if k != "title" and v != ""})
    d = _dir(vault, "position")
    stem = _unique(d, api.slugify(title))
    body = f"# {title}\n\n## 핏 분석\n{_clean(data.get('fit'), 4000)}\n\n## 갭\n{_clean(data.get('gap'), 4000)}\n\n## 기록\n- {_now()}  카드 생성 ({fields.get('status')})\n"
    api.write_note(d / f"{stem}.md", meta, body)
    _commit(api, vault, f"career: add {stem}")
    return _position_payload(api, d / f"{stem}.md", _today(), with_body=True)


def update_position(api, vault, pid, patch, log=None):
    path = _find(vault, "position", pid)
    meta, body = _read(api, path)
    fields = _norm_position_fields(patch or {})
    before = meta.get("status")
    for k, v in fields.items():
        if v == "":
            meta.pop(k, None)
        else:
            meta[k] = v
    meta["updated"] = _today().isoformat()
    notes = []
    if "status" in fields and fields["status"] != before:
        notes.append(f"상태 {before or '-'} → {fields['status']}")
    if log:
        notes.append(_clean(log, 1000))
    for k in ("fit", "gap"):
        if k in (patch or {}):
            body = _replace_section(body, "핏 분석" if k == "fit" else "갭", str(patch[k] or "").strip()[:8000])
    if notes:
        body = _append_log(body, " · ".join(notes))
    api.write_note(path, meta, body)
    _commit(api, vault, f"career: update {pid}")
    return _position_payload(api, path, _today(), with_body=True)


def _replace_section(body, header, text):
    lines = body.rstrip("\n").split("\n")
    try:
        s = next(i for i, l in enumerate(lines) if l.strip() == f"## {header}")
    except StopIteration:
        # 기록 절 앞에 끼워 넣는다
        try:
            s = next(i for i, l in enumerate(lines) if l.strip() == "## 기록")
            lines[s:s] = [f"## {header}", text, ""]
        except StopIteration:
            lines += ["", f"## {header}", text]
        return "\n".join(lines) + "\n"
    e = len(lines)
    for j in range(s + 1, len(lines)):
        if lines[j].startswith("## "):
            e = j
            break
    return "\n".join(lines[:s + 1] + [text, ""] + lines[e:]) + "\n"


def delete_position(api, vault, pid):
    path = _find(vault, "position", pid)
    path.unlink()
    _commit(api, vault, f"career: delete {pid}")
    return True


# ───────────────────────── 연락처 ─────────────────────────

def _norm_contact_fields(patch):
    out = {}
    for k in CONTACT_FIELDS:
        if k not in patch:
            continue
        v = patch[k]
        if k == "status":
            v = _clean(v, 10)
            if v not in CONTACT_STATUSES:
                raise CareerError("연락처 상태는 " + " · ".join(CONTACT_STATUSES) + " 중 하나예요")
        elif k == "reply_due":
            v = _date_or_blank(v, "답장 기한")
        elif k == "positions":
            items = v if isinstance(v, list) else [x for x in str(v or "").split(",")]
            v = [f"[[{_clean(x, 80).strip('[]')}]]" for x in items if _clean(x, 80).strip("[]")]
        else:
            v = _clean(v, 600 if k == "summary" else 120)
        out[k] = v
    return out


def add_contact(api, vault, data):
    name = _clean(data.get("name") or data.get("title"), 40)
    if not name:
        raise CareerError("이름이 필요해요")
    fields = _norm_contact_fields(data)
    fields.setdefault("status", "응대중")
    firm = fields.get("firm", "")
    title = f"{name}" + (f" ({firm})" if firm else "")
    today = _today().isoformat()
    meta = {"title": title, "type": "contact", "created": today, "updated": today, "tags": ["커리어", "헤드헌터"]}
    meta.update({k: v for k, v in fields.items() if k != "title" and v not in ("", [])})
    d = _dir(vault, "contact")
    stem = _unique(d, api.slugify(name + (" " + firm if firm else "")))
    body = f"# {title}\n\n## 메모\n{_clean(data.get('memo'), 4000)}\n\n## 기록\n- {_now()}  연락처 생성\n"
    api.write_note(d / f"{stem}.md", meta, body)
    _commit(api, vault, f"career: contact {stem}")
    return _contact_payload(api, d / f"{stem}.md", _today(), with_body=True)


def update_contact(api, vault, cid, patch, log=None):
    path = _find(vault, "contact", cid)
    meta, body = _read(api, path)
    for k, v in _norm_contact_fields(patch or {}).items():
        if v in ("", []):
            meta.pop(k, None)
        else:
            meta[k] = v
    meta["updated"] = _today().isoformat()
    if log:
        body = _append_log(body, _clean(log, 1000))
    api.write_note(path, meta, body)
    _commit(api, vault, f"career: contact update {cid}")
    return _contact_payload(api, path, _today(), with_body=True)


# ───────────────────────── 자산·스카우트 ─────────────────────────

PROFILE_TEMPLATE = """# 내 커리어 자산

## 문서
- 이력서:
- 경력기술서:

## 킬러 수치
-

## 대외비 원칙
-

## 타깃 방향
-
"""


def profile_path(vault):
    return root(vault) / "profile.md"


def read_profile(vault):
    p = profile_path(vault)
    return p.read_text(encoding="utf-8") if p.is_file() else PROFILE_TEMPLATE


def save_profile(api, vault, text):
    text = str(text or "")
    if len(text) > 60000:
        raise CareerError("자산 메모는 6만 자까지예요")
    p = profile_path(vault)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    _commit(api, vault, "career: profile")
    return {"ok": True, "chars": len(text)}


def scout_sources(api):
    cfg = api.load_config()
    res = Path(os.path.expanduser(os.environ.get("SECOND_BRAIN_SCOUT_RESULT") or cfg.get("career_scout_result") or str(DEFAULT_SCOUT_RESULT)))
    rep = Path(os.path.expanduser(os.environ.get("SECOND_BRAIN_SCOUT_REPORTS") or cfg.get("career_scout_reports") or str(DEFAULT_REPORT_DIR)))
    return res, rep


def scout_overview(api, limit=8):
    """잡스카우트 최신 결과 줄과 매니저 리포트 목록(읽기 전용)."""
    res, rep = scout_sources(api)
    lines, updated = [], None
    if res.is_file():
        updated = datetime.fromtimestamp(res.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        for ln in res.read_text(encoding="utf-8", errors="replace").split("\n"):
            s = ln.strip()
            if s.startswith(("-", "*", "⭐", "•")) or re.match(r"^\d+\.", s):
                url = re.search(r"https?://\S+", s)
                lines.append({"text": re.sub(r"https?://\S+", "", s.lstrip("-*• ")).strip()[:200], "url": url.group(0).rstrip(")") if url else ""})
            if len(lines) >= 30:
                break
    reports = []
    if rep.is_dir():
        for f in sorted(rep.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
            head = ""
            try:
                for ln in f.read_text(encoding="utf-8", errors="replace").split("\n"):
                    if ln.strip() and not ln.startswith("---"):
                        head = ln.lstrip("# ").strip()[:120]
                        break
            except OSError:
                head = ""
            reports.append({"name": f.name, "path": str(f), "title": head,
                            "modified": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M")})
    return {"result_updated": updated, "lines": lines, "reports": reports,
            "result_path": str(res), "reports_dir": str(rep)}


def read_report(api, name):
    _, rep = scout_sources(api)
    name = os.path.basename(str(name or ""))
    p = rep / name
    if not name.endswith(".md") or not p.is_file():
        raise CareerError("리포트를 찾을 수 없어요")
    return {"name": name, "text": p.read_text(encoding="utf-8", errors="replace")[:200000]}


# ───────────────────────── 요약·기한 ─────────────────────────

def list_positions(api, vault, today=None):
    today = today or _today()
    d = root(vault) / "positions"
    if not d.is_dir():
        return []
    out = [_position_payload(api, p, today) for p in sorted(d.glob("*.md"))]
    order = {s: i for i, s in enumerate(STATUSES)}
    out.sort(key=lambda p: (order.get(p["status"], 99), p["due_left"] if p["due_left"] is not None else 999,
                            -(p["score"] or 0), p["updated"]), reverse=False)
    return out


def list_contacts(api, vault, today=None):
    today = today or _today()
    d = root(vault) / "contacts"
    if not d.is_dir():
        return []
    out = [_contact_payload(api, p, today) for p in sorted(d.glob("*.md"))]
    out.sort(key=lambda c: (c["status"] == "종료", c["reply_left"] if c["reply_left"] is not None else 999, c["updated"]))
    return out


def due_items(api, vault, today=None, within=2):
    """기한이 within일 안(지난 것 포함)인 다음 할 일·답장. 보드 브리핑·카톡용."""
    today = today or _today()
    out = []
    for p in list_positions(api, vault, today):
        if p["status"] in ACTIVE and p["due_left"] is not None and p["due_left"] <= within:
            out.append({"kind": "position", "id": p["id"], "who": p["company"], "what": p["next_action"] or "다음 할 일",
                        "due": p["next_due"], "days_left": p["due_left"]})
    for c in list_contacts(api, vault, today):
        if c["status"] != "종료" and c["reply_left"] is not None and c["reply_left"] <= within:
            out.append({"kind": "contact", "id": c["id"], "who": c["title"], "what": "답장",
                        "due": c["reply_due"], "days_left": c["reply_left"]})
    out.sort(key=lambda x: x["days_left"])
    return out


def due_sentence(items, limit=2):
    if not items:
        return ""
    bits = []
    for x in items[:limit]:
        dl = x["days_left"]
        when = "오늘" if dl == 0 else ("지남" if dl < 0 else f"D-{dl}")
        bits.append(f"{x['who'].split(' (')[0]} {x['what']} {when}")
    return "커리어 " + ", ".join(bits) + (f" 외 {len(items) - limit}" if len(items) > limit else "")


def overview(api, vault, today=None):
    today = today or _today()
    pos = list_positions(api, vault, today)
    counts = {s: 0 for s in STATUSES}
    for p in pos:
        counts[p["status"]] = counts.get(p["status"], 0) + 1
    return {"statuses": STATUSES, "active_statuses": sorted(ACTIVE, key=STATUSES.index), "sources": SOURCES,
            "contact_statuses": CONTACT_STATUSES, "counts": counts, "positions": pos,
            "contacts": list_contacts(api, vault, today), "due": due_items(api, vault, today, within=3),
            "today": today.isoformat()}


# ───────────────────────── HTTP ─────────────────────────

def handle_get(api, route, qs, vault):
    one = lambda k: (qs.get(k) or [""])[0]
    if route == "/api/career":
        return overview(api, vault)
    if route == "/api/career/position":
        return _position_payload(api, _find(vault, "position", one("id")), _today(), with_body=True)
    if route == "/api/career/contact":
        return _contact_payload(api, _find(vault, "contact", one("id")), _today(), with_body=True)
    if route == "/api/career/profile":
        return {"text": read_profile(vault), "exists": profile_path(vault).is_file()}
    if route == "/api/career/scout":
        return scout_overview(api)
    if route == "/api/career/report":
        return read_report(api, one("name"))
    return None


def handle_post(api, route, body, vault):
    if route == "/api/career/position":
        act = body.get("action")
        if act == "add":
            return {"ok": True, "position": add_position(api, vault, body)}
        if act == "update":
            return {"ok": True, "position": update_position(api, vault, body.get("id"), body.get("patch") or {}, body.get("log"))}
        if act == "status":
            return {"ok": True, "position": update_position(api, vault, body.get("id"), {"status": body.get("status")}, body.get("note"))}
        if act == "log":
            text = _clean(body.get("text"), 1000)
            if not text:
                raise CareerError("기록할 내용이 비었어요")
            return {"ok": True, "position": update_position(api, vault, body.get("id"), {}, text)}
        if act == "delete":
            return {"ok": delete_position(api, vault, body.get("id"))}
        raise CareerError("action은 add · update · status · log · delete")
    if route == "/api/career/contact":
        act = body.get("action")
        if act == "add":
            return {"ok": True, "contact": add_contact(api, vault, body)}
        if act == "update":
            return {"ok": True, "contact": update_contact(api, vault, body.get("id"), body.get("patch") or {}, body.get("log"))}
        if act == "log":
            text = _clean(body.get("text"), 1000)
            if not text:
                raise CareerError("기록할 내용이 비었어요")
            return {"ok": True, "contact": update_contact(api, vault, body.get("id"), {}, text)}
        raise CareerError("action은 add · update · log")
    if route == "/api/career/profile":
        return save_profile(api, vault, body.get("text"))
    return None
