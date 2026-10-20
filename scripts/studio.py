"""글 공방(studio) — 오늘의 키워드를 원본 글로, 원본을 고른 플랫폼 변환본으로 만들고 게시를 기록한다.

세컨드브레인 대시보드의 한 탭. 글 생성은 콘텐츠 허브(content/)의 기존 스킬을 헤드리스 Claude로
그대로 돌리고, 이 모듈은 입력·진행 상태·검수·게시 기록만 맡는다.

- 키워드: 볼트의 idea 노트(태그 "글감")
- 원본·변환본: content/writer/*.md 가 원본 데이터(파일명 규칙 YYYY-MM-DD-slug[-platform].md)
- 상태(글 묶음·게시 여부·작업 이력): 캐시 폴더의 studio.json 하나
- brain.py가 자기 모듈 객체(api)를 넘겨 호출한다(스크립트로 실행된 brain을 다시 import하지 않기 위해).
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import threading
import time
from datetime import date, datetime
from pathlib import Path

import agenda as agenda_mod  # cache_dir()

KEYWORD_TAG = "글감"
DEFAULT_CONTENT_DIR = Path.home() / "repo" / "project" / "content"
DEFAULT_CMD = ("claude -p --output-format stream-json --verbose --permission-mode acceptEdits "
               "--allowedTools Read,Write,Edit,Glob,Grep,WebSearch,WebFetch,"
               "Bash(python3:*),Bash(wc:*),Bash(grep:*),Bash(ls:*)")
JOB_TIMEOUT = 45 * 60  # 원본 작성(리서치 포함)은 10~25분 걸린다
MAX_JOBS_KEPT = 40

# 플랫폼 하드 리밋은 content/.claude/CLAUDE.md 「플랫폼 하드 리밋」 표와 같다.
# body는 권장 분량(벗어나면 경고), hard_max는 넘으면 실패.
PLATFORMS = {
    "brunch": {"label": "브런치", "title_max": 30, "subtitle_max": 40, "body": (2500, 3700),
               "plain": False, "tone": "평어체 서사. 짧은 단락과 「―」 구분선, 명사형 부제. 표·코드블록은 빼요."},
    "linkedin": {"label": "링크드인", "body": (1100, 1300), "plain": False,
                 "tone": "1인칭 사고 흐름 5단락. 불릿 나열 대신 발견→인사이트→다음 행동, 해시태그 5~7개."},
    "medium": {"label": "미디엄", "body": None, "plain": False, "korean": True,
               "tone": "한글 그대로, 원본 분량 100% 안팎. TL;DR과 Sources는 남기고 표도 써요."},
    "naver-blog": {"label": "네이버 블로그", "title_max": 30, "body": (2400, 3400), "plain": True,
                   "tone": "해요체 검색형 가이드. 마크다운은 전부 빼고 이모지는 괜찮아요."},
    "naver-cafe": {"label": "네이버 카페", "title_max": 30, "body": (600, 1500), "hard_max": 1500, "plain": True,
                   "tone": "해요체 초요약, 1,500자 하드 리밋. 마크다운은 전부 빼요."},
}
PLATFORM_ORDER = ["brunch", "linkedin", "medium", "naver-blog", "naver-cafe"]
FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-(.+?)(?:-(brunch|linkedin|medium|naver-blog|naver-cafe))?\.md$")
_MD_RESIDUE = [
    ("제목 기호 #", re.compile(r"^\s{0,3}#{1,6}\s", re.M)),
    ("구분선 ---", re.compile(r"^\s*-{3,}\s*$", re.M)),
    ("인용 >", re.compile(r"^\s*>", re.M)),
    ("코드블록 ```", re.compile(r"```")),
    ("링크 [](…)", re.compile(r"\[[^\]]+\]\([^)]+\)")),
]


class StudioError(Exception):
    pass


# ───────────────────────── 경로 ─────────────────────────

def content_dir(api):
    cfg = api.load_config()
    raw = os.environ.get("SECOND_BRAIN_STUDIO_DIR") or cfg.get("studio_content_dir") or str(DEFAULT_CONTENT_DIR)
    return Path(os.path.expanduser(raw))


def writer_dir(api):
    return content_dir(api) / "writer"


def _resolve(api, rel, write=False):
    """content 기준 상대경로를 검사해 실제 경로로. 읽기는 writer/ 아래 전체(백업 포함), 쓰기는 active(writer/ 바로 아래)만."""
    if not rel or not str(rel).endswith(".md"):
        raise StudioError("writer 폴더의 .md 파일만 다룰 수 있어요")
    root = Path(os.path.realpath(str(content_dir(api))))
    wdir = Path(os.path.realpath(str(root / "writer")))
    target = Path(os.path.realpath(str(root / rel)))
    if wdir not in target.parents:
        raise StudioError("writer 폴더 밖의 파일이에요")
    if write and target.parent != wdir:
        raise StudioError("백업 폴더의 글은 고칠 수 없어요(active 폴더만)")
    if not target.is_file():
        raise StudioError(f"파일이 없어요: {rel}")
    return target


def _rel(api, path):
    return Path(os.path.realpath(str(path))).relative_to(Path(os.path.realpath(str(content_dir(api))))).as_posix()


# ───────────────────────── 상태 파일 ─────────────────────────

_STATE_LOCK = threading.Lock()


def state_path():
    return agenda_mod.cache_dir() / "studio.json"


def load_state():
    p = state_path()
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        d = {}
    except ValueError:
        d = {}
    d.setdefault("pieces", {})
    d.setdefault("jobs", [])
    d.setdefault("hidden_keywords", [])
    return d


def save_state(d):
    p = state_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def _mutate(fn):
    with _STATE_LOCK:
        d = load_state()
        out = fn(d)
        save_state(d)
        return out


def _now():
    return datetime.now().isoformat(timespec="seconds")


# ───────────────────────── 검수 ─────────────────────────

def split_doc(api, text):
    meta, body = api.parse_frontmatter(text)
    return (meta or {}), body.strip()


def check_doc(api, text, platform=None, original_chars=None):
    """플랫폼 변환본(또는 원본)의 규칙 위반을 배지 목록으로. level: ok|warn|fail."""
    meta, body = split_doc(api, text)
    n = len(body)
    out = []
    if platform is None:  # 원본: 품질 기준(10,000자·Sources 15개)
        out.append({"key": "chars", "label": f"본문 {n:,}자", "level": "ok" if n >= 10000 else "warn",
                    "detail": "원본 기준 10,000자 이상"})
        m = re.search(r"^##\s*Sources\s*$(.*?)(?=^##\s|\Z)", body, re.M | re.S)
        # 각주([^1]: …)가 기본 형식이고, 번호(1.)·불릿(-) 목록도 센다
        k = len(re.findall(r"^\s*(?:\[\^\w+\]:|\d+\.|[-*]\s)", m.group(1), re.M)) if m else 0
        out.append({"key": "sources", "label": f"출처 {k}개", "level": "ok" if k >= 15 else "warn",
                    "detail": "Sources 15개 이상"})
        return out
    spec = PLATFORMS[platform]
    title = str(meta.get("title") or "")
    if spec.get("title_max"):
        tl = len(title)
        out.append({"key": "title", "label": f"제목 {tl}/{spec['title_max']}자",
                    "level": "fail" if (tl == 0 or tl > spec["title_max"]) else "ok", "detail": title})
    if spec.get("subtitle_max"):
        sub = str(meta.get("subtitle") or "")
        sl = len(sub)
        out.append({"key": "subtitle", "label": f"부제 {sl}/{spec['subtitle_max']}자",
                    "level": "fail" if (sl == 0 or sl > spec["subtitle_max"]) else "ok", "detail": sub})
    rng = spec.get("body")
    if spec.get("hard_max") and n > spec["hard_max"]:
        lvl, hint = "fail", f"하드 리밋 {spec['hard_max']:,}자 초과"
    elif rng:
        lvl = "ok" if rng[0] <= n <= rng[1] else "warn"
        hint = f"권장 {rng[0]:,}~{rng[1]:,}자"
    elif original_chars:
        ratio = n / original_chars
        lvl = "ok" if 0.75 <= ratio <= 1.3 else "warn"
        hint = f"원본 대비 {ratio:.0%} (권장 75~130%)"
    else:
        lvl, hint = "ok", "분량 기준 없음"
    out.append({"key": "chars", "label": f"본문 {n:,}자", "level": lvl, "detail": hint})
    bold = body.count("**") // 2 + title.count("**") // 2
    out.append({"key": "bold", "label": "볼드 ** 0건" if not bold else f"볼드 ** {bold}건",
                "level": "ok" if not bold else "fail", "detail": "변환본은 ** 전면 금지"})
    if spec.get("plain"):
        hits = [name for name, rx in _MD_RESIDUE if rx.search(body)]
        out.append({"key": "markdown", "label": "마크다운 없음" if not hits else "마크다운 " + ", ".join(hits),
                    "level": "ok" if not hits else "fail", "detail": "네이버는 마크다운을 렌더링하지 않아요"})
    if spec.get("korean"):
        ko = len(re.findall(r"[가-힣]", body))
        en = len(re.findall(r"[A-Za-z]", body))
        ratio = ko / max(1, ko + en)
        out.append({"key": "korean", "label": f"한글 비율 {ratio:.0%}", "level": "ok" if ratio >= 0.5 else "fail",
                    "detail": "미디엄도 한글로"})
    return out


_THUMB_HEAD = re.compile(r"^(#{2,3})\s*(?:Thumbnail Prompt\b[^\n]*|[^\n]*썸네일 프롬프트[^\n]*)$", re.M)


def _clean_prompt(t):
    """코드블록·인용(>)·「**Prompt**:」 표시와 감싼 따옴표를 떼고 붙여넣을 본문만."""
    m = re.search(r"```[\w-]*\s*\n(.*?)\n?```", t, re.S)
    t = (m.group(1) if m else t).strip()
    t = re.sub(r"^\s*>\s?", "", t, flags=re.M)
    t = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", t).strip()  # 「**Style**:」 같은 볼드 표시는 이미지 도구에 필요 없다
    t = re.sub(r"^Prompt\s*:\s*", "", t)
    if len(t) > 1 and t[0] in "\"“" and t[-1] in "\"”":
        t = t[1:-1]
    return t.strip()


def thumbnail_options(body):
    """썸네일 프롬프트 섹션 → [{label, prompt}]. 「### Option 1 …」로 나뉘면 안마다, 아니면 한 덩어리.
    제목 변형: 「## Thumbnail Prompt」「## Thumbnail Prompt (AI Image Generation)」「### DALL-E 3 썸네일 프롬프트」."""
    h = _THUMB_HEAD.search(body)
    if not h:
        return []
    rest = body[h.end():]
    # ## 제목이면 다음 ## 까지(안의 ### 는 안 나누기), ### 제목이면 다음 제목이나 --- 까지
    stop = re.search(r"^##\s" if len(h.group(1)) == 2 else r"^(?:#{2,3}\s|-{3,}\s*$)", rest, re.M)
    sec = (rest[:stop.start()] if stop else rest).strip()
    parts = re.split(r"^#{3,4}\s+(.+)$", sec, flags=re.M) if len(h.group(1)) == 2 else [sec]
    if len(parts) == 1:
        t = _clean_prompt(sec)
        return [{"label": "", "prompt": t}] if t else []
    out = []
    for label, chunk in zip(parts[1::2], parts[2::2]):
        t = _clean_prompt(chunk)
        if t:
            out.append({"label": label.strip(), "prompt": t})
    return out


def thumbnail_prompt(body):
    opts = thumbnail_options(body)
    return opts[0]["prompt"] if opts else ""


def copy_text(api, text, platform):
    """게시판에 붙여넣을 본문: 프론트매터를 떼고, 원본 제목(H1)은 제목 칸에 따로 넣으니 뺀다."""
    _, body = split_doc(api, text)
    if platform in ("naver-blog", "naver-cafe", "brunch", "linkedin"):
        body = re.sub(r"\A#\s+.+\n+", "", body)
    return body.strip() + "\n"


# ───────────────────────── 이력(파일 스캔) ─────────────────────────

def scan_writer(api):
    """writer/ 전체(백업 포함)를 슬러그별로 묶는다. 원본 제목은 원본 파일 프론트매터에서."""
    wdir = writer_dir(api)
    groups = {}
    if not wdir.is_dir():
        return []
    for p in sorted(wdir.rglob("*.md")):
        m = FILE_RE.match(p.name)
        if not m:
            continue
        day, slug, plat = m.group(1), m.group(2), m.group(3)
        key = f"{day}-{slug}"
        g = groups.setdefault(key, {"stem": key, "date": day, "slug": slug, "original": None, "title": "",
                                    "platforms": {}, "backup": False})
        rel = _rel(api, p)
        if "/" in rel[len("writer/"):]:
            g["backup"] = True
        if plat:
            g["platforms"][plat] = rel
        else:
            g["original"] = rel
            try:  # 프론트매터는 앞쪽에 있다. 4,000자 안에 안 닫히면 제목 없이 슬러그로 보여 준다
                meta, _ = api.parse_frontmatter(p.read_text(encoding="utf-8", errors="replace")[:4000])
                g["title"] = str((meta or {}).get("title") or "")
            except OSError as e:
                api.log(f"studio: 원본을 읽지 못함 {p.name}: {e}")
    out = list(groups.values())
    out.sort(key=lambda g: g["stem"], reverse=True)
    return out


def history(api, groups, pieces):
    """히트맵(날짜별 만든 파일 수)·플랫폼별 합계·게시 기록 합계. 지어낸 수치 없이 파일과 상태에서만."""
    per_day = {}
    per_platform = {k: 0 for k in PLATFORM_ORDER}
    originals = 0
    for g in groups:
        files = (1 if g["original"] else 0) + len(g["platforms"])
        per_day[g["date"]] = per_day.get(g["date"], 0) + files
        originals += 1 if g["original"] else 0
        for k in g["platforms"]:
            per_platform[k] = per_platform.get(k, 0) + 1
    posted = {k: 0 for k in PLATFORM_ORDER}
    for pc in pieces.values():
        for k, v in (pc.get("posts") or {}).items():
            if v.get("posted"):
                posted[k] = posted.get(k, 0) + 1
    return {"per_day": per_day, "per_platform": per_platform, "posted": posted,
            "originals": originals, "pieces": len(groups)}


# ───────────────────────── 키워드 ─────────────────────────

def list_keywords(api, vault, state):
    if vault is None or not api.vault_exists(vault):
        return []
    hidden = set(state.get("hidden_keywords") or [])
    used = {}
    for pid, pc in state["pieces"].items():
        for k in pc.get("keywords") or []:
            used.setdefault(k, []).append(pid)
    out = []
    for n in api.load_notes(vault):
        if n.type != "idea" or KEYWORD_TAG not in n.tags or n.rel in hidden:
            continue
        memo = re.sub(r"\A#\s+.+\n*", "", n.body.strip()).strip()
        out.append({"id": n.rel, "title": n.title, "created": n.created, "memo": memo[:400],
                    "source": str(n.meta.get("source") or ""), "used_by": used.get(n.rel, [])})
    out.sort(key=lambda k: (k["created"], k["id"]), reverse=True)
    return out


def add_keyword(api, vault, text, memo="", link=""):
    text = str(text or "").strip()
    if not text:
        raise StudioError("키워드가 비었어요")
    if len(text) > 120:
        raise StudioError("키워드는 120자 이내로 적어 주세요(길면 메모 칸에)")
    link = str(link or "").strip()
    if link and not re.match(r"^https?://", link):
        raise StudioError("링크는 http(s)로 시작해야 해요")
    body = f"# {text}\n\n{str(memo or '').strip()}\n" if memo else None
    path = api.create_note(vault, "idea", text, tags=[KEYWORD_TAG], source=link or None, body=body)
    return Path(path).relative_to(vault).as_posix()


def hide_keyword(note_id):
    def f(d):
        if note_id not in d["hidden_keywords"]:
            d["hidden_keywords"].append(note_id)
        return True
    return _mutate(f)


# ───────────────────────── 글 묶음(piece) ─────────────────────────

def _new_id():
    return "p" + datetime.now().strftime("%Y%m%d%H%M%S") + f"{int(time.time() * 1000) % 1000:03d}"


def create_piece(title, keywords=None, brief="", original=None):
    title = str(title or "").strip()
    if not title:
        raise StudioError("글 제목(주제)이 비었어요")

    def f(d):
        pid = _new_id()
        while pid in d["pieces"]:
            pid = pid + "x"
        d["pieces"][pid] = {"id": pid, "title": title[:160], "keywords": list(keywords or []),
                            "brief": str(brief or "")[:4000], "original": original, "posts": {},
                            "created": _now(), "updated": _now()}
        return d["pieces"][pid]
    return _mutate(f)


def import_piece(api, rel):
    target = _resolve(api, rel)
    m = FILE_RE.match(target.name)
    if not m or m.group(3):
        raise StudioError("원본 파일만 가져올 수 있어요(플랫폼 접미사 없는 파일)")
    rel = _rel(api, target)
    st = load_state()
    for pc in st["pieces"].values():
        if pc.get("original") == rel:
            return pc
    for j in st["jobs"]:  # 원본 작성 작업이 쓰는 중인 파일을 따로 가져오면 같은 글이 둘이 된다
        if j.get("kind") == "write" and j.get("status") in ("queued", "running") and j.get("started"):
            if target.stat().st_mtime >= datetime.fromisoformat(j["started"]).timestamp() - 5:
                raise StudioError("원본 작성 작업이 쓰는 중인 파일이에요. 작업이 끝나면 자동으로 붙어요")
    meta, _ = split_doc(api, target.read_text(encoding="utf-8", errors="replace"))
    return create_piece(str(meta.get("title") or m.group(2)), original=rel)


def set_posted(piece_id, platform, posted, url=""):
    if platform not in PLATFORMS:
        raise StudioError(f"모르는 플랫폼: {platform}")
    url = str(url or "").strip()
    if url and not re.match(r"^https?://", url):
        raise StudioError("게시 URL은 http(s)로 시작해야 해요")

    def f(d):
        pc = d["pieces"].get(piece_id)
        if not pc:
            raise StudioError("없는 글이에요")
        pc.setdefault("posts", {})[platform] = {"posted": bool(posted), "url": url,
                                                "at": _now() if posted else ""}
        pc["updated"] = _now()
        return pc
    return _mutate(f)


def delete_piece(piece_id):
    def f(d):
        if piece_id not in d["pieces"]:
            raise StudioError("없는 글이에요")
        if any(j.get("piece_id") == piece_id and j.get("status") in ("queued", "running") for j in d["jobs"]):
            raise StudioError("작업이 도는 중이라 지울 수 없어요")
        d["pieces"].pop(piece_id)
        return True
    return _mutate(f)


def piece_stage(api, pc):
    """칸반 단계: 원본 작성 중 → 원본 → 변환 → 게시."""
    files = platform_files(api, pc)
    posts = pc.get("posts") or {}
    if not pc.get("original"):
        return "drafting"
    if any(v.get("posted") for v in posts.values()):
        return "posted"
    if files:
        return "adapted"
    return "original"


def platform_files(api, pc):
    orig = pc.get("original")
    if not orig:
        return {}
    stem = orig[:-3]
    root = content_dir(api)
    return {k: f"{stem}-{k}.md" for k in PLATFORM_ORDER if (root / f"{stem}-{k}.md").is_file()}


def piece_detail(api, pid):
    st = load_state()
    pc = st["pieces"].get(pid)
    if not pc:
        raise StudioError("없는 글이에요")
    pc = dict(pc)
    pc["stage"] = piece_stage(api, pc)
    pc["files"] = platform_files(api, pc)
    pc["jobs"] = [j for j in st["jobs"] if j.get("piece_id") == pid][-8:]
    if pc.get("original"):
        try:
            text = _resolve(api, pc["original"]).read_text(encoding="utf-8", errors="replace")
            meta, body = split_doc(api, text)
            pc["original_chars"] = len(body)
            pc["thumbnail"] = thumbnail_prompt(body)
            pc["thumbnails"] = thumbnail_options(body)
        except StudioError:
            pc["original_missing"] = True
    return pc


def read_file(api, rel, platform=None):
    target = _resolve(api, rel)
    text = target.read_text(encoding="utf-8", errors="replace")
    meta, body = split_doc(api, text)
    orig_chars = None
    if platform:
        m = FILE_RE.match(target.name)
        orig = target.parent / f"{m.group(1)}-{m.group(2)}.md" if m else None
        if orig and orig.is_file():
            orig_chars = len(split_doc(api, orig.read_text(encoding="utf-8", errors="replace"))[1])
    return {"path": _rel(api, target), "text": text, "title": str(meta.get("title") or ""),
            "subtitle": str(meta.get("subtitle") or ""), "chars": len(body),
            "checks": check_doc(api, text, platform, orig_chars),
            "copy": copy_text(api, text, platform) if platform else body + "\n",
            "editable": target.parent == Path(os.path.realpath(str(writer_dir(api))))}


def save_file(api, rel, text):
    target = _resolve(api, rel, write=True)
    text = str(text or "")
    if len(text) > 400_000:
        raise StudioError("너무 길어요")
    if not text.lstrip().startswith("---"):
        raise StudioError("프론트매터(---)가 사라졌어요. 저장하지 않았어요")
    target.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    return {"ok": True, "path": _rel(api, target)}


# ───────────────────────── 작업(헤드리스 Claude) ─────────────────────────

def write_prompt(pc, keywords):
    lines = [f"/blog-write {pc['title']}", ""]
    if keywords:
        lines.append("오늘 모아 둔 키워드와 메모(글감):")
        for k in keywords:
            src = f" ({k['source']})" if k.get("source") else ""
            memo = f" — {k['memo'][:300]}" if k.get("memo") else ""
            lines.append(f"- {k['title']}{src}{memo}")
        lines.append("")
    if pc.get("brief"):
        lines += ["작성자 메모:", pc["brief"], ""]
    lines += [
        "글 공방(웹)에서 보낸 요청이에요. 원본 1편만 writer/에 쓰고 플랫폼 변환·발행은 하지 마세요.",
        "질문하지 말고 끝까지 진행하세요. 확인이 필요한 결정은 합리적으로 정하고 마지막에 한 줄로 적어 주세요.",
        "마지막 줄에 반드시 `STUDIO_FILE: writer/파일명.md` 형식으로 만든 파일 경로를 적어 주세요.",
    ]
    return "\n".join(lines)


def adapt_prompt(pc, platforms, note=""):
    stem = pc["original"][:-3]
    targets = ", ".join(platforms)
    lines = [
        f"콘텐츠 허브의 플랫폼 변환을 해 주세요. 원본: {pc['original']}",
        f"이번에 만들 플랫폼: {targets} — 이 목록 밖의 플랫폼 파일은 만들지도 고치지도 마세요.",
        "",
        "규칙:",
        "- .claude/CLAUDE.md 「플랫폼 하드 리밋」과 「변환 후 자체 검증 체크리스트」를 먼저 읽고 따르세요.",
        "- 플랫폼마다 .claude/skills/blog/platform-adapt/references/{플랫폼}-rules.md 를 읽고, writer/에서 같은 플랫폼의 최근 변환본 1편을 읽어 톤과 구조를 맞추세요.",
        "- 플랫폼마다 글 색깔이 달라요. 한 변환본을 다른 플랫폼에 길이만 바꿔 복사하지 마세요.",
        "- 변환본 전체에 `**` 0건. 네이버 계열은 마크다운 전면 금지. 미디엄도 한글.",
        "- 자동 발행(카페 API, 블로그 발행 스크립트)은 절대 실행하지 마세요. 사람이 직접 옮겨요.",
        "",
        "출력 파일:",
    ]
    lines += [f"- {stem}-{p}.md" for p in platforms]
    if note:
        lines += ["", "작성자 추가 요청:", str(note)[:2000]]
    lines += ["", "질문하지 말고 끝까지 진행하세요.",
              "마지막 줄에 `STUDIO_FILES: 경로1, 경로2` 형식으로 만든 파일을 적어 주세요."]
    return "\n".join(lines)


def _step_from_event(ev):
    """stream-json 한 줄 → 사람이 읽을 진행 단계(없으면 None)."""
    t = ev.get("type")
    if t == "assistant":
        steps = []
        for c in (ev.get("message") or {}).get("content") or []:
            if c.get("type") == "tool_use":
                inp = c.get("input") or {}
                hint = inp.get("query") or inp.get("file_path") or inp.get("pattern") or inp.get("url") \
                    or inp.get("command") or inp.get("description") or ""
                name = c.get("name") or "도구"
                steps.append(f"{name} · {str(hint)[:110]}" if hint else name)
            elif c.get("type") == "text" and str(c.get("text") or "").strip():
                steps.append("✎ " + str(c["text"]).strip().splitlines()[0][:140])
        return steps or None
    return None


class JobRunner:
    """한 번에 하나씩(순서대로) 헤드리스 Claude 작업을 돌린다. 상태는 studio.json에 남긴다."""

    def __init__(self, api):
        self.api = api
        self.lock = threading.Lock()
        self.procs = {}
        self.queue = []
        self.worker = None
        self._recover()

    def _recover(self):
        def f(d):
            for j in d["jobs"]:
                if j.get("status") in ("queued", "running"):
                    j["status"], j["error"], j["ended"] = "stopped", "서버가 다시 시작돼 중단됐어요", _now()
        _mutate(f)

    def submit(self, kind, piece_id, prompt, platforms=None):
        cfg = self.api.load_config()
        self.api._claude_check_cap(cfg)

        def f(d):
            if any(j.get("piece_id") == piece_id and j.get("status") in ("queued", "running") for j in d["jobs"]):
                raise StudioError("이 글은 이미 작업 중이에요")
            job = {"id": "j" + datetime.now().strftime("%Y%m%d%H%M%S%f")[:-3], "kind": kind, "piece_id": piece_id,
                   "platforms": list(platforms or []), "status": "queued", "created": _now(), "started": "",
                   "ended": "", "steps": [], "error": "", "result": ""}
            d["jobs"].append(job)
            d["jobs"] = d["jobs"][-MAX_JOBS_KEPT:]
            return job
        job = _mutate(f)
        with self.lock:
            self.queue.append((job["id"], prompt))
            if not self.worker or not self.worker.is_alive():
                self.worker = threading.Thread(target=self._loop, daemon=True)
                self.worker.start()
        return job

    def stop(self, job_id):
        with self.lock:
            self.queue = [q for q in self.queue if q[0] != job_id]
            proc = self.procs.get(job_id)
        if proc:
            proc.terminate()
        self._update(job_id, lambda j: j.update(status="stopped", ended=_now(), error="사용자가 멈췄어요")
                     if j["status"] in ("queued", "running") else None)
        return True

    def _update(self, job_id, fn):
        def f(d):
            for j in d["jobs"]:
                if j["id"] == job_id:
                    fn(j)
                    return j
            return None
        return _mutate(f)

    def _loop(self):
        while True:
            with self.lock:
                if not self.queue:
                    return
                job_id, prompt = self.queue.pop(0)
            self._run(job_id, prompt)

    def _cmd(self):
        cfg = self.api.load_config()
        return shlex.split(os.environ.get("SECOND_BRAIN_STUDIO_CMD") or cfg.get("studio_cmd") or DEFAULT_CMD)

    def _run(self, job_id, prompt):
        st = load_state()
        job = next((j for j in st["jobs"] if j["id"] == job_id), None)
        if not job or job["status"] != "queued":
            return
        self._update(job_id, lambda j: j.update(status="running", started=_now()))
        t0 = time.time()
        root_prefix = os.path.realpath(str(content_dir(self.api))) + os.sep
        result, err, ok = "", "", False
        pending = []
        last_flush = 0.0
        try:
            proc = subprocess.Popen(self._cmd(), cwd=str(content_dir(self.api)), stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        except OSError as e:
            self._finish(job_id, False, "", f"Claude를 실행하지 못했어요: {e}", t0, prompt)
            return
        with self.lock:
            self.procs[job_id] = proc
        timer = threading.Timer(JOB_TIMEOUT, proc.kill)
        timer.start()
        try:
            proc.stdin.write(prompt)
            proc.stdin.close()
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    pending.append(line[:160])
                    result += line + "\n"
                    ev = None
                if isinstance(ev, dict):
                    if ev.get("type") == "result":
                        result = str(ev.get("result") or "")
                        if ev.get("is_error"):
                            err = result[:300] or "Claude가 오류로 끝났어요"
                    else:  # 진행 로그는 content 기준 상대 경로로 짧게
                        pending.extend(x.replace(root_prefix, "") for x in (_step_from_event(ev) or []))
                if pending and time.time() - last_flush > 1.0:
                    batch, pending = pending, []
                    self._update(job_id, lambda j, b=batch: j.update(steps=(j["steps"] + b)[-60:]))
                    last_flush = time.time()
            proc.wait()
            stderr = proc.stderr.read()
            if pending:
                self._update(job_id, lambda j, b=pending: j.update(steps=(j["steps"] + b)[-60:]))
            if proc.returncode != 0 and not err:
                err = (stderr or "").strip()[:300] or f"종료 코드 {proc.returncode}"
            ok = not err
        finally:
            timer.cancel()
            with self.lock:
                self.procs.pop(job_id, None)
        self._finish(job_id, ok, result, err, t0, prompt)

    def _finish(self, job_id, ok, result, err, t0, prompt):
        api = self.api
        try:
            api.claude_usage_log("studio", ok, (time.time() - t0) * 1000, len(prompt), len(result))
        except OSError as e:
            api.log(f"studio 사용량 기록 실패: {e}")

        def f(d):
            j = next((x for x in d["jobs"] if x["id"] == job_id), None)
            if not j:
                return None
            if j["status"] == "stopped":
                return j
            j.update(status="done" if ok else "failed", ended=_now(), error=err, result=result[-2000:])
            pc = d["pieces"].get(j["piece_id"])
            if ok and pc and j["kind"] == "write":
                rel = _found_original(api, result, j.get("started") or j["created"])
                if rel:
                    pc["original"] = rel
                    pc["updated"] = _now()
                else:
                    j["status"], j["error"] = "failed", "원본 파일을 찾지 못했어요(STUDIO_FILE 줄 없음)"
            if ok and pc and j["kind"] == "adapt":
                missing = _missing_variants(api, pc, j.get("platforms") or [], j.get("started") or j["created"])
                if len(missing) == len(j.get("platforms") or []):
                    j["status"], j["error"] = "failed", "변환본 파일이 하나도 생기지 않았어요"
                elif missing:
                    j["error"] = "일부만 만들어졌어요. 빠진 곳: " + ", ".join(PLATFORMS[k]["label"] for k in missing)
                pc["updated"] = _now()
            return j
        _mutate(f)


def _missing_variants(api, pc, platforms, since_iso):
    """이번 작업에서 새로 쓰이거나 고쳐지지 않은 플랫폼 파일 목록."""
    try:
        since = datetime.fromisoformat(since_iso).timestamp() - 5
    except ValueError:
        since = 0
    root, stem = content_dir(api), pc["original"][:-3]
    out = []
    for k in platforms:
        p = root / f"{stem}-{k}.md"
        if not p.is_file() or p.stat().st_mtime < since:
            out.append(k)
    return out


def _found_original(api, result, since_iso):
    m = re.findall(r"STUDIO_FILE:\s*`?([^\s`]+\.md)", result or "")
    for cand in reversed(m):
        try:
            target = _resolve(api, cand)
            fm = FILE_RE.match(target.name)
            if fm and not fm.group(3):
                return _rel(api, target)
        except StudioError:
            continue
    # 줄이 없으면 작업 시작 뒤 생긴 원본 파일(active)을 찾는다
    wdir = writer_dir(api)
    try:
        since = datetime.fromisoformat(since_iso).timestamp() - 5
    except ValueError:
        return None
    news = [p for p in wdir.glob("*.md") if p.stat().st_mtime >= since
            and FILE_RE.match(p.name) and not FILE_RE.match(p.name).group(3)]
    if len(news) == 1:
        return _rel(api, news[0])
    return None


# ───────────────────────── API 진입점 ─────────────────────────

def overview(api, vault):
    st = load_state()
    groups = scan_writer(api)
    pieces = []
    for pc in st["pieces"].values():
        x = dict(pc)
        x["stage"] = piece_stage(api, pc)
        x["files"] = list(platform_files(api, pc).keys())
        x["running"] = any(j.get("piece_id") == pc["id"] and j.get("status") in ("queued", "running")
                           for j in st["jobs"])
        pieces.append(x)
    pieces.sort(key=lambda p: p.get("updated") or "", reverse=True)
    imported = {pc.get("original") for pc in st["pieces"].values()}
    writing = [datetime.fromisoformat(j["started"]).timestamp() - 5 for j in st["jobs"]
               if j.get("kind") == "write" and j.get("status") in ("queued", "running") and j.get("started")]
    root = content_dir(api)

    def in_progress(rel):
        if not writing or not rel:
            return False
        try:
            return (root / rel).stat().st_mtime >= min(writing)
        except OSError:
            return False
    recent = [{"stem": g["stem"], "date": g["date"], "title": g["title"] or g["slug"], "original": g["original"],
               "platforms": sorted(g["platforms"].keys(), key=PLATFORM_ORDER.index), "backup": g["backup"],
               "imported": g["original"] in imported, "writing": in_progress(g["original"])}
              for g in groups[:40]]
    running = [j for j in st["jobs"] if j.get("status") in ("queued", "running")]
    return {"today": date.today().isoformat(), "content_dir": str(content_dir(api)),
            "content_ok": writer_dir(api).is_dir(),
            "platforms": [dict(PLATFORMS[k], key=k) for k in PLATFORM_ORDER],
            "keywords": list_keywords(api, vault, st), "pieces": pieces, "recent": recent,
            "history": history(api, groups, st["pieces"]), "running": running}


def handle_get(api, route, qs, vault):
    one = lambda k: (qs.get(k) or [""])[0]  # noqa: E731
    if route == "/api/studio":
        return overview(api, vault)
    if route == "/api/studio/piece":
        return piece_detail(api, one("id"))
    if route == "/api/studio/file":
        plat = one("platform") or None
        if plat and plat not in PLATFORMS:
            raise StudioError(f"모르는 플랫폼: {plat}")
        return read_file(api, one("path"), plat)
    if route == "/api/studio/job":
        st = load_state()
        j = next((x for x in st["jobs"] if x["id"] == one("id")), None)
        if not j:
            raise StudioError("없는 작업이에요")
        return j
    return None


def handle_post(api, route, body, vault, runner):
    if route == "/api/studio/keyword":
        return {"ok": True, "id": add_keyword(api, vault, body.get("text"), body.get("memo", ""), body.get("link", ""))}
    if route == "/api/studio/keyword-hide":
        return {"ok": hide_keyword(str(body.get("id") or ""))}
    if route == "/api/studio/piece":
        kws = [str(k) for k in body.get("keywords") or []][:12]
        pc = create_piece(body.get("title"), kws, body.get("brief", ""))
        if body.get("write", True):
            st = load_state()
            klist = [k for k in list_keywords(api, vault, st) if k["id"] in kws]
            runner.submit("write", pc["id"], write_prompt(pc, klist))
        return {"ok": True, "piece": pc}
    if route == "/api/studio/import":
        return {"ok": True, "piece": import_piece(api, str(body.get("path") or ""))}
    if route == "/api/studio/adapt":
        pid = str(body.get("id") or "")
        plats = [p for p in PLATFORM_ORDER if p in (body.get("platforms") or [])]
        if not plats:
            raise StudioError("변환할 플랫폼을 하나 이상 골라 주세요")
        pc = load_state()["pieces"].get(pid)
        if not pc:
            raise StudioError("없는 글이에요")
        if not pc.get("original"):
            raise StudioError("원본이 아직 없어요")
        _resolve(api, pc["original"])
        job = runner.submit("adapt", pid, adapt_prompt(pc, plats, body.get("note", "")), plats)
        return {"ok": True, "job": job}
    if route == "/api/studio/rewrite":
        pid = str(body.get("id") or "")
        pc = load_state()["pieces"].get(pid)
        if not pc:
            raise StudioError("없는 글이에요")
        if pc.get("original"):
            raise StudioError("이미 원본이 있어요")
        st = load_state()
        klist = [k for k in list_keywords(api, vault, st) if k["id"] in (pc.get("keywords") or [])]
        return {"ok": True, "job": runner.submit("write", pid, write_prompt(pc, klist))}
    if route == "/api/studio/stop":
        return {"ok": runner.stop(str(body.get("id") or ""))}
    if route == "/api/studio/posted":
        return {"ok": True, "piece": set_posted(str(body.get("id") or ""), str(body.get("platform") or ""),
                                                bool(body.get("posted")), body.get("url", ""))}
    if route == "/api/studio/save":
        return save_file(api, str(body.get("path") or ""), body.get("text"))
    if route == "/api/studio/delete":
        return {"ok": delete_piece(str(body.get("id") or ""))}
    return None
