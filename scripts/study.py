"""공부방 — 매일 하루치(보안 뉴스 3 · 실무감 한 조각 · CISA 문제 5)와 CISA 전체 과정.

세컨드브레인 대시보드의 별도 탭(/study). 데이터는 볼트의 study/ 폴더이고, 이 폴더는
일반 노트 처리(보드·검색·그래프·BRAIN.md 인덱스)에서 빠진다(brain.SKIP_DIRS).

- study/cisa/<장>.json   : 장별 개념(마크다운)·용어·문제. `study fill`이 헤드리스 Claude로 채운다.
- study/daily/<날짜>.json : 하루치. `study daily`(launchd 새벽)가 만든다. 뉴스는 RSS 원문만 근거로 쓴다.
- study/progress.json     : 진도 기억 — 풀이 기록, 오답 상자(1·3·7일 간격), 읽은 장, 학습일, 실무감 단원, 모의고사.

실무감 재료는 CISSP+CISM, 시험은 CISA(한국어). 실무감 10단원은 세 자격의 겹침 순서다.
brain.py가 자기 모듈 객체(api)를 넘겨 호출한다(career.py와 같은 방식).
"""
from __future__ import annotations

import json
import random
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path


class StudyError(Exception):
    pass


# ───────────────────────── 커리큘럼 ─────────────────────────

UNITS = [  # 실무감 10단원: CISSP·CISM·CISA 겹침 순서
    {"n": 1, "title": "거버넌스·정책·조직", "map": "CISSP D1 · CISM D1 · CISA D2"},
    {"n": 2, "title": "위험관리", "map": "CISSP D1 · CISM D2 · CISA D2"},
    {"n": 3, "title": "보안 프로그램·통제·지표", "map": "CISSP D1·D6 · CISM D3 · CISA D2·D5"},
    {"n": 4, "title": "사고 대응·BCP/DR", "map": "CISSP D7 · CISM D4 · CISA D4", "hot": True},
    {"n": 5, "title": "자산·데이터·개인정보", "map": "CISSP D2 · CISM D3 · CISA D5"},
    {"n": 6, "title": "접근통제·IAM", "map": "CISSP D5 · CISM D3 · CISA D5", "hot": True},
    {"n": 7, "title": "평가·감사·테스트", "map": "CISSP D6 · CISM D3 · CISA D1"},
    {"n": 8, "title": "개발 보안·변경관리", "map": "CISSP D8 · CISM D3 · CISA D3", "hot": True},
    {"n": 9, "title": "운영 보안·로그·모니터링", "map": "CISSP D7 · CISM D3·D4 · CISA D4", "hot": True},
    {"n": 10, "title": "아키텍처·암호·네트워크", "map": "CISSP D3·D4 · CISA D5 일부", "hot": True},
    {"n": 11, "title": "AI 보안", "map": "AAISM · CISM D1·D2 · CISSP D1·D8", "hot": True, "ai": True},
]
PIECES_PER_UNIT = 5  # 단원마다 실무 한 조각 5개를 지나면 다음 단원
DEFAULT_TARGET = "2027-02-28"  # 2026-11-13 사용자 확정: CISA 응시 기준선(강제 마감 아님)
MOCK_WEEKS = 2  # 목표일 전 2주는 모의고사 기간 — 장 진도는 그 전에 끝낸다

DOMAINS = [  # 2026 ISACA 응시자 가이드 비중
    {"id": "D1", "name": "정보시스템 감사 프로세스", "weight": 18, "chapters": [
        {"id": "1-1", "title": "감사 기준·윤리, 위험 기반 감사 계획", "unit": 7},
        {"id": "1-2", "title": "통제의 종류(예방·탐지·교정)와 평가", "unit": 3},
        {"id": "1-3", "title": "표본 추출, 감사 증거, 데이터 분석", "unit": 7},
        {"id": "1-4", "title": "감사 보고와 후속 조치, 통제 자가 평가", "unit": 7}]},
    {"id": "D2", "name": "IT 거버넌스와 관리", "weight": 18, "chapters": [
        {"id": "2-1", "title": "IT 거버넌스·전략·위원회 구조", "unit": 1},
        {"id": "2-2", "title": "정책·표준·절차, 조직 구조와 직무 분리", "unit": 1},
        {"id": "2-3", "title": "전사 위험관리, 법규 준수", "unit": 2},
        {"id": "2-4", "title": "IT 자원·벤더 관리, 성과 지표", "unit": 3}]},
    {"id": "D3", "name": "정보시스템 도입·개발·구현", "weight": 12, "chapters": [
        {"id": "3-1", "title": "사업 타당성, 프로젝트 관리", "unit": 8},
        {"id": "3-2", "title": "개발 방법론과 단계별 통제", "unit": 8},
        {"id": "3-3", "title": "테스트, 이관, 구현 후 검토", "unit": 8}]},
    {"id": "D4", "name": "정보시스템 운영과 업무 회복력", "weight": 26, "chapters": [
        {"id": "4-1", "title": "운영 관리, IT 자산, 작업 스케줄", "unit": 9},
        {"id": "4-2", "title": "문제·사고 관리, 변경·구성·패치 관리", "unit": 4},
        {"id": "4-3", "title": "업무 영향 분석(BIA), 백업과 복구", "unit": 4},
        {"id": "4-4", "title": "업무 연속성(BCP)·재해 복구(DRP) 계획과 테스트", "unit": 4}]},
    {"id": "D5", "name": "정보자산 보호", "weight": 26, "chapters": [
        {"id": "5-1", "title": "보안 프레임워크, 데이터 분류·개인정보", "unit": 5},
        {"id": "5-2", "title": "접근통제와 계정 관리", "unit": 6},
        {"id": "5-3", "title": "네트워크·암호·클라우드 보안", "unit": 10},
        {"id": "5-4", "title": "물리·환경 보안, 보안 이벤트 모니터링", "unit": 9}]},
]
CHAPTERS = {c["id"]: dict(c, domain=d["id"]) for d in DOMAINS for c in d["chapters"]}
ORDER = sorted(CHAPTERS, key=lambda k: (CHAPTERS[k]["unit"], k))  # 실무감 단원 순서를 따라 공부
QUESTIONS_PER_CHAPTER = 12
MINI = {"D1": 5, "D2": 5, "D3": 4, "D4": 8, "D5": 8}  # 30문항, 도메인 비중대로
BOX_DAYS = [1, 3, 7]  # 오답 상자: 틀리면 1일 뒤, 맞히면 3일·7일 뒤, 세 번 맞히면 졸업
READY_RATE, READY_RUNS = 0.75, 3
FEEDS = [("데일리시큐", "https://www.dailysecu.com/rss/allArticle.xml"),
         ("The Hacker News", "https://feeds.feedburner.com/TheHackersNews")]
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) second-brain-study"


# ───────────────────────── 저장소 ─────────────────────────

def root(vault):
    return Path(vault) / "study"


def _read(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except ValueError as e:
        raise StudyError(f"{Path(path).name}이 손상됐어요({e})") from e


def _write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def chapter_path(vault, cid):
    return root(vault) / "cisa" / f"{cid}.json"


def daily_path(vault, day):
    return root(vault) / "daily" / f"{day.isoformat()}.json"


def load_progress(vault):
    p = _read(root(vault) / "progress.json", {})
    p.setdefault("attempts", [])
    p.setdefault("boxes", {})
    p.setdefault("read", {})
    p.setdefault("days", [])
    p.setdefault("field", {"unit": 1, "covered": []})
    p.setdefault("mocks", [])
    p.setdefault("plan", {"target": DEFAULT_TARGET, "start": date.today().isoformat()})
    return p


def save_progress(vault, p):
    _write(root(vault) / "progress.json", p)


def load_chapter(vault, cid):
    if cid not in CHAPTERS:
        raise StudyError(f"없는 장이에요: {cid}")
    return _read(chapter_path(vault, cid), None)


def all_questions(vault):
    out = {}
    for cid in ORDER:
        ch = load_chapter(vault, cid)
        for i, q in enumerate((ch or {}).get("questions", [])):
            out[f"{cid}#{i + 1}"] = dict(q, id=f"{cid}#{i + 1}", chapter=cid, domain=CHAPTERS[cid]["domain"])
    return out


# ───────────────────────── 진도 ─────────────────────────

def _mark_day(p, day):
    d = day.isoformat()
    if d not in p["days"]:
        p["days"].append(d)
        p["days"].sort()


def streak(p, today):
    days = set(p["days"])
    d = today if today.isoformat() in days else today - timedelta(days=1)
    n = 0
    while d.isoformat() in days:
        n += 1
        d -= timedelta(days=1)
    return n


def record_answer(vault, qid, choice, mode="practice", today=None):
    today = today or date.today()
    qs = all_questions(vault)
    if qid not in qs:
        raise StudyError(f"없는 문제예요: {qid}")
    q = qs[qid]
    try:
        choice = int(choice)
    except (TypeError, ValueError) as e:
        raise StudyError("choice는 0~3") from e
    ok = choice == int(q["answer"])
    p = load_progress(vault)
    p["attempts"].append({"q": qid, "ok": ok, "at": datetime.now().strftime("%Y-%m-%d %H:%M"), "mode": str(mode)[:12]})
    box = p["boxes"].get(qid)
    if not ok:
        p["boxes"][qid] = {"box": 0, "due": (today + timedelta(days=BOX_DAYS[0])).isoformat()}
    elif box is not None:
        nb = box["box"] + 1
        if nb >= len(BOX_DAYS):
            p["boxes"].pop(qid)
        else:
            p["boxes"][qid] = {"box": nb, "due": (today + timedelta(days=BOX_DAYS[nb])).isoformat()}
    _mark_day(p, today)
    save_progress(vault, p)
    return {"ok": ok, "answer": int(q["answer"]), "explanation": q.get("explanation", "")}


def mark_read(vault, cid, today=None):
    today = today or date.today()
    load_chapter(vault, cid)
    p = load_progress(vault)
    p["read"][cid] = today.isoformat()
    _mark_day(p, today)
    save_progress(vault, p)
    return {"ok": True, "chapter": cid}


def record_mock(vault, mode, score, total, today=None):
    today = today or date.today()
    if mode not in ("mini", "full"):
        raise StudyError("mode는 mini · full")
    score, total = int(score), int(total)
    if total <= 0 or not 0 <= score <= total:
        raise StudyError("점수가 이상해요")
    p = load_progress(vault)
    p["mocks"].append({"date": today.isoformat(), "mode": mode, "score": score, "total": total})
    save_progress(vault, p)
    return {"ok": True, "readiness": readiness(p)}


def readiness(p):
    minis = [m for m in p["mocks"] if m["mode"] in ("mini", "full")][-READY_RUNS:]
    rates = [m["score"] / m["total"] for m in minis]
    ready = len(rates) == READY_RUNS and all(r >= READY_RATE for r in rates)
    return {"ready": ready, "recent": [round(r * 100) for r in rates], "need": READY_RUNS, "rate": round(READY_RATE * 100)}


def pace(p, today):
    """목표일 기준 계획 대비 진도. 장 진도는 (목표일 − 2주)까지 고르게, 그 뒤는 모의고사 기간."""
    plan = p["plan"]
    target = date.fromisoformat(plan["target"])
    start = date.fromisoformat(plan["start"])
    study_end = target - timedelta(weeks=MOCK_WEEKS)
    span = max(1, (study_end - start).days)
    done = len([c for c in p["read"] if c in CHAPTERS])
    frac = min(1.0, max(0.0, (today - start).days / span))
    expected = min(len(ORDER), int(len(ORDER) * frac + 0.999)) if today > start else 0
    weeks_left = max(0, (study_end - today).days) / 7
    per_week = round((len(ORDER) - done) / weeks_left, 1) if weeks_left else None
    phase = "모의고사 기간" if today >= study_end else "진도 기간"
    gap = done - expected
    return {"target": plan["target"], "d_day": (target - today).days, "chapters_done": done, "chapters_expected": expected, "gap": gap,
            "per_week": per_week, "phase": phase, "mock_from": study_end.isoformat(),
            "status": "앞섬" if gap > 0 else ("계획대로" if gap == 0 else "뒤처짐")}


def set_target(vault, target):
    try:
        date.fromisoformat(target)
    except (TypeError, ValueError) as e:
        raise StudyError("목표일은 YYYY-MM-DD") from e
    p = load_progress(vault)
    p["plan"]["target"] = target
    save_progress(vault, p)
    return pace(p, date.today())


def _stats(p, qs):
    """문제별 마지막 결과 기준 장·도메인 정답률."""
    last, cnt = {}, {}
    for a in p["attempts"]:
        last[a["q"]] = a["ok"]
        cnt[a["q"]] = cnt.get(a["q"], 0) + 1
    ch, dom = {}, {}
    for qid, ok in last.items():
        q = qs.get(qid)
        if not q:
            continue
        for key, bucket in ((q["chapter"], ch), (q["domain"], dom)):
            b = bucket.setdefault(key, {"tried": 0, "right": 0})
            b["tried"] += 1
            b["right"] += int(ok)
    for bucket in (ch, dom):
        for b in bucket.values():
            b["rate"] = round(100 * b["right"] / b["tried"]) if b["tried"] else None
    return ch, dom


def due_wrong(p, today):
    return sorted([q for q, b in p["boxes"].items() if b["due"] <= today.isoformat()])


def current_chapter(vault, p):
    """아직 안 읽은 첫 장(공부 순서). 내용이 없는 장은 건너뛴다."""
    for cid in ORDER:
        if cid not in p["read"] and chapter_path(vault, cid).is_file():
            return cid
    return None


# ───────────────────────── 출제 ─────────────────────────

def _public(q):
    return {k: q[k] for k in ("id", "chapter", "domain", "stem", "options", "answer", "explanation", "trap") if k in q}


def _pick(pool, n, p, rnd):
    """안 풀어 본 문제 → 틀린 문제 → 맞힌 문제 순으로 섞어 고른다."""
    last = {}
    for a in p["attempts"]:
        last[a["q"]] = a["ok"]
    fresh = [q for q in pool if q["id"] not in last]
    wrong = [q for q in pool if last.get(q["id"]) is False]
    right = [q for q in pool if last.get(q["id"]) is True]
    out = []
    for group in (fresh, wrong, right):
        rnd.shuffle(group)
        out += group[: n - len(out)]
        if len(out) >= n:
            break
    return out


def make_quiz(vault, mode, chapter=None, today=None, seed=None):
    today = today or date.today()
    rnd = random.Random(seed)
    p = load_progress(vault)
    qs = all_questions(vault)
    if not qs:
        raise StudyError("아직 문제가 없어요. 내용 채우기(study fill)가 먼저 돌아야 해요.")
    allq = list(qs.values())
    title = ""
    if mode == "quick":  # 10분: 가장 약한 도메인 5문제
        _, dom = _stats(p, qs)
        have = sorted({q["domain"] for q in allq})
        weakest = min(have, key=lambda d: ((dom.get(d) or {}).get("rate") if (dom.get(d) or {}).get("rate") is not None else 101,
                                           -(dict((x["id"], x["weight"]) for x in DOMAINS)[d])))
        pick = _pick([q for q in allq if q["domain"] == weakest], 5, p, rnd)
        title = f"10분 · {weakest} 약점 5문제"
    elif mode == "chapter":  # 30분: 장 개념 + 10문제
        cid = chapter or current_chapter(vault, p) or next((c for c in ORDER if chapter_path(vault, c).is_file()), None)
        if not cid or cid not in CHAPTERS:
            raise StudyError("풀 장이 없어요")
        pick = _pick([q for q in allq if q["chapter"] == cid], 10, p, rnd)
        title = f"{cid} {CHAPTERS[cid]['title']} · 10문제"
    elif mode in ("mini", "full"):
        scale = 1 if mode == "mini" else 5
        pick = []
        for d, n in MINI.items():
            pick += _pick([q for q in allq if q["domain"] == d], n * scale, p, rnd)
        if mode == "full" and len(pick) < 150:
            raise StudyError(f"실전 모의고사는 문제가 150개 이상 쌓이면 열려요(지금 {len(qs)}개)")
        rnd.shuffle(pick)
        title = "미니 모의고사 30문항 · 48분" if mode == "mini" else "실전 모의고사 150문항 · 4시간"
    elif mode == "wrong":
        ids = due_wrong(p, today) or sorted(p["boxes"])
        pick = [qs[i] for i in ids if i in qs][:20]
        title = "오답노트 다시 풀기"
    else:
        raise StudyError("mode는 quick · chapter · mini · full · wrong")
    if not pick:
        raise StudyError("이 조건에 맞는 문제가 없어요")
    minutes = {"mini": 48, "full": 240}.get(mode)
    return {"mode": mode, "title": title, "minutes": minutes, "questions": [_public(q) for q in pick]}


# ───────────────────────── 내용 채우기 (헤드리스 Claude) ─────────────────────────

def chapter_prompt(cid):
    c = CHAPTERS[cid]
    d = next(x for x in DOMAINS if x["id"] == c["domain"])
    u = UNITS[c["unit"] - 1]
    return (
        "너는 16년차 CISO를 가르치는 CISA 강사다. 학습자는 실무 경험은 많지만 현장 감각이 떨어졌고, CISA는 한국어로 응시한다. "
        "실무감 재료는 CISSP·CISM이다.\n"
        f"[장] CISA {d['id']} {d['name']}(시험 비중 {d['weight']}%) · {cid} {c['title']}\n"
        f"[연결된 실무감 단원] {u['n']}단원 {u['title']} ({u['map']})\n\n"
        "아래 JSON 하나만 출력한다(코드블록·설명 금지).\n"
        '{"concept_md": "...", "terms": [{"ko": "...", "en": "...", "desc": "..."}], "questions": [{"stem": "...", "options": ["...", "...", "...", "..."], "answer": 0, "explanation": "...", "trap": "BEST"}]}\n\n'
        "concept_md 규칙:\n"
        "- 한국어 해요체, 2,500~4,000자. 마크다운은 ##, ###, -, 1., **굵게**, 표(| |), > 인용만 쓴다.\n"
        "- 구성: ## 핵심 한 줄 → ## 개념 정리(소제목 3~5개, 용어는 한국어(영어) 병기) → ## CISA 감사인 관점 vs CISM 관리자 관점 vs CISSP 실무 관점(표) → "
        "## 시험 함정(자주 틀리는 구분 3~5개) → ## 실무 한 장면(CISO 현장에서 이 개념이 쓰이는 구체 장면 1개, 실제 기업명·가짜 수치 금지).\n"
        "- 공식 출제 범위(ISACA CISA)와 널리 알려진 표준(COBIT, ISO/IEC 27001, NIST 등)에 근거하고, 확신 없는 수치·연도는 쓰지 않는다. 특정 교재 문장을 옮기지 않는다.\n"
        "terms: 이 장 핵심 용어 8~12개(desc는 한 문장).\n"
        f"questions: 정확히 {QUESTIONS_PER_CHAPTER}개. CISA 실전 유형(감사인 관점, 4지선다, 정답 하나). 한국어 문제에 핵심 용어 영어 병기. "
        "MOST·FIRST·BEST·PRIMARY·GREATEST 같은 함정 단어를 쓰는 문제를 절반 이상 넣고 trap에 그 단어(없으면 빈 문자열)를 적는다. "
        "answer는 0~3 인덱스이고 정답 위치를 고르게 섞는다. explanation은 정답 이유와 가장 그럴듯한 오답이 틀린 이유를 2~3문장으로."
    )


def _valid_chapter(data):
    if not isinstance(data, dict) or len(str(data.get("concept_md") or "")) < 800:
        raise StudyError("개념 본문이 비었거나 너무 짧아요")
    qs = []
    for q in data.get("questions") or []:
        opts = q.get("options") if isinstance(q, dict) else None
        try:
            ans = int(q.get("answer"))
        except (TypeError, ValueError, AttributeError):
            continue
        if isinstance(opts, list) and len(opts) == 4 and 0 <= ans <= 3 and str(q.get("stem") or "").strip():
            qs.append({"stem": str(q["stem"]).strip(), "options": [str(o).strip() for o in opts], "answer": ans,
                       "explanation": str(q.get("explanation") or "").strip(), "trap": str(q.get("trap") or "").strip()[:12]})
    if len(qs) < 6:
        raise StudyError(f"쓸 수 있는 문제가 {len(qs)}개뿐이에요")
    terms = [t for t in (data.get("terms") or []) if isinstance(t, dict) and t.get("ko")]
    return {"concept_md": str(data["concept_md"]).strip(), "terms": terms[:16], "questions": qs}


def fill(api, vault, cid=None, limit=1, do_review=True):
    """내용 없는 장을 공부 순서대로 limit개 채운다. cid를 주면 그 장만(덮어쓰기)."""
    targets = [cid] if cid else [c for c in ORDER if not chapter_path(vault, c).is_file()][:max(0, int(limit))]
    done = []
    for c in targets:
        if c not in CHAPTERS:
            raise StudyError(f"없는 장이에요: {c}")
        data = _valid_chapter(api._run_claude_json(chapter_prompt(c), timeout=900, kind="study"))
        data.update({"id": c, "title": CHAPTERS[c]["title"], "domain": CHAPTERS[c]["domain"], "made": datetime.now().strftime("%Y-%m-%d %H:%M")})
        _write(chapter_path(vault, c), data)
        rec = {"chapter": c, "questions": len(data["questions"])}
        if do_review:  # 새로 만든 장은 바로 검수까지 거친다
            try:
                rec["review"] = review(api, vault, c)
            except Exception as e:  # noqa: BLE001 — 검수 실패는 다음 study review로 다시
                rec["review_error"] = str(e)[:160]
        done.append(rec)
    return {"filled": done, "remaining": sum(1 for c in ORDER if not chapter_path(vault, c).is_file())}


def review_prompt(cid, ch):
    c = CHAPTERS[cid]
    qs = [dict(i=i, **{k: q[k] for k in ("stem", "options", "answer", "explanation", "trap")}) for i, q in enumerate(ch["questions"])]
    return (
        "너는 ISACA CISA 문제 검수자이자 보안 감사 전문가다. 아래 [검수 대상]은 AI가 만든 한국어 학습 자료다. 학습자는 이걸로 실제 시험을 준비하므로 틀린 내용이 하나도 없어야 한다.\n"
        f"[장] {cid} {c['title']} (CISA {c['domain']})\n\n"
        "문제마다 다음을 확인한다: ① ISACA 감사인 관점에서 정답이 하나로 확정되는가(복수 정답·논쟁 여지 금지) ② answer 인덱스가 해설이 말하는 정답과 같은가 "
        "③ 오답 보기가 그럴듯하지만 분명히 틀렸는가 ④ 사실이 정확한가(COBIT 2019, ISO/IEC 27001:2022, NIST CSF 2.0, ISACA 감사 기준 용어) "
        "⑤ trap 단어가 실제로 문제에 쓰였는가 ⑥ 한국어가 자연스럽고 핵심 용어에 영어가 병기됐는가 ⑦ 같은 장 안에서 사실상 같은 문제가 반복되지 않는가.\n"
        "개념 본문은 사실 오류, 오해를 부르는 단정, 감사인 관점과 어긋나는 설명만 고친다(문체 손질 금지).\n\n"
        "아래 JSON 하나만 출력한다(코드블록·설명 금지). 고칠 것이 없으면 빈 배열.\n"
        '{"question_fixes": [{"i": 0, "problem": "무엇이 문제였나 한 문장", "stem": "...", "options": ["...", "...", "...", "..."], "answer": 0, "explanation": "...", "trap": ""}], '
        '"concept_fixes": [{"find": "본문에 그대로 있는 문장 일부", "replace": "고친 문장", "why": "한 문장"}], "verdict": "전체 평가 한 문장"}\n'
        "question_fixes는 고친 문제 전체(4지선다, 정답 하나)를 다시 적는다. 문제를 삭제하지 말고 고쳐 쓴다. 정답 위치는 원래 위치를 유지해도 된다.\n\n"
        f"[검수 대상]\n{json.dumps({'concept_md': ch['concept_md'], 'questions': qs}, ensure_ascii=False)}"
    )


def review(api, vault, cid):
    """장 하나를 검수해 고친다. 문제 번호(진도 기록의 id)는 유지하고, 고친 내역은 study/review-log.md에 남긴다."""
    ch = load_chapter(vault, cid)
    if not ch:
        raise StudyError(f"{cid}는 아직 내용이 없어요")
    data = api._run_claude_json(review_prompt(cid, ch), timeout=900, kind="study")
    if not isinstance(data, dict):
        raise StudyError("검수 응답이 이상해요")
    fixed, notes = [], []
    for f in data.get("question_fixes") or []:
        try:
            i = int(f.get("i"))
            q = _valid_chapter({"concept_md": "x" * 800, "questions": [f] * 6})["questions"][0]
        except (StudyError, TypeError, ValueError, AttributeError):
            continue
        if 0 <= i < len(ch["questions"]):
            old = ch["questions"][i]
            ch["questions"][i] = q
            fixed.append(i + 1)
            notes.append(f"- Q{i + 1} {str(f.get('problem') or '')[:160]} (정답 {'ABCD'[old['answer']]}→{'ABCD'[q['answer']]})")
    cfix = 0
    for f in data.get("concept_fixes") or []:
        find, rep_ = str((f or {}).get("find") or ""), str((f or {}).get("replace") or "")
        if len(find) >= 8 and rep_ and ch["concept_md"].count(find) == 1:
            ch["concept_md"] = ch["concept_md"].replace(find, rep_)
            cfix += 1
            notes.append(f"- 본문: {str(f.get('why') or '')[:160]}")
    ch["reviewed"] = {"at": datetime.now().strftime("%Y-%m-%d %H:%M"), "questions_fixed": fixed, "concept_fixed": cfix,
                      "verdict": str(data.get("verdict") or "")[:200]}
    _write(chapter_path(vault, cid), ch)
    log = root(vault) / "review-log.md"
    head = "" if log.is_file() else "# 공부방 검수 기록\n\n장별로 AI 검수가 고친 내역. 문제 번호는 그대로 유지된다.\n"
    with log.open("a", encoding="utf-8") as fh:
        fh.write(head + f"\n## {cid} {CHAPTERS[cid]['title']} · {ch['reviewed']['at']}\n{ch['reviewed']['verdict']}\n" + ("\n".join(notes) or "- 고칠 것 없음") + "\n")
    return {"chapter": cid, "questions_fixed": fixed, "concept_fixed": cfix, "verdict": ch["reviewed"]["verdict"]}


# ───────────────────────── 하루치 ─────────────────────────

def fetch_feeds(timeout=15):
    """보안 RSS에서 최근 기사 목록. 실패한 피드는 건너뛴다."""
    items = []
    for src, url in FEEDS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                tree = ET.fromstring(r.read())
        except Exception as e:  # noqa: BLE001 — 피드 하나가 죽어도 나머지로 간다
            items.append({"error": f"{src}: {e}"})
            continue
        for it in tree.iter("item"):
            t = (it.findtext("title") or "").strip()
            link = (it.findtext("link") or "").strip()
            raw = (it.findtext("pubDate") or it.findtext("{http://purl.org/dc/elements/1.1/}date") or "").strip()
            try:
                when = parsedate_to_datetime(raw).date().isoformat()
            except (TypeError, ValueError):
                when = raw[:10]
            desc = re.sub(r"<[^>]+>", " ", it.findtext("description") or "")
            if t and link:
                items.append({"source": src, "title": t, "url": link, "date": when, "summary": " ".join(desc.split())[:400]})
    return items


def daily_prompt(feed, unit, covered):
    return (
        "너는 16년차 CISO의 아침 공부를 준비하는 보안 코치다. 학습자는 현장 감각을 되살리려 하고, CISA를 한국어로 준비 중이다.\n"
        "아래 JSON 하나만 출력한다(코드블록·설명 금지).\n"
        '{"news": [{"i": 0, "title": "...", "summary": ["...", "...", "..."], "tech": "...", "ask": "...", "unit": 1}], '
        '"piece": {"title": "...", "lead": "...", "points": [{"h": "...", "body": "..."}], "views": {"CISSP": "...", "CISM": "...", "CISA": "..."}, "scene": {"title": "...", "steps": ["...", "...", "..."], "why": "..."}}}\n\n'
        "news 규칙: [기사 목록]에서 CISO 관점으로 의미 있는 기사 3개를 고른다(국내 1개 이상, 같은 사건 중복 금지). i는 목록 번호. "
        "title은 한국어 한 줄. summary는 기사 요약에 있는 사실만 3줄(없는 숫자·사실을 만들지 않는다). "
        "tech는 기술적으로 무슨 일인지 또는 지금 확인할 통제를 2문장(기사에 없는 세부는 일반 원리로만). "
        "ask는 '우리 회사라면?' 질문 한 문장. unit은 가장 가까운 실무감 단원 번호(1~11, 11은 AI 보안).\n"
        f"piece 규칙: 실무감 {unit['n']}단원 「{unit['title']}」({unit['map']})의 한 조각. 이미 다룬 제목 {json.dumps(covered[-12:], ensure_ascii=False)}와 겹치지 않는 주제 하나. "
        "lead는 한 줄 명제, points는 2~3개 핵심(용어는 한국어(영어) 병기, 각 2~3문장), views는 같은 주제를 세 자격이 보는 정답 기준 각 1~2문장, "
        "scene은 CISO 현장 장면 하나(가능하면 고른 뉴스와 연결, 실무 단계 3개, why는 이 단원과의 연결 한두 문장). 해요체, 가짜 수치 금지. "
        "11단원(AI 보안)이면 AI 거버넌스, 모델·데이터 위험(프롬프트 주입, 데이터 유출, 모델 공급망), AI 사고 대응을 다루고 views에 CISA 대신 AAISM 관점도 괜찮다. "
        "학습자의 목표는 AI 보안을 아는 CISO라서, 다른 단원에서도 자연스러우면 AI 관련 연결 한 줄을 넣는다.\n\n"
        f"[기사 목록]\n{json.dumps(feed, ensure_ascii=False)}"
    )


def _valid_daily(data, feed):
    if not isinstance(data, dict):
        raise StudyError("하루치 응답이 이상해요")
    news = []
    for n in data.get("news") or []:
        try:
            src = feed[int(n.get("i"))]
        except (TypeError, ValueError, IndexError, AttributeError):
            continue
        news.append({"title": str(n.get("title") or src["title"])[:120], "source": src["source"], "url": src["url"], "date": src["date"],
                     "summary": [str(s) for s in (n.get("summary") or [])][:3], "tech": str(n.get("tech") or ""), "ask": str(n.get("ask") or ""),
                     "unit": int(n.get("unit") or 0) if str(n.get("unit") or "").isdigit() else 0})
    piece = data.get("piece") if isinstance(data.get("piece"), dict) else None
    if not news and not piece:
        raise StudyError("뉴스도 실무 조각도 비었어요")
    return news[:3], piece


def make_daily(api, vault, day=None, force=False, feed=None):
    day = day or date.today()
    path = daily_path(vault, day)
    if path.is_file() and not force:
        return _read(path, {})
    p = load_progress(vault)
    field = p["field"]
    unit = UNITS[(int(field.get("unit") or 1) - 1) % len(UNITS)]
    covered = [c["title"] for c in field["covered"]]
    feed = feed if feed is not None else fetch_feeds()
    recent = [f for f in feed if "error" not in f and f.get("date", "") >= (day - timedelta(days=2)).isoformat()] or [f for f in feed if "error" not in f]
    recent = recent[:40]
    news, piece = _valid_daily(api._run_claude_json(daily_prompt(recent, unit, covered), timeout=600, kind="study"), recent)
    # 문제 5개: 오늘 다시 볼 오답 먼저, 나머지는 지금 공부하는 장(없으면 아무 장)
    qs = all_questions(vault)
    ids = [q for q in due_wrong(p, day) if q in qs][:3]
    cur = current_chapter(vault, p)
    pool = [q for q in qs.values() if q["id"] not in ids and (not cur or q["chapter"] == cur)] or [q for q in qs.values() if q["id"] not in ids]
    ids += [q["id"] for q in _pick(pool, 5 - len(ids), p, random.Random(day.toordinal()))]
    doc = {"date": day.isoformat(), "unit": unit["n"], "news": news, "piece": piece, "questions": ids, "chapter": cur,
           "made": datetime.now().strftime("%Y-%m-%d %H:%M")}
    _write(path, doc)
    if piece and piece.get("title"):
        field["covered"].append({"date": day.isoformat(), "unit": unit["n"], "title": str(piece["title"])[:80]})
        if sum(1 for c in field["covered"] if c["unit"] == unit["n"]) % PIECES_PER_UNIT == 0:
            field["unit"] = unit["n"] % len(UNITS) + 1
        save_progress(vault, p)
    return doc


# ───────────────────────── 화면용 묶음 ─────────────────────────

def overview(vault, today=None):
    today = today or date.today()
    p = load_progress(vault)
    qs = all_questions(vault)
    ch_stats, dom_stats = _stats(p, qs)
    daily = _read(daily_path(vault, today), None)
    if daily:
        daily = dict(daily, questions=[_public(qs[i]) for i in daily.get("questions", []) if i in qs])
        answered = {a["q"]: a["ok"] for a in p["attempts"] if a["at"].startswith(today.isoformat())}
        daily["answered"] = {i: answered[i] for i in [q["id"] for q in daily["questions"]] if i in answered}
    domains = []
    for d in DOMAINS:
        chs = []
        for c in d["chapters"]:
            ch = load_chapter(vault, c["id"])
            chs.append({"id": c["id"], "title": c["title"], "unit": c["unit"], "ready": bool(ch),
                        "questions": len((ch or {}).get("questions", [])), "read": p["read"].get(c["id"]), "stats": ch_stats.get(c["id"])})
        read_n = sum(1 for c in chs if c["read"])
        domains.append({"id": d["id"], "name": d["name"], "weight": d["weight"], "chapters": chs, "read": read_n,
                        "progress": round(100 * read_n / len(chs)), "stats": dom_stats.get(d["id"])})
    field = p["field"]
    unit_n = int(field.get("unit") or 1)
    return {
        "date": today.isoformat(), "daily": daily, "domains": domains,
        "units": [dict(u, now=u["n"] == unit_n, done=sum(1 for c in field["covered"] if c["unit"] == u["n"])) for u in UNITS],
        "field": {"unit": unit_n, "per_unit": PIECES_PER_UNIT, "covered": field["covered"][-8:]},
        "current_chapter": current_chapter(vault, p), "bank": len(qs), "chapters_ready": sum(1 for c in ORDER if chapter_path(vault, c).is_file()),
        "chapters_total": len(ORDER), "wrong_due": len(due_wrong(p, today)), "wrong_total": len(p["boxes"]),
        "streak": streak(p, today), "today_answered": sum(1 for a in p["attempts"] if a["at"].startswith(today.isoformat())),
        "mocks": p["mocks"][-5:], "readiness": readiness(p), "pace": pace(p, today),
        "reviewed": sum(1 for c in ORDER if (load_chapter(vault, c) or {}).get("reviewed")),
        "exam": {"name": "CISA", "questions": 150, "minutes": 240, "pass": "450 / 800", "language": "한국어 가능", "where": "PSI 시험장 또는 집에서 원격 감독"},
    }


def chapter_payload(vault, cid):
    ch = load_chapter(vault, cid)
    if not ch:
        raise StudyError(f"{cid}는 아직 내용이 준비되지 않았어요")
    p = load_progress(vault)
    c = CHAPTERS[cid]
    return {"id": cid, "title": c["title"], "domain": c["domain"], "unit": UNITS[c["unit"] - 1], "concept_md": ch["concept_md"],
            "terms": ch.get("terms", []), "questions": len(ch.get("questions", [])), "read": p["read"].get(cid)}


def glossary(vault):
    out = []
    for cid in ORDER:
        for t in (load_chapter(vault, cid) or {}).get("terms", []):
            out.append(dict(t, chapter=cid))
    return {"terms": out}


def save_news_note(api, vault, day, idx):
    doc = _read(daily_path(vault, day), None)
    try:
        n = (doc or {})["news"][int(idx)]
    except (KeyError, IndexError, TypeError, ValueError) as e:
        raise StudyError("저장할 뉴스를 못 찾았어요") from e
    body = "\n".join(["- " + s for s in n.get("summary", [])] + ["", "기술적으로: " + n.get("tech", ""), "", "우리 회사라면? " + n.get("ask", ""), "",
                      f"맥락: 공부방 {day.isoformat()} 보안 뉴스에서 저장"])
    meta = {"type": "source", "title": n["title"], "created": day.isoformat(), "tags": ["보안뉴스", "공부방"], "source": n["url"]}
    d = Path(vault) / "notes" / day.strftime("%Y") / day.strftime("%m")
    d.mkdir(parents=True, exist_ok=True)
    stem = api.slugify(n["title"])[:60] or "보안뉴스"
    path = d / f"{stem}.md"
    if not path.exists():
        api.write_note(path, meta, body + "\n")
    return {"ok": True, "path": str(path.relative_to(vault))}


def brief_line(vault, today=None):
    """아침 카톡 한 줄. 하루치가 없으면 빈 문자열."""
    today = today or date.today()
    doc = _read(daily_path(vault, today), None)
    if not doc:
        return ""
    unit = UNITS[(int(doc.get("unit") or 1) - 1) % len(UNITS)]
    return f"공부방: 뉴스 {len(doc.get('news', []))} · {unit['n']}단원 {unit['title']} · CISA {len(doc.get('questions', []))}문제"


def evening_part(vault, today=None):
    """저녁 마감 카톡에 붙일 (우선순위, 문장). 오늘 안 했으면 우선순위 1로 독려."""
    today = today or date.today()
    p = load_progress(vault)
    n = sum(1 for a in p["attempts"] if a["at"].startswith(today.isoformat()))
    if not n and today.isoformat() not in p["days"]:
        return (1, "공부방 오늘 아직이에요. 자기 전 3문제만 풀어요 (10분 버튼)")
    right = sum(1 for a in p["attempts"] if a["at"].startswith(today.isoformat()) and a["ok"])
    return (3, f"공부방 오늘 {n}문제 {round(100 * right / n) if n else 0}% · 연속 {streak(p, today)}일")


def weekly_report(vault, today=None):
    """월요일 주간 진도 카톡(≤200자)."""
    today = today or date.today()
    p = load_progress(vault)
    qs = all_questions(vault)
    week = [(today - timedelta(days=i)).isoformat() for i in range(1, 8)]
    att = [a for a in p["attempts"] if a["at"][:10] in week]
    days = sum(1 for d in p["days"] if d in week)
    pc = pace(p, today)
    _, dom = _stats(p, qs)
    weak = sorted([(v["rate"], k) for k, v in dom.items() if v["rate"] is not None])
    msg = (f"[공부방 주간] 지난주 {days}일 · {len(att)}문제 " + (f"{round(100 * sum(a['ok'] for a in att) / len(att))}%" if att else "") +
           f" / 장 {pc['chapters_done']}/{len(ORDER)}(계획 {pc['chapters_expected']}, {pc['status']}) / 목표 {pc['target']} D-{pc['d_day']}")
    if weak:
        msg += f" / 약점 {weak[0][1]} {weak[0][0]}%"
    if pc["per_week"]:
        msg += f" / 이번 주 {pc['per_week']}장"
    return msg[:200]


# ───────────────────────── HTTP ─────────────────────────

def handle_get(api, route, qs, vault):
    one = lambda k: (qs.get(k) or [""])[0]
    if route == "/api/study":
        return overview(vault)
    if route == "/api/study/chapter":
        return chapter_payload(vault, one("id"))
    if route == "/api/study/quiz":
        return make_quiz(vault, one("mode") or "quick", one("chapter") or None)
    if route == "/api/study/glossary":
        return glossary(vault)
    return None


def handle_post(api, route, body, vault):
    if route == "/api/study/answer":
        return record_answer(vault, body.get("id"), body.get("choice"), body.get("mode") or "practice")
    if route == "/api/study/read":
        return mark_read(vault, body.get("chapter"))
    if route == "/api/study/mock":
        return record_mock(vault, body.get("mode"), body.get("score", 0), body.get("total", 0))
    if route == "/api/study/note":
        return save_news_note(api, vault, date.today(), body.get("index"))
    return None
