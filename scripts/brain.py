#!/usr/bin/env python3
"""세컨드브레인 코어 CLI — 마크다운 볼트 관리 (Python 3.9+ 표준 라이브러리만 사용).

데이터는 stdout, 로그는 stderr. 종료 코드: 0 성공, 2 입력 오류, 3 볼트 없음.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agenda as agenda_mod  # noqa: E402 - 일정 어댑터(같은 폴더)
import apple_notes as apple_notes_mod  # noqa: E402 - 애플 메모 어댑터(JXA, 읽기 전용)
import mailer as mail_mod  # noqa: E402 - 메일 어댑터(IMAP 읽기 전용, 기본 꺼짐)
import reminders as reminders_mod  # noqa: E402 - 미리알림 어댑터(맥 미리알림, 읽기 전용, 기본 꺼짐)
import weather as weather_mod  # noqa: E402 - 날씨 어댑터(읽기 전용, 실패해도 일정 표시는 막지 않음)

VERSION = "0.1.0"

EXIT_OK = 0
EXIT_INPUT = 2
EXIT_NO_VAULT = 3

NOTE_TYPES = ("note", "idea", "source", "meeting", "event", "journal")
ALL_TYPES = NOTE_TYPES + ("decision", "project", "person")
DECISION_STATUSES = ("open", "decided", "superseded")
SKIP_FILES = {"BRAIN.md", "inbox.md"}
SKIP_DIRS = {".git", ".obsidian", ".trash", "node_modules", "_demo"}

DEFAULT_CONFIG = {"vault": "~/brain", "git_autocommit": False, "index_head": 40}

AUTO_BEGIN = "<!-- brain:auto:begin -->"
AUTO_END = "<!-- brain:auto:end -->"

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
WIKILINK_RE = re.compile(r"\[\[([^\[\]]+?)\]\]")


class BrainError(Exception):
    """사용자에게 보여줄 오류. code는 프로세스 종료 코드."""

    def __init__(self, message, code=EXIT_INPUT):
        super().__init__(message)
        self.code = code


def log(msg):
    print(msg, file=sys.stderr)


# ---------------------------------------------------------------------------
# 프론트매터 (YAML 부분집합) 파싱/직렬화
# ---------------------------------------------------------------------------

def _unquote(s):
    """따옴표 문자열 해제. 따옴표가 없으면 그대로(공백 제거)."""
    s = s.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        out, i, inner = [], 0, s[1:-1]
        esc = {"n": "\n", "t": "\t", '"': '"', "\\": "\\", "/": "/"}
        while i < len(inner):
            c = inner[i]
            if c == "\\" and i + 1 < len(inner):
                out.append(esc.get(inner[i + 1], "\\" + inner[i + 1]))
                i += 2
                continue
            out.append(c)
            i += 1
        return "".join(out)
    if len(s) >= 2 and s[0] == "'" and s[-1] == "'":
        return s[1:-1].replace("''", "'")
    return s


def _strip_comment(s):
    """따옴표 밖의 ' #' 주석 제거."""
    quote, escaped = None, False
    for i, c in enumerate(s):
        if quote:
            if escaped:
                escaped = False
            elif c == "\\" and quote == '"':
                escaped = True
            elif c == quote:
                quote = None
        elif c in "\"'" and (i == 0 or s[i - 1] in " [,"):
            quote = c
        elif c == "#" and i > 0 and s[i - 1] in " \t":
            return s[:i].rstrip()
    return s


def _split_inline_list(s):
    """'[a, "b, c", d]' 내부를 쉼표 기준으로 분리(따옴표 존중)."""
    items, buf, quote = [], [], None
    for c in s:
        if quote:
            buf.append(c)
            if c == quote:
                quote = None
        elif c in "\"'":
            quote = c
            buf.append(c)
        elif c == ",":
            items.append("".join(buf))
            buf = []
        else:
            buf.append(c)
    if "".join(buf).strip():
        items.append("".join(buf))
    return [_unquote(x) for x in items if x.strip() != ""]


def _parse_scalar_or_inline(raw):
    raw = _strip_comment(raw.strip())
    if raw.startswith("[") and raw.endswith("]"):
        return _split_inline_list(raw[1:-1])
    return _unquote(raw)


def _indent(line):
    return len(line) - len(line.lstrip(" "))


def parse_frontmatter(text):
    """마크다운 텍스트 → (meta dict, body). 프론트매터 없으면 ({}, text)."""
    text = text.lstrip("\ufeff").replace("\r\n", "\n")
    if not text.startswith("---\n"):
        return {}, text
    lines = text.split("\n")
    end = None
    for i in range(1, len(lines)):
        if lines[i].rstrip() in ("---", "..."):
            end = i
            break
    if end is None:
        return {}, text
    meta = _parse_block(lines[1:end])
    body = "\n".join(lines[end + 1:])
    if body.startswith("\n"):
        body = body[1:]
    return meta, body


def _parse_block(lines):
    meta = {}
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        m = re.match(r"^(\s*)([^:\s][^:]*?)\s*:(?:\s+(.*)|\s*)$", line)
        if not m or _indent(line) > 0:
            i += 1  # 해석 불가 줄은 무시(관대하게)
            continue
        key, rest = m.group(2).strip(), (m.group(3) or "").strip()
        i += 1
        if rest in ("|", ">", "|-", ">-", "|+", ">+"):
            buf = []
            while i < n and (not lines[i].strip() or _indent(lines[i]) > 0):
                buf.append(lines[i].strip())
                i += 1
            while buf and not buf[-1]:
                buf.pop()
            meta[key] = ("\n" if rest.startswith("|") else " ").join(buf)
            continue
        if rest:
            meta[key] = _parse_scalar_or_inline(rest)
            continue
        # 값이 비어 있음: 다음 줄이 '- item' 리스트인지, 들여쓴 맵인지 확인
        j = i
        while j < n and not lines[j].strip():
            j += 1
        if j < n and lines[j].lstrip().startswith("- ") or (j < n and lines[j].strip() == "-"):
            items = []
            i = j
            while i < n:
                s = lines[i].strip()
                if not s:
                    i += 1
                    continue
                if s == "-" or s.startswith("- "):
                    items.append(_parse_scalar_or_inline(s[1:].strip()) if s != "-" else "")
                    i += 1
                else:
                    break
            meta[key] = [x if isinstance(x, str) else ", ".join(x) for x in items]
        elif j < n and _indent(lines[j]) > 0:
            sub, i = [], j
            base = _indent(lines[j])
            while i < n and (not lines[i].strip() or _indent(lines[i]) >= base):
                sub.append(lines[i][base:] if lines[i].strip() else "")
                i += 1
            meta[key] = _parse_block(sub)
        else:
            meta[key] = ""
    return meta


_NEEDS_QUOTE_START = set("[]{}>|*&!%@`#'\",?-:~ ")
_RESERVED = {"true", "false", "yes", "no", "null", "on", "off", "~"}


def _dump_scalar(v):
    s = str(v)
    needs = (
        s == ""
        or s[0] in _NEEDS_QUOTE_START
        or s[-1] in " :"
        or ": " in s
        or " #" in s
        or "\n" in s
        or "\t" in s
        or s.lower() in _RESERVED
        or (re.match(r"^[-+]?\d+(\.\d+)?$", s) is not None)
    )
    if not needs:
        return s
    esc = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")
    return '"' + esc + '"'


def dump_frontmatter(meta, body=""):
    """meta dict + body → 마크다운 텍스트. 리스트는 여러 줄 '- item' 형식."""
    out = ["---"]
    for k, v in meta.items():
        if isinstance(v, dict):
            out.append(f"{k}:")
            for sk, sv in v.items():
                if isinstance(sv, (list, tuple)):
                    out.append(f"  {sk}: [" + ", ".join(_dump_scalar(x) for x in sv) + "]")
                else:
                    out.append(f"  {sk}: {_dump_scalar(sv)}")
        elif isinstance(v, (list, tuple)):
            if not v:
                out.append(f"{k}: []")
            else:
                out.append(f"{k}:")
                out.extend(f"  - {_dump_scalar(x)}" for x in v)
        else:
            out.append(f"{k}: {_dump_scalar(v)}")
    out.append("---")
    text = "\n".join(out) + "\n"
    if body:
        text += "\n" + body.lstrip("\n")
        if not text.endswith("\n"):
            text += "\n"
    return text


# ---------------------------------------------------------------------------
# 설정 · 경로 안전
# ---------------------------------------------------------------------------

def home_dir():
    return Path(os.path.realpath(os.path.expanduser("~")))


def config_path():
    return home_dir() / ".config" / "second-brain" / "config.json"


CONFIG_OVERRIDES = {}  # 데모 모드 등에서 파일 설정 위에 덧씌우는 값(저장되지 않음)


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    p = config_path()
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            log(f"경고: 설정 파일을 읽지 못했습니다({e}). 기본값을 사용합니다.")
            data = {}
        if isinstance(data, dict):
            cfg.update(data)
    cfg.update(CONFIG_OVERRIDES)
    return cfg


def save_config(cfg):
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def ensure_in_home(path, what="경로"):
    """홈 디렉토리 밖이면 거부. 실경로(심볼릭 링크 해석)를 반환."""
    real = Path(os.path.realpath(os.path.expanduser(str(path))))
    home = home_dir()
    if real != home and home not in real.parents:
        raise BrainError(f"{what}가 홈 디렉토리 밖입니다: {real}")
    if real == home:
        raise BrainError(f"{what}로 홈 디렉토리 자체는 사용할 수 없습니다: {real}")
    return real


def vault_path(override=None):
    raw = override or os.environ.get("SECOND_BRAIN_VAULT") or load_config().get("vault") or "~/brain"
    return ensure_in_home(raw, "볼트 경로")


def vault_exists(v):
    """볼트 판정: 디렉토리와 BRAIN.md가 모두 있어야 한다(init으로 생성)."""
    return v.is_dir() and (v / "BRAIN.md").is_file()


def require_vault(override=None):
    v = vault_path(override)
    if not vault_exists(v):
        raise BrainError(f"볼트가 없습니다: {v} — 먼저 `brain.py init`을 실행하세요.", EXIT_NO_VAULT)
    return v


def ensure_in_vault(vault, path):
    real = Path(os.path.realpath(str(path)))
    if real != vault and vault not in real.parents:
        raise BrainError(f"볼트 밖 경로는 다룰 수 없습니다: {path}")
    return real


def resolve_note(vault, ref):
    """PATH(절대/볼트 상대/cwd 상대) 또는 파일 stem으로 노트 파일을 찾는다."""
    ref = str(ref)
    name = ref[2:-2] if ref.startswith("[[") and ref.endswith("]]") else ref
    cands = []
    p = Path(os.path.expanduser(name))
    if p.is_absolute():
        cands.append(p)
    else:
        cands.append(vault / name)
        cands.append(Path.cwd() / name)
        if not name.endswith(".md"):
            cands.append(vault / (name + ".md"))
    for c in cands:
        if c.is_file():
            return ensure_in_vault(vault, c)
    stem = Path(name).name
    stem = stem[:-3] if stem.endswith(".md") else stem
    for n in iter_note_files(vault):
        if n.stem == stem:
            return ensure_in_vault(vault, n)
    raise BrainError(f"노트를 찾을 수 없습니다: {ref}")


# ---------------------------------------------------------------------------
# 노트 모델
# ---------------------------------------------------------------------------

def iter_note_files(vault):
    for root, dirs, files in os.walk(vault):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        rel_root = Path(root).relative_to(vault)
        for f in sorted(files):
            if not f.endswith(".md"):
                continue
            if rel_root == Path(".") and f in SKIP_FILES:
                continue
            yield Path(root) / f


class Note:
    def __init__(self, vault, path):
        self.path = path
        self.rel = path.relative_to(vault).as_posix()
        self.stem = path.stem
        text = path.read_text(encoding="utf-8", errors="replace")
        self.meta, self.body = parse_frontmatter(text)

    @property
    def title(self):
        return str(self.meta.get("title") or self.stem)

    @property
    def type(self):
        return str(self.meta.get("type") or "note")

    @property
    def created(self):
        c = str(self.meta.get("created") or "")[:10]
        return c if DATE_RE.match(c) else "0000-00-00"

    @property
    def tags(self):
        return as_list(self.meta.get("tags"))

    @property
    def project(self):
        return str(self.meta.get("project") or "")

    def out_links(self):
        """프론트매터 links/supersedes + 본문 [[위키링크]]의 대상 stem 집합."""
        targets = []
        for key in ("links", "supersedes", "people"):
            for v in as_list(self.meta.get(key)):
                targets.append(v)
        targets.extend(WIKILINK_RE.findall(self.body))
        out = set()
        for t in targets:
            s = link_target(t)
            if s and s != self.stem:
                out.add(s)
        return out

    def to_dict(self):
        return {"path": self.rel, "title": self.title, "type": self.type, "created": self.created,
                "tags": self.tags, "project": self.project}


def as_list(v):
    if v is None or v == "":
        return []
    if isinstance(v, list):
        return [str(x) for x in v]
    if isinstance(v, dict):
        return []
    return [x.strip() for x in str(v).split(",") if x.strip()]


def link_target(s):
    s = str(s).strip()
    if s.startswith("[[") and s.endswith("]]"):
        s = s[2:-2]
    s = s.split("|", 1)[0].split("#", 1)[0].strip()
    if s.endswith(".md"):
        s = s[:-3]
    return s.rsplit("/", 1)[-1]


def wikilink(stem):
    return f"[[{stem}]]"


_NOTES_CACHE = {}  # 해석된 볼트 경로 -> (지문, Note 리스트). 서버 프로세스 생존 기간 동안만 유지.


def _vault_note_signature(vault):
    """볼트의 노트 디렉토리 지문: iter_note_files와 같은 필터로 (rel경로, mtime_ns, size)만 stat.

    내용을 읽지 않고 os.walk + stat만 하므로 전체 파싱보다 자릿수 단위로 저비용이다.
    파일이 추가/삭제/수정/이름변경 되면 이 튜플이 달라져 캐시가 자동으로 무효화된다
    (Obsidian 등 외부 에디터가 서버 몰래 파일을 바꿔도 동일하게 감지됨).
    """
    sig = []
    for root, dirs, files in os.walk(vault):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        rel_root = Path(root).relative_to(vault)
        for f in sorted(files):
            if not f.endswith(".md"):
                continue
            if rel_root == Path(".") and f in SKIP_FILES:
                continue
            fp = Path(root) / f
            try:
                st = fp.stat()
            except OSError:
                continue  # 스캔과 stat 사이에 삭제된 경우: 지문에서 빠지므로 자연히 캐시 미스 유발
            sig.append((str((rel_root / f).as_posix()), st.st_mtime_ns, st.st_size))
    return tuple(sig)


def invalidate_notes_cache(vault=None):
    """노트 캐시를 비운다. vault가 없으면 전체(모든 볼트), 있으면 해당 볼트만."""
    if vault is None:
        _NOTES_CACHE.clear()
    else:
        _NOTES_CACHE.pop(str(Path(vault).resolve()), None)


def _load_notes_uncached(vault):
    notes = []
    for p in iter_note_files(vault):
        try:
            notes.append(Note(vault, p))
        except OSError as e:
            log(f"경고: 읽기 실패 {p}: {e}")
    return notes


def load_notes(vault):
    """볼트의 모든 노트를 로드한다.

    같은 요청 안에서, 그리고 서버가 살아있는 동안 여러 번 호출돼도(대시보드 폴링 등)
    파일이 안 바뀌었으면 디스크 재파싱 없이 캐시를 반환한다.
    캐시 키는 볼트 지문(파일 mtime/size)이라 create/edit/delete/rename 모두 자동 감지된다.
    반환값은 항상 새 list 객체(list(cached))다 — 호출자가 리스트 자체를 정렬/추가/삭제해도
    캐시가 오염되지 않는다. 리스트 안의 Note 객체는 캐시 적중 시 재사용되는데, 코드베이스 전체를
    확인한 결과 Note.meta를 직접 mutate하는 곳(find_or_create_event_note, 테스트 setUp)은
    전부 그 직후 write_note()로 즉시 디스크에 반영하고, write_note()가 캐시를 무효화하므로
    다음 load_notes() 호출은 새로 파싱한 새 Note 인스턴스를 받는다. apply_enrichment처럼
    서버 요청 경로에서 meta를 바꾸는 곳은 `dict(note.meta)`로 복사해 쓰므로 캐시된 Note를
    직접 건드리지 않는다 — 그래서 Note를 매번 재파싱하지 않고 재사용해도 안전하다.
    SECOND_BRAIN_NO_NOTE_CACHE=1이면 캐시를 완전히 끈다(테스트/디버깅용).
    """
    if os.environ.get("SECOND_BRAIN_NO_NOTE_CACHE"):
        return _load_notes_uncached(vault)
    key = str(Path(vault).resolve())
    sig = _vault_note_signature(vault)
    cached = _NOTES_CACHE.get(key)
    if cached is not None and cached[0] == sig:
        return list(cached[1])
    notes = _load_notes_uncached(vault)
    _NOTES_CACHE[key] = (sig, notes)
    return list(notes)


def link_graph(notes):
    """(stem→노트, 무방향 인접 집합). 존재하는 노트 사이의 링크만 센다."""
    by_stem = {n.stem: n for n in notes}
    title_map = {n.title: n.stem for n in notes}
    adj = defaultdict(set)
    for n in notes:
        for t in n.out_links():
            target = t if t in by_stem else title_map.get(t)
            if target and target != n.stem:
                adj[n.stem].add(target)
                adj[target].add(n.stem)
    return by_stem, adj


def write_note(path, meta, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_frontmatter(meta, body), encoding="utf-8")
    invalidate_notes_cache()  # create_note/import_path/relink/enrich 등 모든 노트 쓰기가 이 함수를 거치므로
    # 여기 한 곳에서 무효화하면 전부 커버된다(지문 기반 자동감지 위의 belt-and-braces).


# ---------------------------------------------------------------------------
# slug · 경로 규칙
# ---------------------------------------------------------------------------

_FORBIDDEN = re.compile(r"[\\/:*?\"<>|#^\[\]{}()!@$%&+=,;'`~.]")


def slugify(title):
    """한글 유지, 공백→-, 금지문자 제거, 영문 소문자."""
    s = _FORBIDDEN.sub("", str(title).strip().lower())
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    s = s[:60].rstrip("-")
    return s or "untitled"


def existing_stems(vault):
    return {p.stem for p in iter_note_files(vault)}


def unique_stem(stem, taken):
    if stem not in taken:
        return stem
    k = 2
    while f"{stem}-{k}" in taken:
        k += 1
    return f"{stem}-{k}"


def next_decision_number(vault):
    mx = 0
    d = vault / "decisions"
    if d.is_dir():
        for p in d.glob("*.md"):
            m = re.match(r"^(\d+)-", p.name)
            if m:
                mx = max(mx, int(m.group(1)))
    return mx + 1


def target_path(vault, ntype, title, created, taken=None):
    """타입별 경로 규칙. stem은 볼트 전체에서 유일(위키링크 충돌 방지)."""
    taken = existing_stems(vault) if taken is None else taken
    slug = slugify(title)
    if ntype == "decision":
        base = f"{next_decision_number(vault):03d}-{slug}"
        stem = unique_stem(base, taken)
        return vault / "decisions" / f"{stem}.md"
    stem = unique_stem(slug, taken)
    if ntype == "project":
        return vault / "projects" / f"{stem}.md"
    if ntype == "person":
        return vault / "people" / f"{stem}.md"
    y, m = created[:4], created[5:7]
    if ntype == "event":
        return vault / "events" / y / f"{created[:10]}-{stem}.md"
    if ntype == "journal":  # 하루 한 장: 날짜가 파일명(주간 회고는 -weekly)
        return vault / "journal" / y / f"{created[:10]}{'-weekly' if slug.endswith('주간-회고') else ''}.md"
    return vault / "notes" / y / m / f"{stem}.md"


def parse_date(s, what="날짜"):
    try:
        return datetime.strptime(str(s), "%Y-%m-%d").date()
    except ValueError:
        raise BrainError(f"{what} 형식이 잘못됐습니다(YYYY-MM-DD): {s}")


def default_body(ntype, title):
    if ntype == "decision":
        return (f"# {title}\n\n## 상황\n\n## 고려한 선택지\n\n## 결정\n\n## 이유\n\n"
                "## 되돌아볼 날짜\n")
    if ntype == "project":
        return f"# {title}\n\n## 목표\n\n## 메모\n"
    if ntype == "person":
        return f"# {title}\n\n## 맥락\n"
    if ntype == "event":
        return f"# {title}\n\n## 준비\n\n## 동선\n\n## 메모\n"
    if ntype == "journal":
        return f"# {title}\n\n## 오늘\n\n## 잘한 것\n\n## 내일 첫 일\n"
    return f"# {title}\n"


def create_note(vault, ntype, title, tags=None, project=None, source=None, people=None,
                revisit=None, body=None, status=None, created=None, extra=None):
    if ntype not in ALL_TYPES:
        raise BrainError(f"알 수 없는 타입: {ntype} (가능: {', '.join(ALL_TYPES)})")
    if not title or not str(title).strip():
        raise BrainError("제목(--title)이 비어 있습니다.")
    created = created or date.today().isoformat()
    parse_date(created, "created")
    if revisit:
        parse_date(revisit, "revisit")
    if status and status not in DECISION_STATUSES:
        raise BrainError(f"알 수 없는 상태: {status} (가능: {', '.join(DECISION_STATUSES)})")
    meta = {"title": str(title).strip(), "type": ntype, "created": created, "tags": list(tags or [])}
    if project:
        meta["project"] = project
    if source:
        meta["source"] = source
    if people:
        meta["people"] = [wikilink(slugify(p)) for p in people]
    if revisit:
        meta["revisit"] = revisit
    if ntype == "decision":
        meta["status"] = status or "open"
    elif status:
        meta["status"] = status
    for k, v in (extra or {}).items():
        meta.setdefault(k, v)
    path = target_path(vault, ntype, meta["title"], created)
    write_note(path, meta, body if body is not None else default_body(ntype, meta["title"]))
    return path


# ---------------------------------------------------------------------------
# 검색 (BM25-lite + 최근성)
# ---------------------------------------------------------------------------

_HANGUL_RUN = re.compile(r"[\uac00-\ud7a3\u3131-\u318e]+")
_WORD = re.compile(r"[a-z0-9]+")


def tokenize(text):
    """한글은 연속 구간의 2-gram(1글자면 그대로), 영문/숫자는 소문자 단어."""
    text = str(text).lower()
    toks = []
    for m in re.finditer(r"[\uac00-\ud7a3\u3131-\u318e]+|[a-z0-9]+", text):
        w = m.group(0)
        if _HANGUL_RUN.fullmatch(w):
            if len(w) == 1:
                toks.append(w)
            else:
                toks.extend(w[i:i + 2] for i in range(len(w) - 1))
        else:
            toks.append(w)
    return toks


FIELD_WEIGHTS = {"title": 3.0, "tags": 2.0, "summary": 2.0, "body": 1.0}
HALF_LIFE_DAYS = 90.0


def recency_factor(created, today):
    """90일 반감 감쇠. 오래된 노트도 완전히 묻히지 않도록 0.5~1.0 범위로 섞는다."""
    try:
        age = max(0, (today - parse_date(created)).days)
    except BrainError:
        age = 3650
    return 0.5 + 0.5 * (0.5 ** (age / HALF_LIFE_DAYS))


def _snippet(body, qtoks, lines=2):
    qset = set(qtoks)
    cands = []
    for idx, line in enumerate(body.split("\n")):
        s = line.strip()
        if not s or s.startswith("<!--"):
            continue
        hits = sum(1 for t in tokenize(s) if t in qset)
        cands.append((hits, idx, s))
    if not cands:
        return []
    matched = [c for c in cands if c[0] > 0]
    pool = sorted(matched, key=lambda c: (-c[0], c[1]))[:lines] if matched else cands[:lines]
    return [c[2][:160] for c in sorted(pool, key=lambda c: c[1])]


# ---------------------------------------------------------------------------
# 검색 연산자 (v0.29) — type:/tag:/project:/since:/until:/has:/status:/is:orphan/
# -부정어/"정확한 문구". dash_search·cmd_search가 공유한다.
# ---------------------------------------------------------------------------

_QUERY_TOKEN_RE = re.compile(r'"([^"]*)"|(\S+)')
_QUERY_OP_RE = re.compile(r"^(type|tag|project|since|until|has|status|is):(.+)$", re.IGNORECASE)
_ABS_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_REL_DATE_RE = re.compile(r"^(\d+)([dwm])$", re.IGNORECASE)
_HAS_VALUES = ("summary", "revisit")


def parse_query(q):
    """검색어 문자열 → (terms, filters). 잘못된 연산자 값은 조용히 무시한다(입력마다 검색이
    실행되는 실시간 UI에서 오류를 던지지 않기 위함). 반환하는 filters는 값이 있는 키만 담는다."""
    terms = []
    filters = {}
    for m in _QUERY_TOKEN_RE.finditer(str(q or "")):
        phrase, token = m.group(1), m.group(2)
        if phrase is not None:
            if phrase.strip():
                filters.setdefault("phrase", []).append(phrase)
                terms.append(phrase)
            continue
        om = _QUERY_OP_RE.match(token)
        if om:
            key, val = om.group(1).lower(), om.group(2)
            if key == "type" and val:
                filters.setdefault("type", []).append(val.lower())
            elif key == "tag" and val:
                filters.setdefault("tag", []).append(val.lower())
            elif key == "project" and val:
                filters["project"] = val
            elif key in ("since", "until") and (_ABS_DATE_RE.match(val) or _REL_DATE_RE.match(val)):
                filters[key] = val
            elif key == "has" and val.lower() in _HAS_VALUES:
                filters.setdefault("has", []).append(val.lower())
            elif key == "status" and val.lower() in DECISION_STATUSES:
                filters.setdefault("status", []).append(val.lower())
            elif key == "is" and val.lower() == "orphan":
                filters["orphan"] = True
            continue
        if token.startswith("-") and len(token) > 1:
            filters.setdefault("neg", []).append(token[1:])
            continue
        terms.append(token)
    return terms, filters


def _resolve_query_date(val, today):
    """since:/until: 값(절대 YYYY-MM-DD 또는 7d/2w/3m 상대)을 절대 날짜 문자열로."""
    if _ABS_DATE_RE.match(val):
        return val
    m = _REL_DATE_RE.match(val)
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2).lower()
    days = n if unit == "d" else n * 7 if unit == "w" else n * 30
    return (today - timedelta(days=days)).isoformat()


def _apply_query_filters(notes, filters, today):
    """parse_query가 뽑은 filters로 후보 노트를 좁힌다(랭킹 전 단계)."""
    types_req = filters.get("type")
    tags_req = filters.get("tag")
    project_req = filters.get("project")
    since_v = filters.get("since")
    until_v = filters.get("until")
    since_resolved = _resolve_query_date(since_v, today) if since_v else None
    until_resolved = _resolve_query_date(until_v, today) if until_v else None
    has_req = filters.get("has")
    status_req = filters.get("status")
    orphan_req = filters.get("orphan")
    neg_words = [w.lower() for w in filters.get("neg", [])]
    phrases = [p.lower() for p in filters.get("phrase", [])]
    adj = link_graph(notes)[1] if orphan_req else None
    out = []
    for n in notes:
        if types_req and n.type not in types_req:
            continue
        if tags_req:
            note_tags = [t.lower() for t in n.tags]
            if not all(t in note_tags for t in tags_req):
                continue
        if project_req and slugify(n.project) != slugify(project_req):
            continue
        if since_resolved and n.created < since_resolved:
            continue
        if until_resolved and n.created > until_resolved:
            continue
        if has_req:
            if "summary" in has_req and not str(n.meta.get("summary") or "").strip():
                continue
            if "revisit" in has_req and not str(n.meta.get("revisit") or "").strip():
                continue
        if status_req and str(n.meta.get("status") or "open") not in status_req:
            continue
        if orphan_req and adj.get(n.stem):
            continue
        if neg_words or phrases:
            hay = (n.title + "\n" + n.body).lower()
            if neg_words and any(w in hay for w in neg_words):
                continue
            if phrases and not all(p in hay for p in phrases):
                continue
        out.append(n)
    return out


class SearchHits(list):
    """dash_search 결과: 기존 list 동작(길이·인덱싱·순회) 그대로 유지하면서
    facets(타입·태그 집계)와 applied(파싱된 필터)를 부가 속성으로 붙인다."""

    def __init__(self, hits, facets=None, applied=None):
        super().__init__(hits)
        self.facets = facets or {"types": {}, "tags": {}}
        self.applied = applied or {}


def search(vault, query, ntype=None, project=None, tag=None, since=None, limit=10, today=None, notes=None):
    """notes를 넘기면(예: dash_search의 연산자 필터링 결과) ntype/project/tag/since 인자 대신
    그 후보 목록을 그대로 채점한다 — 기존 호출부(notes=None)는 동작이 그대로다."""
    today = today or date.today()
    qtoks = tokenize(query)
    if not qtoks:
        raise BrainError("검색어에서 토큰을 찾지 못했습니다.")
    if since:
        parse_date(since, "--since")
    if notes is None:
        notes = []
        for n in load_notes(vault):
            if ntype and n.type != ntype:
                continue
            if project and slugify(n.project) != slugify(project):
                continue
            if tag and tag.lower() not in [t.lower() for t in n.tags]:
                continue
            if since and n.created < since:
                continue
            notes.append(n)
    docs = []
    for n in notes:
        tf = Counter()
        length = 0.0
        fields = {"title": n.title, "tags": " ".join(n.tags), "summary": str(n.meta.get("summary") or ""), "body": n.body}
        for f, text in fields.items():
            toks = tokenize(text)
            w = FIELD_WEIGHTS[f]
            for t in toks:
                tf[t] += w
            length += w * len(toks)
        docs.append((n, tf, length))
    N = len(docs)
    if N == 0:
        return []
    avgdl = sum(d[2] for d in docs) / N or 1.0
    df = Counter()
    for _, tf, _ in docs:
        for t in set(qtoks):
            if tf.get(t):
                df[t] += 1
    k1, b = 1.2, 0.75
    results = []
    for n, tf, dl in docs:
        score = 0.0
        for t in set(qtoks):
            f = tf.get(t, 0.0)
            if not f:
                continue
            idf = math.log(1 + (N - df[t] + 0.5) / (df[t] + 0.5))
            score += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * dl / avgdl))
        if score <= 0:
            continue
        score *= recency_factor(n.created, today)
        d = n.to_dict()
        d["score"] = round(score, 4)
        d["snippet"] = _snippet(n.body, qtoks)
        results.append(d)
    results.sort(key=lambda r: (-r["score"], r["path"]))
    return results[:limit]


# ---------------------------------------------------------------------------
# 인덱스 (BRAIN.md) · 프로젝트 허브 자동 수집
# ---------------------------------------------------------------------------

def _project_hubs(notes):
    hubs = {}
    for n in notes:
        if n.type == "project":
            hubs[n.stem] = n
            hubs.setdefault(slugify(n.title), n)
    return hubs


def update_project_hubs(vault, notes):
    """프로젝트 허브 본문의 자동 블록에 관련 노트·결정 링크를 채운다. 변경된 허브 수 반환."""
    hubs = _project_hubs(notes)
    members = defaultdict(list)
    for n in notes:
        if n.type == "project" or not n.project:
            continue
        hub = hubs.get(slugify(n.project)) or hubs.get(link_target(n.project))
        if hub:
            members[hub.stem].append(n)
    changed = 0
    for hub in {h.stem: h for h in hubs.values()}.values():
        items = sorted(members.get(hub.stem, []), key=lambda x: (x.created, x.stem), reverse=True)
        lines = [AUTO_BEGIN, "## 관련 기록 (자동)", ""]
        if items:
            for x in items:
                extra = f" ({x.meta.get('status')})" if x.type == "decision" else ""
                lines.append(f"- {x.created} · {x.type} · {wikilink(x.stem)}{extra}")
        else:
            lines.append("- (아직 없음)")
        lines.append(AUTO_END)
        block = "\n".join(lines)
        body = hub.body
        if AUTO_BEGIN in body and AUTO_END in body:
            pre = body.split(AUTO_BEGIN, 1)[0]
            post = body.split(AUTO_END, 1)[1]
            new_body = pre + block + post
        else:
            new_body = body.rstrip("\n") + "\n\n" + block + "\n"
        if new_body != body:
            write_note(hub.path, hub.meta, new_body)
            hub.body = new_body
            changed += 1
    return changed


def build_index(vault, today=None):
    today = today or date.today()
    notes = load_notes(vault)
    update_project_hubs(vault, notes)
    by_stem, adj = link_graph(notes)
    t = today.isoformat()
    out = [
        "# BRAIN — 세컨드브레인 인덱스",
        "",
        f"> 자동 생성 {t} · 노트 {len(notes)}개 · `brain.py index`가 덮어쓰니 직접 수정하지 마세요.",
        "",
        "## 최근 기록",
        "",
    ]
    recent = sorted(notes, key=lambda n: (n.created, n.rel), reverse=True)[:20]
    out += [f"- {n.created} · {n.type} · {n.title} — {wikilink(n.stem)}" for n in recent] or ["- (없음)"]

    out += ["", "## 프로젝트", ""]
    hubs = _project_hubs(notes)
    groups = defaultdict(list)
    names = {}
    for n in notes:
        if n.type == "project":
            key = n.stem
            names[key] = n.title
            groups.setdefault(key, [])
            continue
        if not n.project:
            continue
        hub = hubs.get(slugify(n.project))
        key = hub.stem if hub else slugify(n.project)
        names.setdefault(key, hub.title if hub else n.project)
        groups[key].append(n)
    if groups:
        for key in sorted(groups, key=lambda k: (-len(groups[k]), k)):
            items = sorted(groups[key], key=lambda n: (n.created, n.rel), reverse=True)
            head = wikilink(key) if key in by_stem else names[key]
            recent3 = ", ".join(wikilink(x.stem) for x in items[:3]) or "-"
            out.append(f"- {head} · {len(items)}개 · 최근: {recent3}")
    else:
        out.append("- (없음)")

    out += ["", "## 미해결 결정", ""]
    opens = [n for n in notes if n.type == "decision" and n.meta.get("status", "open") == "open"]
    opens.sort(key=lambda n: (str(n.meta.get("revisit") or "9999-99-99"), n.rel))
    if opens:
        for n in opens:
            rv = str(n.meta.get("revisit") or "")
            due = " **도래**" if rv and rv <= t else ""
            rvs = f" · 되돌아볼 날 {rv}{due}" if rv else ""
            out.append(f"- {wikilink(n.stem)} {n.title}{rvs}")
    else:
        out.append("- (없음)")

    out += ["", "## 고아 노트 (링크 0)", ""]
    orphans = [n for n in sorted(notes, key=lambda n: (n.created, n.rel), reverse=True)
               if not adj.get(n.stem)]
    out += [f"- {wikilink(n.stem)} {n.title}" for n in orphans[:10]] or ["- (없음)"]
    content = "\n".join(out) + "\n"
    (vault / "BRAIN.md").write_text(content, encoding="utf-8")
    return content, {"notes": len(notes), "recent": len(recent), "projects": len(groups),
                     "open_decisions": len(opens), "orphans": len(orphans)}


# ---------------------------------------------------------------------------
# 리뷰 · 링크 · 결정 대체
# ---------------------------------------------------------------------------

def review(vault, days=7, today=None):
    today = today or date.today()
    since = (today - timedelta(days=days)).isoformat()
    t = today.isoformat()
    notes = load_notes(vault)
    by_stem, adj = link_graph(notes)
    new = [n.to_dict() for n in sorted(notes, key=lambda n: (n.created, n.rel), reverse=True)
           if n.created >= since]
    due = [dict(n.to_dict(), revisit=str(n.meta.get("revisit"))) for n in notes
           if n.type == "decision" and n.meta.get("status", "open") == "open"
           and n.meta.get("revisit") and str(n.meta.get("revisit")) <= t]
    orphans = [n.to_dict() for n in notes if not adj.get(n.stem)]
    pairs = []
    cand = [n for n in notes if n.type != "project"]
    for i in range(len(cand)):
        for j in range(i + 1, len(cand)):
            a, b = cand[i], cand[j]
            if b.stem in adj.get(a.stem, set()):
                continue
            shared = sorted(set(x.lower() for x in a.tags) & set(x.lower() for x in b.tags))
            same_proj = bool(a.project) and slugify(a.project) == slugify(b.project)
            if not shared and not same_proj:
                continue
            score = len(shared) + (1 if same_proj else 0)
            reason = []
            if same_proj:
                reason.append(f"프로젝트 {a.project}")
            if shared:
                reason.append("태그 " + ", ".join(shared))
            pairs.append((score, max(a.created, b.created), a.rel, b.rel, " · ".join(reason)))
    pairs.sort(key=lambda p: (p[1], p[2], p[3]), reverse=True)  # 최신 우선
    pairs.sort(key=lambda p: -p[0])  # 공유 태그/프로젝트 많은 순(안정 정렬)
    suggestions = [{"a": p[2], "b": p[3], "reason": p[4]} for p in pairs[:10]]
    build_index(vault, today)
    return {"days": days, "since": since, "new_notes": new, "due_decisions": due,
            "orphans": orphans[:10], "link_suggestions": suggestions}


def add_link(vault, a, b):
    pa, pb = resolve_note(vault, a), resolve_note(vault, b)
    if pa == pb:
        raise BrainError("같은 노트끼리는 연결할 수 없습니다.")
    changed = []
    for src, dst in ((pa, pb), (pb, pa)):
        meta, body = parse_frontmatter(src.read_text(encoding="utf-8"))
        links = as_list(meta.get("links"))
        if dst.stem not in {link_target(x) for x in links}:
            links.append(wikilink(dst.stem))
            meta["links"] = links
            write_note(src, meta, body)
            changed.append(src.relative_to(vault).as_posix())
    return pa, pb, changed


def supersede(vault, old, new):
    po, pn = resolve_note(vault, old), resolve_note(vault, new)
    if po == pn:
        raise BrainError("같은 결정을 스스로 대체할 수 없습니다.")
    mo, bo = parse_frontmatter(po.read_text(encoding="utf-8"))
    mn, bn = parse_frontmatter(pn.read_text(encoding="utf-8"))
    for m, p in ((mo, po), (mn, pn)):
        if m.get("type") != "decision":
            raise BrainError(f"decision 타입이 아닙니다: {p.name}")
    mo["status"] = "superseded"
    mo["superseded_by"] = wikilink(pn.stem)
    sup = as_list(mn.get("supersedes"))
    if po.stem not in {link_target(x) for x in sup}:
        sup.append(wikilink(po.stem))
    mn["supersedes"] = sup
    if mn.get("status", "open") == "superseded":
        mn["status"] = "decided"
    for m, target in ((mo, pn), (mn, po)):
        links = as_list(m.get("links"))
        if target.stem not in {link_target(x) for x in links}:
            links.append(wikilink(target.stem))
        m["links"] = links
    write_note(po, mo, bo)
    write_note(pn, mn, bn)
    return po, pn


# ---------------------------------------------------------------------------
# 가져오기 (마크다운/Obsidian/Claude Code 메모리)
# ---------------------------------------------------------------------------

MEMORY_TYPE_MAP = {"feedback": "note", "project": "project", "reference": "source", "user": "person"}


def _is_memory_meta(meta):
    return "name" in meta and "description" in meta and "title" not in meta


def _memory_type(meta):
    t = meta.get("type")
    md = meta.get("metadata")
    if not t and isinstance(md, dict):
        t = md.get("type")
    return str(t or "")


def _memory_title(name, mtype):
    name = str(name).strip()
    if re.fullmatch(r"[A-Za-z0-9_\-]+", name):
        for pre in (mtype + "_", mtype + "-"):
            if mtype and name.lower().startswith(pre):
                name = name[len(pre):]
        name = re.sub(r"[_\-]+", " ", name).strip() or name
    return name


_SLUG_NAME_RE = re.compile(r"[A-Za-z0-9_\-]+")
_SENTENCE_END_RE = re.compile(r"[.,](?=\s|$)")  # v0.2.0·1,300 같은 숫자 내부 구두점은 제외


def _description_title(desc):
    """description 첫 문장: ' — '/' - ' 앞 → 첫 마침표/쉼표 앞 → 40자 초과면 40자 절단.
    어느 규칙에도 안 걸리는 짧은 설명(구분자 없음·40자 이하)은 None(기존 방식 폴백)."""
    d = re.sub(r"\s+", " ", str(desc or "").replace("**", "")).strip()
    if not d:
        return None
    head = None
    for sep in (" — ", " - "):
        if sep in d:
            head = d.split(sep, 1)[0]
            break
    if head is None:
        m = _SENTENCE_END_RE.search(d)
        if m:
            head = d[:m.start()]
        elif len(d) > 40:
            head = d[:40]
    head = (head or "").strip()
    return head or None


def _memory_display_title(meta, mtype):
    """메모리 노트 제목. name이 영문 슬러그일 때만 description 첫 문장을 쓴다(한글 name은 그대로)."""
    name = str(meta.get("name") or "").strip()
    if _SLUG_NAME_RE.fullmatch(name):
        t = _description_title(meta.get("description"))
        if t:
            return t
    return _memory_title(name, mtype)


def _link_key(target):
    """위키링크 대상 문자열 → 매핑 조회용 키(.md·경로 제거)."""
    t = target.strip()
    if t.endswith(".md"):
        t = t[:-3]
    return t.rsplit("/", 1)[-1]


def rewrite_wikilinks(text, mapping):
    """본문의 [[대상|별칭]]/[[대상#헤딩]]에서 대상이 mapping에 있으면 새 stem으로 바꾼다."""
    def sub(m):
        inner = m.group(1)
        cut = min([i for i in (inner.find("|"), inner.find("#")) if i >= 0] or [len(inner)])
        new = mapping.get(_link_key(inner[:cut]))
        return f"[[{new}{inner[cut:]}]]" if new else m.group(0)
    return WIKILINK_RE.sub(sub, text)


def _rewrite_link_value(v, mapping):
    v = str(v)
    if "[[" in v:
        return rewrite_wikilinks(v, mapping)
    new = mapping.get(_link_key(link_target(v)))
    return wikilink(new) if new else v


def rewrite_meta_links(meta, mapping):
    """프론트매터 links/supersedes 값의 대상을 재작성. 바뀌었으면 True."""
    changed = False
    for key in ("links", "supersedes"):
        if key not in meta or isinstance(meta[key], dict):
            continue
        old = meta[key]
        if isinstance(old, list):
            new = [_rewrite_link_value(x, mapping) for x in old]
        else:
            new = _rewrite_link_value(old, mapping)
        if new != old:
            meta[key] = new
            changed = True
    return changed


def _date_from(value, fallback):
    s = str(value or "")[:10]
    return s if DATE_RE.match(s) else fallback


def _first_heading(body):
    for line in body.split("\n"):
        m = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
        if m:
            return m.group(1).strip()
    return None


def convert_file(path):
    """원본 파일 → (type, meta, body, info). 원본은 읽기만 한다.
    info = {"stem_title": 파일 stem 산출용 제목, "names": 이 파일을 가리키던 원래 링크 이름들}."""
    text = path.read_text(encoding="utf-8", errors="replace")
    meta, body = parse_frontmatter(text)
    mtime = date.fromtimestamp(path.stat().st_mtime).isoformat()
    if _is_memory_meta(meta):
        mtype = _memory_type(meta)
        ntype = MEMORY_TYPE_MAP.get(mtype, "note")
        md = meta.get("metadata") if isinstance(meta.get("metadata"), dict) else {}
        created = _date_from(meta.get("modified") or md.get("modified"), mtime)
        stem_title = _memory_title(meta["name"], mtype)  # stem은 기존 규칙 유지(링크 안정성)
        new_meta = {"title": _memory_display_title(meta, mtype), "type": ntype, "created": created,
                    "tags": ["claude-memory"] + ([mtype] if mtype else [])}
        desc = str(meta.get("description") or "").strip()
        new_body = (f"> {desc}\n\n" if desc else "") + body.lstrip("\n")
        new_meta["imported_from"] = path.name
        names = [x for x in (str(meta["name"]).strip(), path.stem) if x]
        return ntype, new_meta, new_body, {"stem_title": stem_title, "names": names}
    ntype = str(meta.get("type") or "note")
    if ntype not in ALL_TYPES:
        ntype = "note"
    title = str(meta.get("title") or _first_heading(body) or path.stem).strip()
    created = _date_from(meta.get("created") or meta.get("date"), mtime)
    tags = [t.lstrip("#") for t in as_list(meta.get("tags") or meta.get("tag"))]
    if len(tags) == 1 and " " in tags[0]:
        tags = tags[0].split()
    new_meta = {"title": title, "type": ntype, "created": created, "tags": tags}
    for k, v in meta.items():
        if k in ("title", "type", "created", "date", "tags", "tag") or isinstance(v, dict):
            continue
        new_meta[k] = v
    if ntype == "decision" and new_meta.get("status") not in DECISION_STATUSES:
        new_meta["status"] = "open"
    new_meta["imported_from"] = path.name
    return ntype, new_meta, body, {"stem_title": title, "names": [path.stem]}


def import_path(vault, src, dry_run=False):
    src = ensure_in_home(src, "가져올 경로")
    if not src.exists():
        raise BrainError(f"경로가 없습니다: {src}")
    if src == vault or vault in src.parents or src in vault.parents:
        raise BrainError("볼트 자신(또는 볼트를 포함한 폴더)은 가져올 수 없습니다.")
    if src.is_file():
        files = [src] if src.suffix == ".md" else []
    else:
        files = []
        for root, dirs, fs in os.walk(src):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
            files += [Path(root) / f for f in sorted(fs) if f.endswith(".md")]
    seen = {(n.title, n.created): n.stem for n in load_notes(vault)}
    taken = existing_stems(vault)
    next_dec = next_decision_number(vault)
    result = {"source": str(src), "dry_run": dry_run, "imported": [], "skipped": [], "errors": []}
    planned = []  # (원본, 대상 경로, type, meta, body)
    mapping = {}  # 원래 링크 이름(name·파일 stem) → 볼트의 새 stem
    for f in files:
        if f.name == "MEMORY.md" and f.parent.name == "memory":
            result["skipped"].append({"file": str(f), "reason": "메모리 인덱스 파일"})
            continue
        try:
            ntype, meta, body, info = convert_file(f)
        except (OSError, UnicodeError) as e:
            result["errors"].append({"file": str(f), "error": str(e)})
            continue
        key = (meta["title"], meta["created"])
        if key in seen:
            # 이미 가져온 노트여도 다른 파일의 링크가 그 노트를 가리키도록 매핑은 남긴다
            for nm in info["names"]:
                if nm != seen[key]:
                    mapping.setdefault(nm, seen[key])
            result["skipped"].append({"file": str(f), "reason": "중복(title+created)"})
            continue
        if ntype == "decision":
            stem = unique_stem(f"{next_dec:03d}-{slugify(info['stem_title'])}", taken)
            dest = vault / "decisions" / f"{stem}.md"
            next_dec += 1
        else:
            dest = target_path(vault, ntype, info["stem_title"], meta["created"], taken)
        seen[key] = dest.stem
        taken.add(dest.stem)
        for nm in info["names"]:
            if nm != dest.stem:  # 파일명이 바뀐 경우만 재작성 대상
                mapping.setdefault(nm, dest.stem)
        planned.append((f, dest, ntype, meta, body))
    relinked = 0
    for f, dest, ntype, meta, body in planned:
        new_body = rewrite_wikilinks(body, mapping)
        if rewrite_meta_links(meta, mapping) or new_body != body:
            relinked += 1
        if not dry_run:
            write_note(dest, meta, new_body)
        result["imported"].append({"file": str(f), "dest": dest.relative_to(vault).as_posix(),
                                   "type": ntype, "title": meta["title"]})
    result["relinked"] = relinked
    if not dry_run and result["imported"]:
        build_index(vault)
    return result


def import_apple_notes(vault, folders=None, since=None, dry_run=False, limit=500, timeout=60):
    """맥 메모 앱(Notes.app)을 apple_notes.fetch_notes()로 읽어 볼트로 가져온다(읽기 전용, 쓰기는 볼트에만).

    idempotent 기준은 frontmatter의 `imported_from: "apple-notes:<id>"`:
    - 같은 id의 노트가 볼트에 없으면 새로 생성.
    - 있으면 메모 앱의 modified가 저장된 source_modified보다 최신일 때만 본문을 갱신("갱신"),
      아니면 건너뜀("변경 없음").
    since가 있으면 노트 생성일이 그 날짜 이후인 것만 대상으로 한다.
    """
    if since:
        parse_date(since, "--since")
    notes = apple_notes_mod.fetch_notes(folders=folders, limit=limit, timeout=timeout)
    err = apple_notes_mod.LAST_ERROR
    result = {"dry_run": dry_run, "created": [], "updated": [], "skipped": [], "error": err}
    if err:
        return result
    if since:
        notes = [n for n in notes if str(n.get("created") or "")[:10] >= since]
    existing_by_key = {}
    for n in load_notes(vault):
        key = str(n.meta.get("imported_from") or "")
        if key.startswith("apple-notes:"):
            existing_by_key[key] = n
    taken = existing_stems(vault)
    today = date.today().isoformat()
    for note in notes:
        nid = str(note.get("id") or "")
        if not nid:
            result["skipped"].append({"id": "", "title": note.get("title") or "", "reason": "id 없음"})
            continue
        key = f"apple-notes:{nid}"
        folder = str(note.get("folder") or "")
        body_md = apple_notes_mod.html_to_markdown(note.get("body_html") or "")
        if not body_md.strip():
            body_md = str(note.get("plaintext") or "").strip()
        title = str(note.get("title") or "").strip() or (body_md.split("\n", 1)[0].strip()[:80] or "제목 없음")
        created = str(note.get("created") or "")[:10]
        if not DATE_RE.match(created):
            created = today
        modified_iso = str(note.get("modified") or "")
        context_line = f"맥락: Apple Notes 「{folder}」에서 가져옴 ({today})" if folder else f"맥락: Apple Notes에서 가져옴 ({today})"
        body = f"# {title}\n\n{body_md}\n\n{context_line}\n"
        existing = existing_by_key.get(key)
        if existing:
            old_modified = str(existing.meta.get("source_modified") or "")
            if modified_iso and modified_iso > old_modified:
                new_meta = dict(existing.meta)
                new_meta["source_modified"] = modified_iso
                if not dry_run:
                    write_note(existing.path, new_meta, body)
                result["updated"].append({"path": existing.rel, "title": title})
            else:
                result["skipped"].append({"id": nid, "title": title, "reason": "변경 없음"})
            continue
        tags = ["apple-notes"] + ([slugify(folder)] if folder else [])
        meta = {"title": title, "type": "note", "created": created, "tags": tags,
                "imported_from": key, "source_modified": modified_iso}
        dest = target_path(vault, "note", title, created, taken)
        taken.add(dest.stem)
        if not dry_run:
            write_note(dest, meta, body)
        result["created"].append({"path": dest.relative_to(vault).as_posix(), "title": title, "folder": folder})
    if not dry_run and (result["created"] or result["updated"]):
        build_index(vault)
    return result


MEMORY_PREFIX_RE = re.compile(r"^(?:project|feedback|reference|user)[_\-]", re.I)


def _relink_candidates(key):
    stripped = MEMORY_PREFIX_RE.sub("", key)
    out = []
    for c in (stripped.replace("_", "-"), slugify(stripped), key.replace("_", "-"), slugify(key)):
        if c and c != key and c not in out:
            out.append(c)
    return out


def relink(vault, dry_run=False):
    """존재하지 않는 [[대상]]을 `_`→`-`·메모리 타입 접두어 제거로 찾을 수 있으면 재작성."""
    notes = load_notes(vault)
    stems = {n.stem for n in notes}
    titles = {n.title for n in notes}
    result = {"dry_run": dry_run, "changed": [], "links": 0}
    for n in notes:
        targets = [link_target(t) for t in WIKILINK_RE.findall(n.body)]
        for k in ("links", "supersedes"):
            targets += [link_target(v) for v in as_list(n.meta.get(k))]
        mapping = {}
        for t in targets:
            key = _link_key(t)
            if not key or key in stems or key in titles or key in mapping:
                continue
            hit = next((c for c in _relink_candidates(key) if c in stems), None)
            if hit:
                mapping[key] = hit
        if not mapping:
            continue
        meta = dict(n.meta)
        new_body = rewrite_wikilinks(n.body, mapping)
        meta_changed = rewrite_meta_links(meta, mapping)
        if new_body == n.body and not meta_changed:
            continue
        result["changed"].append({"path": n.rel, "map": mapping})
        result["links"] += len(mapping)
        if not dry_run:
            write_note(n.path, meta, new_body)
    if not dry_run and result["changed"]:
        build_index(vault)
    return result


# ---------------------------------------------------------------------------
# lint — 볼트 데이터 품질 점검
# ---------------------------------------------------------------------------

LINT_SUMMARY_MAX = 400
LINT_ORPHAN_DAYS = 90


def _strip_self_links(body, stem, title):
    """본문에서 자기 자신을 가리키는 [[위키링크]]만 없애고(별칭/대상 텍스트는 남김)."""
    def sub(m):
        inner = m.group(1)
        cut = min([i for i in (inner.find("|"), inner.find("#")) if i >= 0] or [len(inner)])
        target = link_target(inner[:cut])
        if target != stem and target != title:
            return m.group(0)
        rest = inner[cut:]
        if rest.startswith("|"):
            alias = rest[1:].split("#", 1)[0].strip()
            return alias or target
        return target
    return WIKILINK_RE.sub(sub, body)


def _drop_self_meta_links(meta, stem, title):
    """프론트매터 links/supersedes/people에서 자기 자신을 가리키는 항목을 뺀다. 바뀌면 True."""
    changed = False
    for key in ("links", "supersedes", "people"):
        vals = as_list(meta.get(key))
        if not vals:
            continue
        keep = [v for v in vals if link_target(v) not in (stem, title)]
        if len(keep) != len(vals):
            meta[key] = keep
            changed = True
    return changed


def lint_vault(vault, today=None):
    """볼트 데이터 품질 점검. 끊어진 [[링크]]는 relink()와 같은 해석기(_relink_candidates)를 재사용한다.

    반환: {"issues": [{"code","path","message","fixable","detail"?}], "counts": {code: n}, "notes": N}.
    파일은 절대 지우지 않는다 — fixable 항목만 write_note()로 고칠 수 있다.
    """
    today = today or date.today()
    today_s = today.isoformat()
    notes = load_notes(vault)
    stems = {n.stem for n in notes}
    titles = {n.title for n in notes}
    by_stem, adj = link_graph(notes)
    taken = existing_stems(vault)
    issues = []

    def add(code, path, message, fixable=False, detail=None):
        item = {"code": code, "path": path, "message": message, "fixable": bool(fixable)}
        if detail is not None:
            item["detail"] = detail
        issues.append(item)

    stem_files = defaultdict(list)
    for n in notes:
        stem_files[n.stem].append(n.rel)
    for stem, paths in stem_files.items():
        if len(paths) > 1:
            for p in paths:
                others = ", ".join(x for x in paths if x != p)
                add("stem-collision", p, f"같은 stem '{stem}'을 쓰는 다른 노트: {others}")

    title_groups = defaultdict(list)
    for n in notes:
        title_groups[n.title].append(n.rel)
    for title, paths in title_groups.items():
        if len(paths) > 1:
            for p in paths:
                others = ", ".join(x for x in paths if x != p)
                add("title-duplicate", p, f"제목 '{title}' 중복: {others}")

    for n in notes:
        if not str(n.meta.get("title") or "").strip():
            add("title-missing", n.rel, "title이 비어 있음")
        if not n.meta:
            add("frontmatter-missing", n.rel, "프론트매터 블록이 없음")

        if n.type not in ALL_TYPES:
            add("type-invalid", n.rel, f"알 수 없는 타입: {n.type}")

        raw_created = str(n.meta.get("created") or "")[:10]
        created_ok = bool(DATE_RE.match(raw_created))
        if not created_ok:
            add("created-invalid", n.rel, f"created 형식이 잘못됨: {n.meta.get('created')!r}")

        if n.type in ALL_TYPES and created_ok:
            expected = target_path(vault, n.type, n.title, raw_created, taken=taken)
            if expected.parent != n.path.parent:
                add("type-folder-mismatch", n.rel,
                    f"타입 '{n.type}'의 예상 폴더는 {expected.parent.relative_to(vault).as_posix()}인데 "
                    f"{n.path.parent.relative_to(vault).as_posix()}에 있음")

        tags_raw = n.meta.get("tags")
        if isinstance(tags_raw, str) and tags_raw.strip():
            add("tags-not-list", n.rel, f"tags가 리스트가 아님: {tags_raw!r}", fixable=True)

        summary = n.meta.get("summary")
        if summary and len(str(summary)) > LINT_SUMMARY_MAX:
            add("summary-too-long", n.rel, f"summary {len(str(summary))}자 (> {LINT_SUMMARY_MAX})")

        if n.meta.get("imported_from") and not summary:
            add("imported-unenriched", n.rel, "가져온 노트인데 요약이 없음 — `enrich`로 정제하세요", detail="enrich")

        self_hit = False
        for m in WIKILINK_RE.finditer(n.body):
            inner = m.group(1)
            cut = min([i for i in (inner.find("|"), inner.find("#")) if i >= 0] or [len(inner)])
            target = link_target(inner[:cut])
            if target == n.stem or target == n.title:
                self_hit = True
        for key in ("links", "supersedes", "people"):
            for v in as_list(n.meta.get(key)):
                target = link_target(v)
                if target == n.stem or target == n.title:
                    self_hit = True
        if self_hit:
            add("link-self", n.rel, "노트가 자기 자신을 링크함", fixable=True)

        link_targets = [link_target(t) for t in WIKILINK_RE.findall(n.body)]
        for key in ("links", "supersedes"):
            link_targets += [link_target(v) for v in as_list(n.meta.get(key))]
        seen_keys = set()
        for t in link_targets:
            key = _link_key(t)
            if not key or key in stems or key in titles or key in seen_keys:
                continue
            seen_keys.add(key)
            hit = next((c for c in _relink_candidates(key) if c in stems), None)
            if hit:
                add("link-broken", n.rel, f"[[{key}]] 대상 없음 (relink 후보: {hit})",
                    fixable=True, detail={"key": key, "to": hit})
            else:
                add("link-broken", n.rel, f"[[{key}]] 대상 없음")

        if created_ok and not adj.get(n.stem):
            age = (today - datetime.strptime(raw_created, "%Y-%m-%d").date()).days
            if age > LINT_ORPHAN_DAYS:
                add("orphan-old", n.rel, f"연결된 링크 없음 · {age}일 전 생성")

        if n.type == "event":
            ek = str(n.meta.get("event_key") or "")
            ed = str(n.meta.get("event_date") or n.created)[:10]
            if "|" in ek:
                key_date = ek.split("|", 1)[0][:10]
                if key_date != ed:
                    add("event-key-mismatch", n.rel, f"event_key 날짜({key_date}) != event_date({ed})")
            if DATE_RE.match(ed) and ed < today_s:
                unchecked = [it for it in _checklist(n.body) if not it["done"]]
                if unchecked:
                    add("event-past-unchecked", n.rel, f"지난 일정인데 체크 안 된 항목 {len(unchecked)}개")

        if n.type == "journal":
            fm = re.match(r"^(\d{4}-\d{2}-\d{2})", n.path.name)
            if fm:
                fname_date = fm.group(1)
                jd = str(n.meta.get("journal_date") or "")[:10]
                if jd != fname_date:
                    add("journal-date-mismatch", n.rel,
                        f"journal_date({jd or '없음'}) != 파일명 날짜({fname_date})",
                        fixable=True, detail=fname_date)

        try:
            raw = n.path.read_bytes()
        except OSError:
            raw = b""
        if raw[:3] == b"\xef\xbb\xbf" or b"\r\n" in raw:
            add("bom-or-crlf", n.rel, "BOM 또는 CRLF 줄바꿈 포함", fixable=True)

    counts = dict(Counter(i["code"] for i in issues))
    return {"issues": issues, "counts": counts, "notes": len(notes)}


def fix_lint_issues(vault, issues):
    """lint_vault()가 찾은 fixable 이슈만 고친다. 노트마다 write_note() 한 번만 호출.

    반환: [{"path", "codes": [...]}] — 실제로 고쳐 쓴 노트 목록.
    """
    by_path = defaultdict(list)
    for issue in issues:
        if issue.get("fixable"):
            by_path[issue["path"]].append(issue)
    fixed = []
    for rel, group in by_path.items():
        path = vault / rel
        if not path.is_file():
            continue
        n = Note(vault, path)
        meta = dict(n.meta)
        body = n.body
        codes = []
        for issue in group:
            code = issue["code"]
            if code == "tags-not-list":
                meta["tags"] = as_list(meta.get("tags"))
            elif code == "link-self":
                body = _strip_self_links(body, n.stem, n.title)
                _drop_self_meta_links(meta, n.stem, n.title)
            elif code == "link-broken":
                d = issue.get("detail") or {}
                key, to = d.get("key"), d.get("to")
                if not (key and to):
                    continue
                mapping = {key: to}
                body = rewrite_wikilinks(body, mapping)
                rewrite_meta_links(meta, mapping)
            elif code == "journal-date-mismatch":
                meta["journal_date"] = issue.get("detail")
            elif code == "bom-or-crlf":
                pass  # 파싱 단계에서 이미 BOM/CRLF가 빠졌으니 다시 쓰기만 하면 정규화됨
            else:
                continue
            codes.append(code)
        if codes:
            write_note(path, meta, body)
            fixed.append({"path": rel, "codes": codes})
    return fixed


# ---------------------------------------------------------------------------
# init · git
# ---------------------------------------------------------------------------

def git_commit(vault, message):
    if not load_config().get("git_autocommit") or not (vault / ".git").is_dir():
        return
    try:
        subprocess.run(["git", "-C", str(vault), "add", "-A"], check=True, capture_output=True)
        r = subprocess.run(["git", "-C", str(vault), "commit", "-m", message],
                           capture_output=True, text=True)
        if r.returncode not in (0, 1):
            log(f"경고: git 커밋 실패: {r.stderr.strip()}")
    except (OSError, subprocess.CalledProcessError) as e:
        log(f"경고: git 자동 커밋 실패: {e}")


SAMPLE_BODY = """# 세컨드브레인 시작하기

이 볼트는 평범한 마크다운 폴더예요. Obsidian으로 열어도 되고, 그냥 에디터로 봐도 돼요.

- Claude에게 "이거 기억해둬"라고 말하면 notes/ 아래에 노트가 생겨요.
- "이렇게 결정했어"라고 하면 decisions/ 아래에 결정 기록(ADR)이 생겨요.
- "그때 왜 그렇게 정했지?"라고 물으면 볼트를 검색해서 [[파일]]로 인용해 답해요.

BRAIN.md는 자동 생성 인덱스라 직접 고치지 않아도 돼요.
"""


def init_vault(vault, git=False):
    created = []
    for d in ("notes", "decisions", "projects", "people"):
        p = vault / d
        if not p.exists():
            p.mkdir(parents=True)
            created.append(d + "/")
    inbox = vault / "inbox.md"
    if not inbox.exists():
        inbox.write_text("# inbox\n\n빠른 캡처 임시함이에요. 정리되면 노트로 옮겨요.\n\n", encoding="utf-8")
        created.append("inbox.md")
    if not any(iter_note_files(vault)):
        p = create_note(vault, "note", "세컨드브레인 시작하기", tags=["guide", "second-brain"],
                        body=SAMPLE_BODY)
        created.append(p.relative_to(vault).as_posix())
    gi = vault / ".gitignore"
    if git:
        if not gi.exists():
            gi.write_text(".obsidian/workspace*\n.trash/\n.DS_Store\n", encoding="utf-8")
        if not (vault / ".git").is_dir():
            try:
                subprocess.run(["git", "init", "-q", str(vault)], check=True, capture_output=True)
                created.append(".git/")
            except (OSError, subprocess.CalledProcessError) as e:
                log(f"경고: git init 실패: {e}")
    build_index(vault)
    created.append("BRAIN.md")
    return created


# ---------------------------------------------------------------------------
# 대시보드 데이터 (v0.2) — 모두 순수 함수: (vault, today) → JSON 직렬화 가능한 값
# ---------------------------------------------------------------------------

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
DEMO_DIR_PARTS = (".cache", "second-brain", "demo")
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8", ".svg": "image/svg+xml",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
    ".webp": "image/webp", ".ico": "image/x-icon", ".woff2": "font/woff2", ".woff": "font/woff",
    ".txt": "text/plain; charset=utf-8", ".md": "text/markdown; charset=utf-8",
}


def _resolved_links(notes):
    """노트별 '존재하는' 대상 stem 집합(위키링크+프론트매터 links 등). 제목 링크도 해석."""
    by_stem = {n.stem: n for n in notes}
    title_map = {n.title: n.stem for n in notes}
    out = {}
    for n in notes:
        targets = set()
        for t in n.out_links():
            s = t if t in by_stem else title_map.get(t)
            if s and s != n.stem:
                targets.add(s)
        out[n.stem] = targets
    return out


def _days_left(revisit, today):
    try:
        return (parse_date(str(revisit)[:10]) - today).days if revisit else None
    except BrainError:
        return None


def _decision_item(n, today):
    rv = str(n.meta.get("revisit") or "") or None
    return {"path": n.rel, "title": n.title, "created": n.created, "revisit": rv,
            "days_left": _days_left(rv, today), "project": n.project,
            "supersedes": [link_target(x) for x in as_list(n.meta.get("supersedes"))]}


def _decision_status(n):
    s = str(n.meta.get("status") or "open")
    return s if s in DECISION_STATUSES else "open"


def dash_summary(vault, today=None):
    today = today or date.today()
    notes = load_notes(vault)
    week_ago = (today - timedelta(days=7)).isoformat()
    counts = {"notes": 0, "decisions": 0, "projects": 0, "people": 0}
    for n in notes:
        if n.type == "decision":
            counts["decisions"] += 1
        elif n.type == "project":
            counts["projects"] += 1
        elif n.type == "person":
            counts["people"] += 1
        else:
            counts["notes"] += 1
    counts["total"] = len(notes)
    recent_week = [n for n in notes if n.created >= week_ago]
    opens = [n for n in notes if n.type == "decision" and _decision_status(n) == "open"]
    opens.sort(key=lambda n: (str(n.meta.get("revisit") or "9999-99-99"), n.rel))
    open_items = []
    for n in opens:
        rv = str(n.meta.get("revisit") or "") or None
        open_items.append({"path": n.rel, "title": n.title, "revisit": rv,
                           "days_left": _days_left(rv, today)})
    recent = sorted(notes, key=lambda n: (n.created, n.rel), reverse=True)[:30]
    return {
        "vault": str(vault),
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "counts": counts,
        "this_week": {"new_notes": sum(1 for n in recent_week if n.type != "decision"),
                      "new_decisions": sum(1 for n in recent_week if n.type == "decision")},
        "open_decisions": open_items,
        "recent": [{"path": n.rel, "title": n.title, "type": n.type, "created": n.created,
                    "project": n.project, "tags": n.tags} for n in recent],
    }


def dash_graph(vault):
    notes = load_notes(vault)
    links = _resolved_links(notes)
    edges, seen = [], set()
    degree = Counter()
    for n in notes:
        for t in sorted(links[n.stem]):
            key = tuple(sorted((n.stem, t)))
            if key in seen:
                continue
            seen.add(key)
            edges.append({"source": n.stem, "target": t})
            degree[n.stem] += 1
            degree[t] += 1
    nodes = [{"id": n.stem, "path": n.rel, "title": n.title, "type": n.type, "project": n.project,
              "created": n.created, "tags": n.tags, "degree": degree[n.stem]} for n in notes]
    return {"nodes": nodes, "edges": edges}


def dash_search(vault, q, limit=20, today=None):
    """type:/tag:/project:/since:/until:/has:/status:/is:orphan/-부정어/"정확한 문구" 연산자를
    parse_query로 뽑아 후보를 좁힌 뒤, 남은 일반 단어(terms)로만 BM25 랭킹한다.
    필터만 있고 terms가 없으면 랭킹 없이 생성일 역순으로 반환한다.
    반환값은 list 그대로(기존 호출부 호환)이며 .facets/.applied가 추가로 붙는다."""
    today = today or date.today()
    terms, filters = parse_query(q)
    if not terms and not filters:
        raise BrainError("검색어에서 토큰을 찾지 못했습니다.")
    all_notes = load_notes(vault)
    candidates = _apply_query_filters(all_notes, filters, today)
    query_str = " ".join(terms).strip()
    if query_str:
        res = search(vault, query_str, limit=max(len(candidates), 1), today=today, notes=candidates)
    else:
        ordered = sorted(candidates, key=lambda n: (n.created, n.rel), reverse=True)
        res = []
        for n in ordered:
            d = n.to_dict()
            d["score"] = round(recency_factor(n.created, today), 4)
            d["snippet"] = _snippet(n.body, [])
            res.append(d)

    type_counts = Counter(r["type"] for r in res)
    tag_counts = Counter()
    candidates_by_path = {n.rel: n for n in candidates}
    for r in res:
        n = candidates_by_path.get(r["path"])
        for t in (n.tags if n else []):
            tag_counts[t] += 1
    facets = {"types": dict(sorted(type_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
              "tags": dict(sorted(tag_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:8])}

    res = res[:limit]
    notes_by_path = None
    for r in res:
        r["snippets"] = r.get("snippet", [])  # 대시보드 계약 키. CLI 호환 위해 snippet도 유지
        if "summary" not in r:
            if notes_by_path is None:
                notes_by_path = {n.rel: n for n in all_notes}
            n = notes_by_path.get(r["path"])
            summary = str((n.meta.get("summary") if n else "") or "")
            r["summary"] = summary[:160]
    return SearchHits(res, facets=facets, applied=filters)


def safe_vault_path(vault, rel):
    """쿼리로 받은 볼트 상대 경로 검증. 절대경로·'..'·볼트 밖(심볼릭 링크 포함) 거부."""
    rel = str(rel or "").strip()
    if not rel:
        raise BrainError("path 파라미터가 필요합니다.")
    pp = Path(rel)
    if pp.is_absolute() or rel.startswith(("/", "\\", "~")) or ".." in pp.parts or "\\" in rel:
        raise BrainError(f"허용되지 않는 경로입니다: {rel}")
    if pp.suffix != ".md":
        raise BrainError(f".md 노트만 열 수 있습니다: {rel}")
    return ensure_in_vault(vault, vault / pp)


def dash_note(vault, rel):
    p = safe_vault_path(vault, rel)
    if not p.is_file():
        raise FileNotFoundError(rel)
    notes = load_notes(vault)
    links = _resolved_links(notes)
    n = Note(vault, p)
    links_in = sorted(s for s, ts in links.items() if n.stem in ts)
    return {"path": n.rel, "title": n.title, "type": n.type, "frontmatter": n.meta,
            "body": n.body, "links_out": sorted(links.get(n.stem, set())), "links_in": links_in}


def dash_timeline(vault, days=30, today=None):
    today = today or date.today()
    since = (today - timedelta(days=max(days, 1) - 1)).isoformat()
    groups = defaultdict(list)
    for n in load_notes(vault):
        if since <= n.created <= today.isoformat():
            groups[n.created].append(n)
    out = []
    for d in sorted(groups, reverse=True):
        items = sorted(groups[d], key=lambda n: n.rel)
        out.append({"date": d, "items": [{"path": n.rel, "title": n.title, "type": n.type,
                                          "project": n.project} for n in items]})
    return out


def dash_report(vault, days=7, today=None, widgets=None):
    """/api/report. 인쇄용 주간 리포트 재료: 숫자 띠·이번 주(회고)·결정·일지·새 기록·자동화·할 일·프로젝트."""
    today = today or date.today()
    since = (today - timedelta(days=max(days, 1) - 1)).isoformat()
    until = today.isoformat()
    if widgets is None:
        widgets = collect_widgets()
    notes = load_notes(vault)
    in_window = [n for n in notes if since <= n.created <= until]

    counts = {"notes": 0, "decisions": 0, "journals": 0, "events": 0}
    type_key = {"decision": "decisions", "journal": "journals", "event": "events"}
    for n in in_window:
        counts[type_key.get(n.type, "notes")] += 1

    new_notes = sorted((n for n in in_window if n.type not in ("journal", "event")),
                       key=lambda n: (n.created, n.rel), reverse=True)[:30]
    new_notes = [{"path": n.rel, "title": n.title, "type": n.type, "created": n.created,
                 "summary": str(n.meta.get("summary") or "")[:200]} for n in new_notes]

    dec_notes = sorted((n for n in in_window if n.type == "decision"),
                       key=lambda n: (n.created, n.rel), reverse=True)
    decisions = [{"path": n.rel, "title": n.title, "status": _decision_status(n), "created": n.created,
                 "decision": _section(n, "결정", 200), "why": _section(n, "이유", 200),
                 "revisit": str(n.meta.get("revisit") or "") or None} for n in dec_notes]

    journals = []
    for n in sorted((n for n in in_window if n.type == "journal" and not n.stem.endswith("-weekly")),
                    key=lambda n: n.created):
        lines = [l[2:].strip() for l in note_sections(n.body).get("오늘", "").split("\n") if l.startswith("- ")]
        journals.append({"date": n.created, "lines": lines, "summary": str(n.meta.get("summary") or "")[:200]})

    weekly = [n for n in in_window if n.type == "journal" and n.stem.endswith("-weekly")]
    retro = None
    if weekly:
        n = max(weekly, key=lambda x: x.created)
        sec = note_sections(n.body)

        def cb(name):
            items = []
            for ln in sec.get(name, "").split("\n"):
                m = CHECKBOX_RE.match(ln)
                if m:
                    items.append({"text": m.group(4).strip(), "done": m.group(2).lower() == "x"})
            return items

        week = [l[2:].strip() for l in sec.get("이번 주", "").split("\n") if l.startswith("- ")]
        patterns = [l[2:].strip() for l in sec.get("눈에 띄는 것", "").split("\n") if l.startswith("- ")]
        retro = {"path": n.rel, "date": n.created, "week": week, "patterns": patterns,
                 "questions": cb("되돌아볼 질문"), "next_week": cb("다음 주")}

    automation = []
    for w in widgets:
        if w.get("state") == "paused":
            continue
        runs, fails = 0, 0
        if w.get("kind") == "log":
            h = widget_history(w, days=days, today=today)
            runs, fails = h.get("total_runs", 0), h.get("total_fails", 0)
        automation.append({"id": w.get("id"), "title": w.get("title"), "team": w.get("team") or "",
                           "status": w.get("status"), "runs": runs, "fails": fails})

    tb = dash_tasks(vault, today, widgets, agenda={"today": [], "upcoming": []})
    tasks = {"done_recent": [t.get("text", "") for t in tb.get("done_recent", [])], "open": tb["counts"]}

    projects = defaultdict(int)
    for n in in_window:
        if n.project:
            projects[n.project] += 1
    proj_list = sorted(projects.items(), key=lambda kv: (-kv[1], kv[0]))

    return {"since": since, "until": until, "counts": counts, "new_notes": new_notes,
            "decisions": decisions, "journals": journals, "retro": retro,
            "automation": automation, "tasks": tasks, "projects": proj_list}


def dash_decisions(vault, today=None):
    today = today or date.today()
    out = {s: [] for s in DECISION_STATUSES}
    for n in load_notes(vault):
        if n.type == "decision":
            out[_decision_status(n)].append(_decision_item(n, today))
    out["open"].sort(key=lambda d: (d["revisit"] or "9999-99-99", d["path"]))
    for k in ("decided", "superseded"):
        out[k].sort(key=lambda d: (d["created"], d["path"]), reverse=True)
    return out


def _journal_questions(n):
    """일지 본문 「## 되돌아볼 질문」 체크박스 전부(상태 포함). 보드 체크는 event-note check로."""
    lines = n.body.split("\n")
    try:
        start = next(i for i, l in enumerate(lines) if l.strip() == "## 되돌아볼 질문")
    except StopIteration:
        return []
    out = []
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("## "):
            break
        m = CHECKBOX_RE.match(lines[j])
        if m:
            out.append({"text": m.group(4).strip(), "line": j, "done": m.group(2).lower() == "x"})
    return out


def dash_journals(vault, limit=14):
    """최근 일지: 하루(「오늘」 절 불릿)와 주간 회고(「이번 주」 절 불릿 + 되돌아볼 질문)를 최신순으로 나눠 반환."""
    limit = max(1, min(int(limit or 14), 60))
    daily, weekly = [], []
    for n in load_notes(vault):
        if n.type != "journal":
            continue
        is_weekly = n.stem.endswith("-weekly") or n.meta.get("journal_kind") == "weekly"
        d = str(n.meta.get("journal_date") or n.created)[:10]
        sections = note_sections(n.body)
        item = {"path": n.rel, "title": n.title, "date": d, "summary": str(n.meta.get("summary") or "")}
        if is_weekly:
            item["lines"] = [l[2:].strip() for l in sections.get("이번 주", "").split("\n") if l.startswith("- ")][:5]
            item["questions"] = _journal_questions(n)
            weekly.append(item)
        else:
            item["lines"] = [l[2:].strip() for l in sections.get("오늘", "").split("\n") if l.startswith("- ")][:5]
            daily.append(item)
    daily.sort(key=lambda x: (x["date"], x["path"]), reverse=True)
    weekly.sort(key=lambda x: (x["date"], x["path"]), reverse=True)
    return {"daily": daily[:limit], "weekly": weekly[:limit]}


def dash_projects(vault):
    notes = load_notes(vault)
    hubs = _project_hubs(notes)
    groups = {}
    for n in notes:
        if n.type == "project":
            groups.setdefault(n.stem, {"hub": n, "name": n.title, "members": []})
    for n in notes:
        if n.type == "project" or not n.project:
            continue
        hub = hubs.get(slugify(n.project)) or hubs.get(link_target(n.project))
        key = hub.stem if hub else "~" + slugify(n.project)
        g = groups.setdefault(key, {"hub": hub, "name": hub.title if hub else n.project,
                                    "members": []})
        g["members"].append(n)
    out = []
    for g in groups.values():
        members = sorted(g["members"], key=lambda n: (n.created, n.rel), reverse=True)
        dates = [n.created for n in members] + ([g["hub"].created] if g["hub"] else [])
        item = {"name": g["name"],
                "note_count": sum(1 for n in members if n.type != "decision"),
                "decision_count": sum(1 for n in members if n.type == "decision"),
                "last_activity": max(dates) if dates else None,
                "recent": [{"path": n.rel, "title": n.title, "type": n.type} for n in members[:5]]}
        if g["hub"]:
            item["path"] = g["hub"].rel
        out.append(item)
    out.sort(key=lambda p: (p["last_activity"] or "", p["name"]), reverse=True)
    return out


def dash_people(vault, agenda=None, today=None):
    """/api/people. 「사람」 섹션: 사람 노트마다 다음 만남·최근 기록·언급 수, 그리고
    아직 사람 노트가 없는 참석자 목록(있으면 사람 노트 만들기로 이어짐).

    agenda는 attach_event_notes로 참석자 매칭(people 필드)이 이미 붙은 dict를 받는다
    (dash_tasks/derived_tasks와 같은 패턴 — 캐시 사본에 붙여서 호출자가 넘긴다).
    """
    today = today or date.today()
    ag = agenda or {}
    notes = load_notes(vault)
    by_stem, adj = link_graph(notes)
    events = (ag.get("today") or []) + (ag.get("upcoming") or [])

    def matched_events_for(rel, key):
        hits = []
        for e in events:
            for p in e.get("people") or []:
                if p.get("matched") and (p.get("path") == rel or normalize_person_name(p.get("name")) == key):
                    hits.append(e)
                    break
        return sorted(hits, key=lambda e: e.get("days_left") if e.get("days_left") is not None else 9999)

    people = []
    for n in notes:
        if n.type != "person":
            continue
        key = normalize_person_name(n.title)
        linked = sorted((by_stem[s] for s in adj.get(n.stem, ()) if s in by_stem and by_stem[s].type != "person"),
                        key=lambda m: (m.created, m.rel), reverse=True)
        matched = matched_events_for(n.rel, key)
        next_event = None
        if matched:
            e0 = matched[0]
            next_event = {"title": e0.get("title"), "start": e0.get("start"), "days_left": e0.get("days_left")}
        item = {
            "path": n.rel, "title": n.title, "slug": n.stem, "tags": n.tags,
            "mentions": len(linked),
            "last_note": ({"title": linked[0].title, "path": linked[0].rel, "created": linked[0].created}
                         if linked else None),
            "next_event": next_event,
            "upcoming_count": len(matched),
            "recent_notes": [{"title": m.title, "path": m.rel, "created": m.created} for m in linked[:3]],
        }
        email = str(n.meta.get("email") or "").strip()
        if email:
            item["email"] = email
        people.append(item)
    people.sort(key=lambda p: (p["next_event"]["days_left"] if p.get("next_event") else float("inf"),
                               -p["mentions"]))

    seen, unmatched = set(), []
    for e in events:
        for p in e.get("people") or []:
            if p.get("matched"):
                continue
            key = normalize_person_name(p.get("name"))
            if not key or key in seen:
                continue
            seen.add(key)
            same = [ee for ee in events for pp in (ee.get("people") or [])
                    if not pp.get("matched") and normalize_person_name(pp.get("name")) == key]
            same.sort(key=lambda ee: ee.get("days_left") if ee.get("days_left") is not None else 9999)
            unmatched.append({"name": p.get("name"),
                              "next_event": {"title": same[0].get("title"), "start": same[0].get("start")},
                              "count": len(same), "event_key": same[0].get("key")})
    return {"people": people, "unmatched_attendees": unmatched}


# ---------------------------------------------------------------------------
# 데모 볼트 (전부 가공 데이터)
# ---------------------------------------------------------------------------

def demo_vault_path():
    return home_dir().joinpath(*DEMO_DIR_PARTS)


def build_demo_vault(vault=None, today=None):
    """가공 데이터로 데모 볼트를 새로 만든다(기존 데모 폴더는 지움). 경로 반환."""
    import shutil
    today = today or date.today()
    vault = ensure_in_home(vault or demo_vault_path(), "데모 볼트 경로")
    if vault.exists():
        shutil.rmtree(vault)
    for d in ("notes", "decisions", "projects", "people"):
        (vault / d).mkdir(parents=True)

    def ago(k):
        return (today - timedelta(days=k)).isoformat()

    P1, P2, P3 = "아침브리핑", "세컨드브레인", "카페 운영"
    stems = {}

    def mk(key, ntype, title, days_ago, body, **kw):
        p = create_note(vault, ntype, title, created=ago(days_ago), body=f"# {title}\n\n{body}\n", **kw)
        stems[key] = p
        return p

    # 프로젝트 3
    mk("p1", "project", P1, 58, "## 목표\n매일 아침 7시에 뉴스·일정·날씨를 한 장으로 받기.", tags=["자동화"])
    mk("p2", "project", P2, 55, "## 목표\n대화 중 기록을 쌓고 결정 맥락을 다시 찾기.", tags=["pkm"])
    mk("p3", "project", P3, 50, "## 목표\n가상의 동네 커뮤니티 카페를 월 50명까지 키우기.", tags=["커뮤니티"])
    # 사람 2 (가공 인물)
    mk("u1", "person", "한가람", 52, "## 맥락\n가상의 스터디 동료. 자동화 스크립트 리뷰를 자주 해줌.")
    mk("u2", "person", "서도윤", 40, "## 맥락\n가상의 카페 공동 운영자. 게시판 기획 담당.")
    # 노트 18
    N = [
        ("n1", "note", "브리핑 소스 후보 정리", 57, P1, ["뉴스", "rss"], "RSS 5개와 날씨 API 1개를 후보로 적어둠."),
        ("n2", "idea", "브리핑을 음성으로 듣기", 49, P1, ["tts", "아이디어"], "출근길에 3분짜리 음성 요약으로 듣는 아이디어."),
        ("n3", "source", "RSS 파싱 라이브러리 비교 메모", 46, P1, ["rss", "python"], "표준 라이브러리 xml로 충분한지 비교."),
        ("n4", "meeting", "브리핑 포맷 회의", 41, P1, ["회의"], "한 화면 5줄 원칙으로 합의."),
        ("n5", "note", "크론 실패 원인 기록", 33, P1, ["cron", "장애"], "노트북 잠자기 모드에서 크론이 건너뛰어짐."),
        ("n6", "note", "브리핑 첫 주 회고", 20, P1, ["회고"], "읽는 데 평균 2분. 날씨는 거의 안 봄."),
        ("n7", "idea", "노트를 그래프로 보기", 54, P2, ["그래프", "아이디어"], "링크가 많은 노트가 한눈에 보이면 좋겠다."),
        ("n8", "source", "제텔카스텐 개념 요약", 51, P2, ["pkm", "제텔카스텐"], "원자 노트와 링크 중심 사고."),
        ("n9", "note", "프론트매터 필드 설계", 44, P2, ["스키마"], "title·type·created·tags·links 다섯 개로 시작."),
        ("n10", "meeting", "검색 품질 점검", 30, P2, ["검색", "회의"], "한글 2-gram이 짧은 검색어에 강함을 확인."),
        ("n11", "note", "고아 노트 줄이는 법", 18, P2, ["링크"], "주간 리뷰에서 링크 제안을 3개씩만 승인."),
        ("n12", "idea", "결정 되돌아보기 알림", 9, P2, ["결정", "아이디어"], "revisit 날짜 전날 알림."),
        ("n13", "note", "카페 첫 공지 초안", 48, P3, ["공지"], "가입 인사 게시판부터 열기."),
        ("n14", "meeting", "게시판 구조 회의", 38, P3, ["회의", "게시판"], "게시판은 5개 이하로 시작."),
        ("n15", "source", "커뮤니티 리텐션 글 메모", 27, P3, ["리텐션"], "첫 주 댓글 경험이 재방문을 좌우한다는 가상의 사례."),
        ("n16", "note", "주간 미션 반응 정리", 12, P3, ["미션", "리텐션"], "참여 12명, 인증 글 7개(가상 수치)."),
        ("n17", "idea", "신규 회원 환영 자동화", 4, P3, ["자동화", "아이디어"], "가입 시 환영 댓글 템플릿."),
        ("n18", "note", "이번 주 할 일", 1, None, ["할일"], "브리핑 음성화 실험, 대시보드 확인, 카페 공지 갱신."),
    ]
    for key, t, title, d, proj, tags, body in N:
        mk(key, t, title, d, body, project=proj, tags=tags)
    # 결정 6 (open 2 · decided 3 · superseded 1, revisit 임박 1)
    mk("d1", "decision", "브리핑은 텍스트로 먼저 낸다", 45, "## 결정\n음성은 보류하고 텍스트부터.", project=P1,
       status="superseded", tags=["포맷"])
    mk("d2", "decision", "브리핑에 음성 요약을 붙인다", 15, "## 결정\n텍스트 + 1분 음성.", project=P1,
       status="decided", tags=["포맷", "tts"])
    mk("d3", "decision", "볼트는 마크다운 파일로 둔다", 53, "## 결정\nDB 대신 Obsidian 호환 마크다운.", project=P2,
       status="decided", tags=["스키마"])
    mk("d4", "decision", "검색은 외부 서비스 없이 로컬로", 29, "## 결정\nBM25-lite 로컬 검색.", project=P2,
       status="decided", tags=["검색"])
    mk("d5", "decision", "대시보드를 로컬 서버로 띄울지", 6, "## 상황\n정적 HTML과 로컬 서버 중 고민.", project=P2,
       status="open", revisit=(today + timedelta(days=2)).isoformat(), tags=["그래프"])
    mk("d6", "decision", "카페 게시판을 5개로 제한할지", 36, "## 상황\n게시판이 많으면 글이 흩어짐.", project=P3,
       status="open", revisit=(today + timedelta(days=24)).isoformat(), tags=["게시판"])
    supersede(vault, stems["d1"], stems["d2"])  # d1↔d2 links + supersedes

    # 링크: (출발, 도착, 방식) — fm=프론트매터 links, wl=본문 위키링크
    L = [
        ("n1", "p1", "fm"), ("n2", "p1", "fm"), ("n3", "n1", "wl"), ("n4", "p1", "wl"),
        ("n4", "u1", "fm"), ("n5", "p1", "fm"), ("n6", "n4", "wl"), ("n6", "d2", "wl"),
        ("n2", "d2", "fm"), ("d1", "n4", "wl"), ("n7", "p2", "fm"), ("n8", "n7", "wl"),
        ("n9", "d3", "fm"), ("n9", "p2", "wl"), ("n10", "d4", "fm"), ("n10", "u1", "wl"),
        ("n11", "n8", "wl"), ("n12", "d5", "fm"), ("d5", "n7", "wl"), ("d3", "n8", "wl"),
        ("n13", "p3", "fm"), ("n14", "p3", "fm"), ("n14", "u2", "fm"), ("d6", "n14", "wl"),
        ("n15", "n16", "wl"), ("n16", "p3", "fm"), ("n17", "n16", "wl"), ("n17", "u2", "wl"),
        ("n18", "n2", "wl"), ("n18", "d5", "wl"), ("n18", "n13", "wl"), ("u1", "p1", "fm"),
        ("d6", "u2", "fm"), ("d5", "p2", "fm"),  # 결정이 사람·프로젝트 노트로 직접 연결(그래프 구조용)
    ]
    by_src = defaultdict(list)
    for a, b, how in L:
        by_src[a].append((b, how))
    for a, lst in by_src.items():
        pa = stems[a]
        meta, body = parse_frontmatter(pa.read_text(encoding="utf-8"))
        fm = as_list(meta.get("links"))
        wl = []
        for b, how in lst:
            target = wikilink(stems[b].stem)
            if how == "fm" and target not in fm:
                fm.append(target)
            elif how == "wl":
                wl.append(target)
        if fm:
            meta["links"] = fm
        if wl:
            body = body.rstrip("\n") + "\n\n관련: " + ", ".join(wl) + "\n"
        write_note(pa, meta, body)
    build_demo_assistant(vault, today)
    build_index(vault, today)
    return vault


def build_demo_assistant(vault, today):
    """데모 볼트에 비서 샘플: inbox 할 일, 일정 노트(준비·동선), 데모 캘린더 ICS, 데모 위젯 로그. 서버는 CONFIG_OVERRIDES로 이것들을 쓴다."""
    def d(k):
        return (today + timedelta(days=k))
    (vault / "inbox.md").write_text("# inbox\n\n"
                                    f"- [ ] 주간회의 자료 마무리 @due({today.isoformat()})\n"
                                    f"- [ ] 치과 예약 변경 전화 @due({d(1).isoformat()}) @project(건강)\n"
                                    "- [ ] 세컨드브레인 볼트를 git private 저장소에 올리기 @someday\n"
                                    f"- [ ] 인사평가 자료 제출 @due({d(-2).isoformat()})\n"
                                    f"- [ ] 회계사 답장 @waiting(회계사) @since({d(-3).isoformat()})\n"
                                    f"- [x] 온보딩 메일 3일 차 발송 여부 확인 @due({d(-1).isoformat()})\n", encoding="utf-8")
    demo = vault / "_demo"
    demo.mkdir(exist_ok=True)
    def dt(k, h, m):
        return f"{d(k).strftime('%Y%m%d')}T{h:02d}{m:02d}00"
    ev = lambda uid, title, a, b, loc="", desc="": (f"BEGIN:VEVENT\nUID:{uid}\nDTSTART;TZID=Asia/Seoul:{a}\nDTEND;TZID=Asia/Seoul:{b}\nSUMMARY:{title}\n"
                                                   + (f"LOCATION:{loc}\n" if loc else "") + (f"DESCRIPTION:{desc}\n" if desc else "") + "END:VEVENT\n")
    ics = "BEGIN:VCALENDAR\nVERSION:2.0\nX-WR-CALNAME:데모 캘린더\n"
    ics += ev("demo-1", "팀 주간회의", dt(0, 10, 0), dt(0, 11, 0), "회의실 A", "안건: 로그 저장소 전환 진행 상황")
    ics += ev("demo-2", "치과", dt(0, 15, 0), dt(0, 16, 0), "강남 스마일치과")
    ics += ev("demo-3", "저녁 약속 (대학 동기)", dt(0, 19, 0), dt(0, 21, 0), "판교")
    ics += ev("demo-4", "1:1 면담", dt(1, 14, 0), dt(1, 14, 30), "온라인")
    ics += (f"BEGIN:VEVENT\nUID:demo-5\nDTSTART;VALUE=DATE:{d(3).strftime('%Y%m%d')}\nDTEND;VALUE=DATE:{d(5).strftime('%Y%m%d')}\n"
            "SUMMARY:제주 출장\nLOCATION:김포국제공항\nEND:VEVENT\n")
    ics += ev("demo-6", "KE1201 김포 출발", dt(3, 9, 25), dt(3, 10, 35), "김포공항")
    ics += "END:VCALENDAR\n"
    (demo / "calendar.ics").write_text(ics, encoding="utf-8")
    # 일정 노트: 주간회의(준비·메모) · 제주 출장(여러 날, 동선)
    key_meet = f"{today.isoformat()}|팀 주간회의"
    event_note_action(vault, {"action": "todo", "key": key_meet, "text": "Loki 전환 비용표 출력"})
    event_note_action(vault, {"action": "todo", "key": key_meet, "text": "지난주 결정 3건 요약"})
    event_note_action(vault, {"action": "memo", "key": key_meet, "text": "지난 회의에서 OpenSearch 잔여 비용 정리 요청받음"})
    key_trip = f"{d(3).isoformat()}|제주 출장"
    event_note_action(vault, {"action": "todo", "key": key_trip, "text": "렌터카 예약 확인", "end": d(4).isoformat()})
    event_note_action(vault, {"action": "todo", "key": key_trip, "text": "고객사 방문 자료 인쇄"})
    event_note_action(vault, {"action": "step", "key": key_trip, "text": "07:20 집 출발 (자가용 50분)"})
    event_note_action(vault, {"action": "step", "key": key_trip, "text": "09:25 KE1201 김포 출발 (70분)"})
    event_note_action(vault, {"action": "step", "key": key_trip, "text": "13:00 고객사 미팅 (2시간)", "day": d(3).isoformat()})
    event_note_action(vault, {"action": "step", "key": key_trip, "text": "10:00 공항 이동 (40분)", "day": d(4).isoformat()})
    # 데모 위젯 로그 3개 (정상 2 · 실패 1), 최근 7일치 날짜 줄 포함(KPI 띠·이력 막대용) + widgets.json
    days7 = [d(-k) for k in range(6, -1, -1)]  # 6일 전 ~ 오늘
    backup_log = "".join(f"{day.isoformat()} 03:00:0{i % 2} rsync ok {12.0 + i * 0.2:.1f}GB\n"
                         f"{day.isoformat()} 03:00:05 done len: 3\n" for i, day in enumerate(days7))
    (demo / "backup.log").write_text(backup_log, encoding="utf-8")
    price_log = "".join(f"{day.isoformat()} 09:00:00 항공권 최저가 {312000 - i * 1500:,}원 (목표 300,000)\nlen: 1\n"
                        for i, day in enumerate(days7))
    (demo / "price.log").write_text(price_log, encoding="utf-8")
    fail_idx = {2, 5, 6}  # 7일 중 사흘 실패(오늘도 포함 → 현재 상태는 계속 fail)
    scrape_lines = []
    for i, day in enumerate(days7):
        if i in fail_idx:
            scrape_lines.append(f"{day.isoformat()} 08:00:00 Traceback (most recent call last):")
            scrape_lines.append("  HTTPError 502: Bad Gateway")
        else:
            scrape_lines.append(f"{day.isoformat()} 08:00:00 채용 공고 12건 수집 len: 12")
    (demo / "scrape.log").write_text("\n".join(scrape_lines) + "\n", encoding="utf-8")
    (demo / "growth.csv").write_text("date,members\n" + "\n".join(f"{d(-13 + i).isoformat()},{120 + i * 3 + (i % 3)}" for i in range(14)) + "\n", encoding="utf-8")
    (demo / "widgets.json").write_text(json.dumps({"allow_commands": False, "allow_run": False, "widgets": [
        {"id": "backup", "title": "NAS 백업 (매일 3시)", "kind": "log", "source": str(demo / "backup.log"), "team": "운영팀", "status": {"ok_pattern": "len:", "fail_pattern": "Traceback|Error", "stale_minutes": 1560}, "lines": 3},
        {"id": "price", "title": "항공권 최저가 (매일 9시)", "kind": "log", "source": str(demo / "price.log"), "team": "생활팀", "status": {"ok_pattern": "len:", "fail_pattern": "Traceback|Error"}, "lines": 3},
        {"id": "scrape", "title": "채용 공고 수집 (월·목)", "kind": "log", "source": str(demo / "scrape.log"), "team": "커리어팀", "status": {"ok_pattern": "len:", "fail_pattern": "Traceback|Error"}, "lines": 4},
        {"id": "growth", "title": "카페 회원 추이", "kind": "csv", "source": str(demo / "growth.csv"), "team": "콘텐츠팀", "x": "date", "y": "members", "last": 14},
    ]}, ensure_ascii=False, indent=2), encoding="utf-8")
    # 일지 데모: 하루 일지(어제, 4줄) · 주간 회고(오늘, 질문 3개 미체크)
    j_day = d(-1).isoformat()
    j_title = f"{j_day} 일지"
    j_lines = ["세컨드브레인 그래프 화면 초안을 붙였다", "카페 게시판 구조 회의에서 5개로 정리했다",
               "크론 실패 로그를 확인하고 재시도 로직을 추가했다", "저녁에 항공권 최저가 확인 후 저녁 약속에 다녀왔다"]
    j_summary = "그래프 초안, 게시판 정리, 크론 재시도 추가"
    j_body = (f"# {j_title}\n\n## 오늘\n" + "".join(f"- {l}\n" for l in j_lines)
              + "\n## 잘한 것\n- 크론 실패를 미루지 않고 바로 고쳤다\n\n## 내일 첫 일\n- 항공권 알림 임계값 다시 점검\n")
    create_note(vault, "journal", j_title, tags=["일지"], body=j_body, created=j_day,
                extra={"journal_date": j_day, "summary": j_summary})
    r_day = today.isoformat()
    r_title = f"{r_day} 주간 회고"
    r_week = ["세컨드브레인 그래프 화면을 붙였다", "카페 게시판을 5개로 줄이기로 정했다", "크론 실패가 한 번 있었고 바로 고쳤다"]
    r_questions = ["대시보드를 로컬 서버로 계속 둘지 다시 볼까", "게시판 5개 제한이 아직 맞는지", "크론 재시도 로직을 다른 위젯에도 넓힐지"]
    r_summary = "그래프 반영, 게시판 5개 확정, 크론 재시도 점검"
    r_body = (f"# {r_title}\n\n{d(-7).isoformat()} ~ {r_day} · 노트 12 · 결정 2 · 일지 5\n\n"
              "## 이번 주\n" + "".join(f"- {l}\n" for l in r_week)
              + "\n## 되돌아볼 질문\n" + "".join(f"- [ ] {q}\n" for q in r_questions))
    create_note(vault, "journal", r_title, tags=["회고"], body=r_body, created=r_day,
                extra={"journal_kind": "weekly", "journal_date": r_day, "since": d(-7).isoformat(), "summary": r_summary})
    _seed_demo_cache(today, key_meet, key_trip)


def _seed_demo_cache(today, key_meet, key_trip):
    """agenda.cache_dir() 아래(데모에서는 격리된 캐시 홈) 제안·직원요약·코어대화·날씨 캐시를 미리 채운다.
    cmd_serve --demo가 이 함수 호출 전에 XDG_CACHE_HOME을 데모 전용 경로로 바꿔 두므로, 실제
    ~/.cache/second-brain는 건드리지 않는다."""
    def d(k):
        return today + timedelta(days=k)
    now_min = datetime.now().isoformat(timespec="minutes")
    # (a) suggestions.json: 대기 중 제안 2건(일정 카드에서 채택/무시 UI 확인용)
    sugg = {
        key_meet: {"key": key_meet, "title": "팀 주간회의", "date": today.isoformat(), "end": "",
                   "location": "회의실 A", "status": "pending", "created": now_min,
                   "items": [{"kind": "prep", "text": "Loki 전환 비용표 PDF로 출력"},
                            {"kind": "prep", "text": "지난주 결정 3건 요약 슬라이드"},
                            {"kind": "memo", "text": "OpenSearch 잔여 비용 질문 대비해 두기"}]},
        key_trip: {"key": key_trip, "title": "제주 출장", "date": d(3).isoformat(), "end": d(5).isoformat(),
                   "location": "김포국제공항", "status": "pending", "created": now_min,
                   "items": [{"kind": "prep", "text": "우산·경량 패딩 챙기기"},
                            {"kind": "step", "text": "08:30 공항 리무진 예약 확인"},
                            {"kind": "memo", "text": "고객사 미팅 자료 최종본인지 재확인"}]},
    }
    save_suggestions(sugg)
    # (b) staff_briefs.json: 위젯 2개의 「이번 주 한 줄」(Claude 없이도 보이도록 이미 채운 캐시)
    briefs = {
        "backup": {"id": "backup", "date": today.isoformat(), "did": "NAS 백업 7회 모두 성공, 총 12~14GB 처리",
                   "issue": "문제 없음", "mood": "순조로움", "runs_7d": 14, "fails_7d": 0},
        "scrape": {"id": "scrape", "date": today.isoformat(), "did": "채용 공고 수집 7회 중 4회 성공(48건)",
                   "issue": "사이트 응답 502로 3회 실패", "mood": "지쳤어요", "runs_7d": 7, "fails_7d": 3},
    }
    staff_briefs_path().parent.mkdir(parents=True, exist_ok=True)
    staff_briefs_path().write_text(json.dumps(briefs, ensure_ascii=False, indent=1), encoding="utf-8")
    # (c) core_log.jsonl: 오늘의 코어 문답 3줄
    core_qa = [
        ("오늘 뭐부터 하면 돼?", "오늘은 10시 팀 주간회의와 15시 치과가 있고, 회계사 답장이 3일째 기다림 중이에요."),
        ("제주 출장 준비 뭐 남았어?", "렌터카 예약 확인과 고객사 방문 자료 인쇄가 아직 남아 있어요."),
        ("항공권 최저가 위젯 잘 돌아가?", "네, 매일 9시에 돌아가고 있고 최근 최저가는 하락하는 추세예요."),
    ]
    p = core_log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        for i, (q, a) in enumerate(core_qa):
            ts = f"{today.isoformat()}T{8 + i * 4:02d}:15"
            f.write(json.dumps({"ts": ts, "q": q, "a": a}, ensure_ascii=False) + "\n")
    # (d) weather 캐시: 「제주 출장」의 출발지(김포국제공항) 지오코딩+예보를 미리 채워 오프라인에서도
    # weather_for()가 풀리게 한다. attach_event_notes가 아직 날씨를 붙이지 않는 스냅샷이라도, 다른
    # 워커가 날씨 연동을 붙였을 때 곧바로 동작하도록 캐시 형식(weather.py 기준)에 맞춰 미리 심어 둔다.
    weather_dir = agenda_mod.cache_dir() / "weather"
    weather_dir.mkdir(parents=True, exist_ok=True)
    place = "김포국제공항"
    lat, lon = 37.5583, 126.7906
    geocode_cache = {place: {"result": {"name": place, "lat": lat, "lon": lon, "country": "대한민국", "admin1": "서울특별시"},
                             "ts": time.time()}}
    (weather_dir / "geocode.json").write_text(json.dumps(geocode_cache, ensure_ascii=False), encoding="utf-8")
    forecast_days = [{"date": d(k).isoformat(), "code": 1 if k != 4 else 61,
                      "tmax": 24.0 - k * 0.3, "tmin": 15.0 - k * 0.2,
                      "rain_prob": 10 if k != 4 else 70, "rain_mm": 0.0 if k != 4 else 6.5} for k in range(0, 7)]
    forecast_cache = {"_ts": time.time(), "data": {"days": forecast_days, "fetched_at": now_min}}
    lat4, lon4 = f"{lat:.4f}", f"{lon:.4f}"
    (weather_dir / f"forecast-{lat4}-{lon4}.json").write_text(json.dumps(forecast_cache, ensure_ascii=False), encoding="utf-8")


def demo_overrides(vault):
    """데모 서버용 설정 덧씌우기: 데모 캘린더·위젯·이름. 파일 설정은 건드리지 않는다."""
    demo = Path(vault) / "_demo"
    return {"calendar": {"sources": [{"kind": "ics", "name": "데모 캘린더", "path": str(demo / "calendar.ics")}]},
            "widgets_path": str(demo / "widgets.json"), "assistant_name": "데모 비서", "mail": None}


# ---------------------------------------------------------------------------
# 비서 모드 (v0.3) — 위젯(widgets.json) + 오늘 브리핑
# ---------------------------------------------------------------------------

WIDGET_KINDS = ("log", "json", "csv", "markdown", "command")
WIDGET_STATUSES = ("ok", "warn", "fail", "stale", "missing", "unknown")
SUMMARY_STATUSES = ("ok", "warn", "fail", "stale", "missing", "paused")  # /api/today pill 6개
STATUS_PRIORITY = {"fail": 0, "stale": 1, "missing": 2, "warn": 3, "unknown": 4, "ok": 5, "paused": 6}
WIDGET_STATES = ("active", "paused")  # paused = 멈춘 자동화. 평가는 하되 status는 항상 paused, 요약·카톡 경고에서 제외
WIDGET_CACHE_SEC = 60
COMMAND_STDOUT_MAX = 4096
SUMMARY_MAX = 200
KAKAO_MAX = 200
WEEKDAYS_KO = ("월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일")
INBOX_TODO_RE = re.compile(r"^\s*[-*]\s+\[ \]\s+(.+?)\s*$")

EXAMPLE_WIDGETS = {
    "allow_commands": False,
    "widgets": [
        {"id": "marketset", "title": "마켓세트 카톡", "kind": "log",
         "source": "~/.local/k-skill-cron/marketset.log",
         "status": {"ok_pattern": "len:", "fail_pattern": "Traceback|Error", "stale_minutes": 1500},
         "lines": 5},
        {"id": "cafe-growth", "title": "카페 회원 추이", "kind": "csv",
         "source": "~/.local/naver-publish/growth.csv", "x": "date", "y": "members", "last": 30},
        {"id": "flight", "title": "항공권 최저가", "kind": "json",
         "source": "~/.local/k-skill-cron/flight_state.json",
         "fields": ["best_price", "route", "checked_at"]},
        {"id": "jobscout", "title": "채용 스카우트", "kind": "markdown",
         "source": "~/.local/k-skill-cron/jobscout_result.md", "lines": 8},
        {"id": "disk", "title": "디스크", "kind": "command", "source": "df -h / | tail -1",
         "timeout_sec": 5},
    ],
}


def widgets_config_path():
    if CONFIG_OVERRIDES.get("widgets_path"):
        return Path(CONFIG_OVERRIDES["widgets_path"])
    return home_dir() / ".config" / "second-brain" / "widgets.json"


def load_widgets_config():
    """widgets.json 로드. 없거나 깨졌으면 빈 목록(깨진 경우 error 포함)."""
    p = widgets_config_path()
    empty = {"allow_commands": False, "allow_run": False, "allow_hire": False, "widgets": [], "path": str(p), "error": None}
    if not p.is_file():
        return empty
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log(f"경고: widgets.json을 읽지 못했습니다({e}).")
        return dict(empty, error=f"widgets.json 파싱 실패: {e}")
    if not isinstance(data, dict):
        return dict(empty, error="widgets.json 최상위는 객체여야 합니다.")
    items = data.get("widgets")
    if not isinstance(items, list):
        items = []
    return {"allow_commands": data.get("allow_commands") is True, "allow_run": data.get("allow_run") is True,
            "allow_hire": data.get("allow_hire") is True,
            "widgets": [w for w in items if isinstance(w, dict)], "path": str(p), "error": None}


def init_widgets_config():
    """예시 widgets.json 생성. 이미 있으면 덮어쓰지 않고 (path, False)."""
    p = widgets_config_path()
    if p.exists():
        return p, False
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(EXAMPLE_WIDGETS, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p, True


def resolve_widget_source(raw):
    """위젯 source 경로 → 홈 아래 실경로. `..`·홈 밖(심볼릭 링크 포함)·상대경로는 BrainError."""
    if not isinstance(raw, str) or not raw.strip():
        raise BrainError("source가 비어 있습니다.")
    if ".." in Path(raw).parts:
        raise BrainError(f"source에 '..'는 쓸 수 없습니다: {raw}")
    expanded = os.path.expanduser(raw)
    if not os.path.isabs(expanded):
        raise BrainError(f"source는 ~ 또는 절대경로여야 합니다: {raw}")
    return ensure_in_home(expanded, "source")


def _clip(s, n=SUMMARY_MAX):
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _tail_lines(path, n, max_bytes=262144):
    """파일 끝 n줄(빈 줄 제외). 큰 로그도 끝 max_bytes만 읽는다."""
    size = path.stat().st_size
    with path.open("rb") as f:
        if size > max_bytes:
            f.seek(size - max_bytes)
        raw = f.read()
    lines = [ln.rstrip("\r") for ln in raw.decode("utf-8", errors="replace").split("\n")]
    if size > max_bytes and len(lines) > 1:
        rest = lines[1:]
        if any(ln.strip() for ln in rest):
            lines = rest  # 잘린 첫 줄 버림(뒤에 완전한 줄이 남아 있을 때만)
        # else: 창 전체가 한 줄(초대형 로그 줄)뿐이면 버리지 않고 잘린 내용이라도 남긴다
    return [ln for ln in lines if ln.strip()][-n:] if n > 0 else []


def _pos_int(v, default, hi=10000):
    try:
        v = int(v)
    except (TypeError, ValueError):
        return default
    return max(1, min(hi, v))


def _regex(pat):
    if not pat:
        return None
    try:
        return re.compile(str(pat))
    except re.error as e:
        raise BrainError(f"정규식 오류({pat}): {e}")


def _num(v):
    """문자열 → 숫자(쉼표·공백 허용). 실패 시 None."""
    s = str(v).strip().replace(",", "")
    if not s:
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return int(f) if f.is_integer() else f


def _fmt_num(v):
    if isinstance(v, float):
        return f"{v:,.2f}".rstrip("0").rstrip(".")
    return f"{v:,}"


def _dig(obj, dotted):
    """점 경로(a.b.0) 조회. 없으면 KeyError."""
    cur = obj
    for part in str(dotted).split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and re.fullmatch(r"-?\d+", part) and -len(cur) <= int(part) < len(cur):
            cur = cur[int(part)]
        else:
            raise KeyError(dotted)
    return cur


def _widget_log(w, path):
    st = w.get("status") if isinstance(w.get("status"), dict) else {}
    lines = _tail_lines(path, _pos_int(w.get("lines"), 5))
    fail_re, ok_re = _regex(st.get("fail_pattern")), _regex(st.get("ok_pattern"))
    text = "\n".join(lines)
    if fail_re and fail_re.search(text):
        status = "fail"
        hit = [ln for ln in lines if fail_re.search(ln)]
        summary = hit[-1]
    elif ok_re and ok_re.search(text):
        status, summary = "ok", lines[-1]
    else:
        status, summary = "unknown", (lines[-1] if lines else "빈 로그")
    return status, summary, {"lines": lines}


def _widget_json(w, path):
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        return "warn", f"JSON 파싱 실패: {e}", {"fields": {}}
    fields = w.get("fields")
    out, missing = {}, []
    if isinstance(fields, list) and fields:
        for f in fields:
            try:
                out[str(f)] = _dig(obj, f)
            except KeyError:
                out[str(f)] = None
                missing.append(str(f))
    elif isinstance(obj, dict):
        out = {k: v for k, v in list(obj.items())[:10] if not isinstance(v, (dict, list))}
    shown = [f"{k}: {v if not isinstance(v, (dict, list)) else json.dumps(v, ensure_ascii=False)}"
             for k, v in out.items() if v is not None]
    summary = " · ".join(shown) or "값 없음"
    if missing:
        return "warn", f"없는 필드: {', '.join(missing)} · {summary}", {"fields": out}
    return "ok", summary, {"fields": out}


def _csv_change(rows):
    """최근 값과 30일 전(또는 창의 첫 행) 대비 변화."""
    last_x, last_y = rows[-1]
    base = rows[0][1]
    try:
        last_d = parse_date(str(last_x)[:10])
        cut = (last_d - timedelta(days=30)).isoformat()
        for x, y in rows:
            if str(x)[:10] >= cut:
                base = y
                break
    except BrainError:
        pass
    return last_y, last_y - base


def _widget_csv(w, path):
    xcol, ycol = w.get("x"), w.get("y")
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        cols = reader.fieldnames or []
        if not xcol or not ycol or xcol not in cols or ycol not in cols:
            return "warn", f"열을 찾지 못했습니다(x={xcol}, y={ycol}, 헤더={cols})", \
                {"columns": [xcol, ycol], "rows": []}
        rows = []
        for r in reader:
            y = _num(r.get(ycol) or "")
            if y is None:
                continue  # 숫자 변환 실패 행은 건너뜀
            rows.append([r.get(xcol), y])
    rows = rows[-_pos_int(w.get("last"), 30):]
    data = {"columns": [xcol, ycol], "rows": rows}
    if not rows:
        return "warn", "숫자 데이터 없음", data
    last, delta = _csv_change(rows)
    sign = "+" if delta > 0 else ""
    return "ok", f"최근 {_fmt_num(last)} / 30일 변화 {sign}{_fmt_num(delta)}", data


def _widget_markdown(w, path):
    lines = path.read_text(encoding="utf-8", errors="replace").split("\n")[:_pos_int(w.get("lines"), 10)]
    text = "\n".join(lines).strip()
    first = next((re.sub(r"^[#>\-*\s]+", "", ln).strip() for ln in lines if ln.strip()), "")
    return ("ok", first, {"text": text}) if text else ("unknown", "빈 문서", {"text": ""})


def _widget_command(w, allow):
    if not allow:
        return "unknown", "명령 실행 비활성(allow_commands)", {"stdout": "", "exit_code": None}
    cmd = w.get("source")
    if not isinstance(cmd, str) or not cmd.strip():
        raise BrainError("command source가 비어 있습니다.")
    timeout = _pos_int(w.get("timeout_sec"), 10, hi=60)
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, timeout=timeout, cwd=str(home_dir()))
    except subprocess.TimeoutExpired:
        return "fail", f"시간 초과({timeout}초)", {"stdout": "", "exit_code": None}
    out = r.stdout[:COMMAND_STDOUT_MAX].decode("utf-8", errors="ignore")
    last = next((ln for ln in reversed(out.split("\n")) if ln.strip()), "")
    data = {"stdout": out, "exit_code": r.returncode}
    if r.returncode != 0:
        err = r.stderr[:500].decode("utf-8", errors="ignore").strip()
        return "fail", f"exit {r.returncode}" + (f": {err or last}" if (err or last) else ""), data
    return "ok", last or "(출력 없음)", data


_FILE_HANDLERS = {"log": _widget_log, "json": _widget_json, "csv": _widget_csv,
                  "markdown": _widget_markdown}
_EMPTY_DATA = {"log": {"lines": []}, "json": {"fields": {}}, "csv": {"columns": [], "rows": []},
               "markdown": {"text": ""}, "command": {"stdout": "", "exit_code": None}}


def _widget_base(w, idx):
    wid = str(w.get("id") or f"widget-{idx + 1}")
    kind = str(w.get("kind") or "")
    state = str(w.get("state") or "active").lower()
    return {"id": wid, "title": str(w.get("title") or wid), "kind": kind, "status": "unknown",
            "state": state if state in WIDGET_STATES else "active", "team": str(w.get("team") or ""),
            "source": str(w.get("source") or "") if kind != "command" else "",
            "status_cfg": w.get("status") if isinstance(w.get("status"), dict) else {},
            "updated_at": None, "age_minutes": None, "summary": "",
            "data": dict(_EMPTY_DATA.get(kind, {}))}


def _widget_mtime(w):
    """캐시 키용 mtime(파일 kind). 경로 오류·없음은 None."""
    if w.get("kind") == "command":
        return None
    try:
        p = resolve_widget_source(w.get("source"))
        return p.stat().st_mtime if p.is_file() else None
    except (BrainError, OSError):
        return None


def evaluate_widget(w, idx=0, allow_commands=False, now=None):
    """위젯 1개 평가 → {id,title,kind,state,status,updated_at,age_minutes,summary,data[,error]}.

    state=paused(멈춘 자동화)면 내용은 그대로 읽되 status는 paused로 고정 — 실패·지연 경고를 내지 않는다."""
    res = _evaluate_active(w, idx, allow_commands, now)
    if res["state"] == "paused":
        res["status"] = "paused"
        res["summary"] = _clip("멈춤 · " + (res["summary"] or "마지막 기록 보존"))
    return res


def _evaluate_active(w, idx=0, allow_commands=False, now=None):
    now = now or datetime.now().astimezone()
    res = _widget_base(w, idx)
    kind = res["kind"]
    try:
        if kind not in WIDGET_KINDS:
            raise BrainError(f"알 수 없는 kind: {kind or '(없음)'}")
        if kind == "command":
            status, summary, data = _widget_command(w, allow_commands)
            if data.get("exit_code") is not None or status == "fail":
                res["updated_at"], res["age_minutes"] = now.isoformat(timespec="seconds"), 0
        else:
            try:
                path = resolve_widget_source(w.get("source"))
            except BrainError as e:
                res.update(status="missing", summary="경로 거부", error=str(e))
                return res
            if not path.is_file():
                res.update(status="missing", summary=f"파일 없음: {w.get('source')}")
                return res
            mtime = datetime.fromtimestamp(path.stat().st_mtime).astimezone()
            age = max(0, int((now - mtime).total_seconds() // 60))
            res["updated_at"], res["age_minutes"] = mtime.isoformat(timespec="seconds"), age
            status, summary, data = _FILE_HANDLERS[kind](w, path)
            st = w.get("status") if isinstance(w.get("status"), dict) else {}
            stale = _num(st.get("stale_minutes", w.get("stale_minutes", "")) or "")
            if stale is not None and stale > 0 and age > stale:
                status = "stale"  # stale이 패턴 판정보다 우선
                summary = f"{age}분째 갱신 없음 · {summary}"
        res.update(status=status, summary=_clip(summary), data=data)
    except BrainError as e:
        res.update(status="unknown", summary=_clip(str(e)), error=str(e))
    except (OSError, UnicodeError) as e:
        res.update(status="warn", summary=_clip(f"읽기 실패: {e}"), error=str(e))
    return res


class WidgetCache:
    """위젯별 60초 캐시. 키=위젯 설정, 무효화=mtime 변경 또는 60초 경과. parses=실제 평가 횟수."""

    def clear(self):
        with self._lock:
            self._store.clear()

    def __init__(self, ttl=WIDGET_CACHE_SEC):
        self.ttl = ttl
        self.parses = 0
        self._store = {}
        self._lock = threading.Lock()

    def get(self, w, idx, allow_commands, clock=None):
        t = (clock or time.time)()
        key = json.dumps([w, idx, allow_commands], sort_keys=True, ensure_ascii=False, default=str)
        mtime = _widget_mtime(w)
        with self._lock:
            hit = self._store.get(key)
            if hit and hit[0] == mtime and t - hit[1] < self.ttl:
                return hit[2]
        res = evaluate_widget(w, idx, allow_commands)
        with self._lock:
            self.parses += 1
            self._store[key] = (mtime, t, res)
        return res


def collect_widgets(cache=None):
    cfg = load_widgets_config()
    out = []
    for i, w in enumerate(cfg["widgets"]):
        out.append(cache.get(w, i, cfg["allow_commands"]) if cache
                   else evaluate_widget(w, i, cfg["allow_commands"]))
    # 진행 중(active) 먼저, 멈춘 것(paused)은 뒤로 — 설정 순서는 각 그룹 안에서 유지
    return sorted(out, key=lambda r: r.get("state") == "paused")


def greeting_for(hour):
    if 5 <= hour < 11:
        return "좋은 아침이에요"
    if 11 <= hour < 17:
        return "오후예요"
    if 17 <= hour < 22:
        return "저녁이에요"
    return "늦은 시간이에요"


def parse_inbox(vault):
    p = vault / "inbox.md" if vault else None
    if not p or not p.is_file():
        return []
    out = []
    for ln in p.read_text(encoding="utf-8", errors="replace").split("\n"):
        m = INBOX_TODO_RE.match(ln)
        if m:
            out.append(m.group(1))
    return out


TASK_LINE_RE = re.compile(r"^(\s*[-*]\s+\[)( |x|X)(\]\s+)(.*)$")
TAG_RE = re.compile(r"@(due|since|waiting|project)\(([^)]*)\)|@(someday|today|tomorrow)\b")


def parse_task_line(text):
    """'- [ ] 세무사 답장 @waiting(세무사) @since(2026-09-28)' 의 본문 부분을 태그와 내용으로."""
    tags = {"due": None, "someday": False, "waiting": None, "since": None, "project": None}
    def take(m):
        k, v, bare = m.group(1), m.group(2), m.group(3)
        if bare == "someday":
            tags["someday"] = True
        elif bare in ("today", "tomorrow"):
            tags["due"] = bare  # 상대 표현은 호출자가 날짜로 바꿈
        elif k in ("due", "since"):
            tags[k] = (v or "").strip()[:10]
        elif k == "waiting":
            tags["waiting"] = (v or "").strip() or "?"
        elif k == "project":
            tags["project"] = (v or "").strip()
        return ""
    body = TAG_RE.sub(take, text)
    body = " ".join(body.split())
    return body, tags


def parse_tasks(vault, today=None):
    """inbox.md의 체크박스 줄 전부(완료 포함). 반환 항목: {line, text, done, due, someday, waiting, since, project, raw}."""
    today = today or date.today()
    p = vault / "inbox.md" if vault else None
    if not p or not p.is_file():
        return []
    out = []
    for i, ln in enumerate(p.read_text(encoding="utf-8", errors="replace").split("\n")):
        m = TASK_LINE_RE.match(ln)
        if not m:
            continue
        body, tags = parse_task_line(m.group(4))
        due = tags["due"]
        if due == "today":
            due = today.isoformat()
        elif due == "tomorrow":
            due = (today + timedelta(days=1)).isoformat()
        if due and not DATE_RE.match(due):
            due = None
        out.append({"line": i, "text": body, "done": m.group(2).lower() == "x", "due": due, "someday": tags["someday"],
                    "waiting": tags["waiting"], "since": tags["since"], "project": tags["project"], "raw": ln, "kind": "task"})
    return out


def bucket_tasks(tasks, today=None):
    """4묶음: today(마감 지났거나 오늘), week(7일 안), someday(@someday 또는 마감 없음), waiting(@waiting). 완료는 done_recent."""
    today = today or date.today()
    t_iso, w_iso = today.isoformat(), (today + timedelta(days=7)).isoformat()
    b = {"today": [], "week": [], "someday": [], "waiting": [], "done_recent": []}
    for t in tasks:
        if t.get("done"):
            b["done_recent"].append(t)
            continue
        t = dict(t)
        if t.get("due"):
            t["days_left"] = (date.fromisoformat(t["due"]) - today).days
        if t.get("waiting"):
            if t.get("since") and DATE_RE.match(t["since"]):
                t["waiting_days"] = (today - date.fromisoformat(t["since"])).days
            b["waiting"].append(t)
        elif t.get("due") and t["due"] <= t_iso:
            b["today"].append(t)
        elif t.get("due") and t["due"] <= w_iso:
            b["week"].append(t)
        elif t.get("someday") or not t.get("due"):
            b["someday"].append(t)
        else:
            b["week"].append(t)
    for k in ("today", "week"):
        b[k].sort(key=lambda x: (x.get("due") or "9999", x.get("line", 0)))
    b["done_recent"] = b["done_recent"][-5:]
    return b


def derived_tasks(vault, today, widgets=None, agenda=None):
    """볼트·자동화·일정에서 자동으로 생기는 할 일. 결정 되돌아볼 날(7일 안), 자동화 실패·지연, 일정 준비 미완(7일 안)."""
    out = []
    notes = load_notes(vault) if vault else []
    for d in _revisit_soon(notes, today):
        out.append({"kind": "decision", "text": "결정 되돌아보기: " + d["title"], "due": d["revisit"], "days_left": d["days_left"],
                    "path": d["path"], "done": False})
    for w in widgets or []:
        if w.get("status") in ("fail", "stale") and w.get("state") != "paused":
            out.append({"kind": "automation", "text": ("실패 확인: " if w["status"] == "fail" else "오래된 자동화: ") + re.sub(r"\s*\(.*\)\s*$", "", w["title"]),
                        "due": today.isoformat(), "days_left": 0, "wid": w["id"], "summary": w.get("summary") or "", "done": False})
    for e in ((agenda or {}).get("today") or []) + ((agenda or {}).get("upcoming") or []):
        n = e.get("note")
        if n and n.get("total") and n["done"] < n["total"] and (e.get("days_left") is None or e["days_left"] <= 7):
            for c in n["checklist"]:
                if not c["done"]:
                    out.append({"kind": "prep", "text": e["title"] + " 준비: " + c["text"], "due": e["start"][:10], "days_left": e.get("days_left"),
                                "path": n["path"], "line": c["line"], "event_key": e.get("key"), "done": False})
    return out


def reminder_tasks(cfg, today):
    """설정에서 켜져 있으면 맥 미리알림을 읽기 전용 할 일 항목(kind=reminder)으로 바꾼다.
    실패해도 대시보드는 살아야 하므로 예외는 로그만 남기고 빈 목록을 준다."""
    rcfg = cfg.get("reminders") if isinstance(cfg, dict) else None
    if not isinstance(rcfg, dict) or not rcfg.get("enabled"):
        return []
    try:
        items = reminders_mod.fetch_reminders(lists=rcfg.get("lists") or None)
    except Exception as e:  # noqa: BLE001 - 미리알림 실패해도 대시보드는 살아야 한다
        log(f"경고: 미리알림 수집 실패({type(e).__name__}: {e})")
        return []
    out = []
    for r in items:
        due = r.get("due")
        days_left = (date.fromisoformat(due) - today).days if due and DATE_RE.match(str(due)) else None
        out.append({"kind": "reminder", "text": r.get("title") or "(제목 없음)", "due": due, "days_left": days_left,
                    "source": r.get("list") or "", "rid": r.get("id"), "done": False})
    return out


def dash_tasks(vault, today=None, widgets=None, agenda=None):
    """/api/tasks. inbox 할 일 + 파생 할 일 + 미리알림(켜져 있으면)을 4묶음으로."""
    today = today or date.today()
    own = parse_tasks(vault, today) if vault else []
    b = bucket_tasks(own, today)
    for d in derived_tasks(vault, today, widgets, agenda) + reminder_tasks(load_config(), today):
        dl = d.get("days_left")
        if dl is not None and dl <= 0:
            b["today"].append(d)
        elif dl is not None and dl <= 7:
            b["week"].append(d)
        else:
            b["someday"].append(d)
    b["today"].sort(key=lambda x: (x.get("due") or "9999", x.get("kind") != "task"))
    b["week"].sort(key=lambda x: (x.get("due") or "9999"))
    b["counts"] = {k: len(b[k]) for k in ("today", "week", "someday", "waiting")}
    b["date"] = today.isoformat()
    return b


def task_action(vault, body, today=None):
    """쓰기: add(text, due|someday|waiting|since|project) · check(line, done) · move(line, to: today|tomorrow|week|someday|clear) · remove(line)."""
    today = today or date.today()
    action = body.get("action")
    if action == "check" and str(body.get("kind") or "") == "reminder":
        raise BrainError("미리알림은 미리알림 앱에서 완료해 주세요")
    p = vault / "inbox.md"
    if not p.is_file():
        p.write_text("# inbox\n\n", encoding="utf-8")
    lines = p.read_text(encoding="utf-8").split("\n")
    if action == "add":
        text = " ".join(str(body.get("text") or "").split())
        if not text:
            raise BrainError("내용이 비었어요")
        if len(text) > 300:
            raise BrainError("할 일은 300자까지")
        tags = []
        due = body.get("due")
        if due in ("today", "tomorrow"):
            due = (today + timedelta(days=1 if due == "tomorrow" else 0)).isoformat()
        if due:
            parse_date(due, "마감")
            tags.append(f"@due({due})")
        if body.get("someday"):
            tags.append("@someday")
        if body.get("waiting"):
            tags.append(f"@waiting({str(body['waiting']).strip()[:40]}) @since({today.isoformat()})")
        if body.get("project"):
            tags.append(f"@project({str(body['project']).strip()[:40]})")
        line = f"- [ ] {text}" + (" " + " ".join(tags) if tags else "")
        while lines and not lines[-1].strip():
            lines.pop()
        lines.append(line)
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        git_commit(vault, "brain: task add")
        return {"ok": True, "line": len(lines) - 1, "text": line}
    if action == "carry":
        # 오늘 묶음(마감 지났거나 오늘, 기다림 제외)의 미완료 항목을 전부 내일로
        moved = 0
        tomorrow = (today + timedelta(days=1)).isoformat()
        for i2, ln in enumerate(lines):
            m2 = TASK_LINE_RE.match(ln)
            if not m2 or m2.group(2).lower() == "x":
                continue
            _, tags = parse_task_line(m2.group(4))
            due = tags["due"]
            if due == "today":
                due = today.isoformat()
            if tags["waiting"] or not due or not DATE_RE.match(due) or due > today.isoformat():
                continue
            rest = " ".join(TAG_RE.sub(lambda mm: "" if mm.group(1) == "due" or mm.group(3) in ("today", "tomorrow") else mm.group(0), m2.group(4)).split())
            lines[i2] = f"{m2.group(1)}{m2.group(2)}{m2.group(3)}{rest} @due({tomorrow})"
            moved += 1
        p.write_text("\n".join(lines), encoding="utf-8")
        git_commit(vault, "brain: task carry")
        return {"ok": True, "moved": moved, "to": tomorrow}
    i = int(body.get("line", -1))
    if not (0 <= i < len(lines)) or not TASK_LINE_RE.match(lines[i]):
        raise BrainError("할 일 줄이 아니에요(inbox.md가 바뀌었을 수 있어요. 새로 고쳐 주세요)")
    m = TASK_LINE_RE.match(lines[i])
    if action == "check":
        mark = "x" if body.get("done", True) else " "
        lines[i] = f"{m.group(1)}{mark}{m.group(3)}{m.group(4)}"
    elif action == "move":
        to = str(body.get("to") or "")
        rest = TAG_RE.sub(lambda mm: "" if mm.group(1) in ("due",) or mm.group(3) in ("someday", "today", "tomorrow") else mm.group(0), m.group(4))
        rest = " ".join(rest.split())
        if to in ("today", "tomorrow"):
            rest += f" @due({(today + timedelta(days=1 if to == 'tomorrow' else 0)).isoformat()})"
        elif to == "week":
            rest += f" @due({(today + timedelta(days=7)).isoformat()})"
        elif to == "someday":
            rest += " @someday"
        elif to == "clear":
            pass
        elif DATE_RE.match(to):
            parse_date(to, "마감")
            rest += f" @due({to})"
        else:
            raise BrainError("to는 today · tomorrow · week · someday · clear · YYYY-MM-DD")
        lines[i] = f"{m.group(1)}{m.group(2)}{m.group(3)}{rest}"
    elif action == "remove":
        del lines[i]
    else:
        raise BrainError("action은 add · check · move · remove · carry 중 하나")
    p.write_text("\n".join(lines), encoding="utf-8")
    git_commit(vault, f"brain: task {action}")
    return {"ok": True}


def retro_questions(notes, today, days=14, limit=3):
    """가장 최근 주간 회고(14일 안)의 「되돌아볼 질문」 중 아직 체크하지 않은 것. 보드·코어에 노출, 체크는 event-note check로."""
    since = (today - timedelta(days=days)).isoformat()
    weekly = [n for n in notes if n.type == "journal" and n.stem.endswith("-weekly") and n.created >= since]
    if not weekly:
        return []
    n = max(weekly, key=lambda x: x.created)
    sec = note_sections(n.body).get("되돌아볼 질문", "")
    if not sec:
        return []
    # 절의 줄 번호를 본문 기준으로 맞춘다
    lines = n.body.split("\n")
    try:
        start = next(i for i, l in enumerate(lines) if l.strip() == "## 되돌아볼 질문")
    except StopIteration:
        return []
    out = []
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("## "):
            break
        m = CHECKBOX_RE.match(lines[j])
        if m and m.group(2).lower() != "x":
            out.append({"text": m.group(4).strip(), "line": j, "path": n.rel, "date": n.created})
    return out[:limit]


def week_plan(notes, today, days=14, limit=5):
    """가장 최근 주간 회고(14일 안)의 「다음 주」 중 아직 체크하지 않은 것. 보드·코어에 노출, 체크는 event-note check로."""
    since = (today - timedelta(days=days)).isoformat()
    weekly = [n for n in notes if n.type == "journal" and n.stem.endswith("-weekly") and n.created >= since]
    if not weekly:
        return []
    n = max(weekly, key=lambda x: x.created)
    sec = note_sections(n.body).get("다음 주", "")
    if not sec:
        return []
    # 절의 줄 번호를 본문 기준으로 맞춘다
    lines = n.body.split("\n")
    try:
        start = next(i for i, l in enumerate(lines) if l.strip() == "## 다음 주")
    except StopIteration:
        return []
    out = []
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("## "):
            break
        m = CHECKBOX_RE.match(lines[j])
        if m and m.group(2).lower() != "x":
            out.append({"text": m.group(4).strip(), "line": j, "path": n.rel, "date": n.created})
    return out[:limit]


def _revisit_soon(notes, today, days=7):
    limit = (today + timedelta(days=days)).isoformat()
    out = []
    for n in notes:
        if n.type != "decision" or _decision_status(n) != "open":
            continue
        rv = str(n.meta.get("revisit") or "")[:10]
        if rv and DATE_RE.match(rv) and rv <= limit:
            out.append({"path": n.rel, "title": n.title, "revisit": rv,
                        "days_left": _days_left(rv, today), "project": n.project})
    out.sort(key=lambda d: (d["revisit"], d["path"]))
    return out


def _dleft(d):
    k = d["days_left"]
    return "오늘" if k == 0 else (f"D-{k}" if k > 0 else f"{-k}일 지남")


def _short(title):
    return re.sub(r"\s*\((?:[^()]*)\)\s*$", "", str(title or "")).strip()


def kakao_brief(t):
    """카톡용 요약(≤200자). 읽는 사람 기준으로: 날짜 · 일정(없으면 내일) · 동선 · 메일 · 막힌 자동화 · 할 일 · 되돌아볼 결정 · 이번 주."""
    d = t["date"]
    parts = [f"[{int(d[5:7])}/{int(d[8:10])} {t['weekday'][0]}] {t['greeting']}"]
    ag = t.get("agenda") or {}
    ag_bit = agenda_mod.agenda_kakao(ag)
    if ag_bit:
        parts.append(ag_bit)
    else:
        tomorrow = [e for e in (ag.get("upcoming") or []) if e.get("days_left") == 1]
        bits = []
        for i, e in enumerate(tomorrow[:2]):
            bit = ("종일 " if e["all_day"] else e["start"][11:16] + " ") + e["title"]
            if i == 0:
                matched = [p for p in (e.get("people") or []) if p.get("matched")]
                if matched:
                    extra = f" 외 {len(matched) - 1}" if len(matched) > 1 else ""
                    mentions = sum(p.get("mentions") or 0 for p in matched)
                    bit += f" ({matched[0]['name']}{extra}, 기록 {mentions}건)"
            bits.append(bit)
        parts.append("오늘 일정 없음" + (", 내일 " + ", ".join(bits) if tomorrow else ""))
    # 날씨: 내일(없으면 오늘) 일정 중 날씨가 붙은 첫 건이 우산/추위/더위처럼 눈에 띌 때만 한 줄 추가(200자 예산 절약)
    tomorrow_all = [e for e in (ag.get("upcoming") or []) if e.get("days_left") == 1]
    weather_source = [e for e in tomorrow_all if e.get("weather")] or [e for e in (ag.get("today") or []) if e.get("weather")]
    weather_notable = [e["weather"] for e in weather_source if e["weather"].get("umbrella") or e["weather"].get("cold") or e["weather"].get("hot")]
    if weather_notable:
        parts.append(weather_mod.weather_sentence(weather_notable))
    if ag.get("steps_today"):
        _strip_paren = lambda x: re.sub(r"\s*\(.*\)\s*$", "", x)  # 3.9~3.11은 f-string 식 안에 백슬래시 불가
        parts.append("동선 " + ", ".join(f"{s_['time']} {_strip_paren(s_['text'])}" for s_ in ag["steps_today"][:2]))
    mk = mail_mod.mail_kakao(t.get("mail") or {})
    if mk:
        parts.append(mk)
    bad = [w for w in t["top_widgets"] if w["status"] in ("fail", "stale")]
    if bad:
        parts.append("자동화 " + ", ".join(f"{_short(w['title'])} {'실패' if w['status'] == 'fail' else '오래됨'}" for w in bad[:3]))
    elif sum(t["widgets_summary"].values()):
        parts.append(f"자동화 정상 {t['widgets_summary']['ok']}")
    if (t.get("suggestions") or {}).get("count"):
        parts.append(f"준비 제안 {t['suggestions']['count']}건(보드에서 채택)")
    tk = (t.get("tasks") or {}).get("counts") or {}
    own_today = [x for x in (t.get("tasks") or {}).get("today", []) if x.get("kind") == "task"]
    if own_today:
        parts.append(f"할 일 {len(own_today)}: " + ", ".join(x["text"][:14] for x in own_today[:2]) + (" 등" if len(own_today) > 2 else ""))
    elif tk.get("week") or tk.get("waiting") or tk.get("someday"):
        parts.append("할 일 오늘 0" + (f"·이번 주 {tk['week']}" if tk.get("week") else "") + (f"·언젠가 {tk['someday']}" if tk.get("someday") else "") + (f"·기다림 {tk['waiting']}" if tk.get("waiting") else ""))
    rem_n = sum(1 for x in (t.get("tasks") or {}).get("today", []) + (t.get("tasks") or {}).get("week", []) + (t.get("tasks") or {}).get("someday", []) if x.get("kind") == "reminder")
    if rem_n:
        parts.append(f"미리알림 {rem_n}")
    if t["revisit"]:
        parts.append("되돌아볼 결정 " + ", ".join(f"{_short(d_['title'])[:16]}({_dleft(d_)})" for d_ in t["revisit"][:2]))
    w = t["this_week"]
    parts.append(f"이번 주 노트 {w['new_notes']}·결정 {w['new_decisions']}")
    return _clip(" / ".join(parts), KAKAO_MAX)


class AgendaCache:
    """일정 60초 캐시(ICS는 어댑터가 15분 캐시, EventKit 호출을 줄이기 위한 층). days별로 따로."""

    def __init__(self):
        self._lock = threading.Lock()
        self._data = {}

    def invalidate(self):
        with self._lock:
            self._data.clear()

    def get(self, days=7):
        with self._lock:
            hit = self._data.get(days)
            if hit and time.monotonic() - hit[0] < 60:
                return hit[1]
        data = collect_agenda_safe(days)
        with self._lock:
            self._data[days] = (time.monotonic(), data)
        return data


def collect_mail_safe(force=False):
    try:
        return mail_mod.collect_mail(load_config(), force=force)
    except Exception as e:  # noqa: BLE001
        log(f"경고: 메일 수집 실패({type(e).__name__}: {e})")
        return {"configured": False, "status": "fail", "error": f"{type(e).__name__}: {e}", "counts": {"reply": 0, "waiting": 0, "info": 0}}


def cmd_mail(args):
    cfg = load_config()
    if args.action == "add":
        if not args.user:
            raise BrainError("사용법: mail add <메일주소> --password-file <앱 비밀번호 파일> [--host imap.gmail.com] [--sent-folder ...]")
        pf = Path(os.path.expanduser(args.password_file or "~/.config/second-brain/mail.pass"))
        if not pf.is_file():
            raise BrainError(f"비밀번호 파일이 없어요: {pf} (앱 비밀번호 한 줄, chmod 600)")
        try:
            os.chmod(pf, 0o600)
        except OSError:
            pass
        host = args.host or ("imap.gmail.com" if args.user.lower().endswith("@gmail.com") else "imap.naver.com" if args.user.lower().endswith("@naver.com") else "")
        if not host:
            raise BrainError("--host 를 적어 주세요(예: imap.gmail.com)")
        mcfg = {"host": host, "port": 993, "user": args.user, "password_file": str(pf), "folder": "INBOX",
                "sent_folder": args.sent_folder or ("[Gmail]/Sent Mail" if "gmail" in host else "Sent"), "days": 7, "max": 120}
        cfg["mail"] = mcfg
        save_config(cfg)
        emit(args, mcfg, f"메일 소스 저장: {args.user} ({host}). `brain.py mail test`로 확인해 보세요. 읽기 전용, 헤더만 읽어요.")
        return EXIT_OK
    if args.action == "remove":
        cfg.pop("mail", None)
        save_config(cfg)
        emit(args, {"removed": True}, "메일 소스 제거")
        return EXIT_OK
    m = collect_mail_safe(force=(args.action == "test"))
    emit(args, m, mail_mod.mail_human(m))
    return EXIT_OK if m.get("status") in ("ok", "stale", "unconfigured") else EXIT_INPUT


def collect_agenda_safe(days=7, now=None):
    """설정을 읽어 일정 수집. 모듈 오류가 나도 서버·브리핑은 살아야 하므로 실패는 sources에 담는다."""
    try:
        return agenda_mod.collect_agenda(load_config(), now=now, days=days)
    except Exception as e:  # noqa: BLE001
        log(f"경고: 일정 수집 실패({type(e).__name__}: {e})")
        return {"today": [], "upcoming": [], "next": None, "current": [], "conflicts": [], "total": 0, "days": days,
                "sources": [{"name": "agenda", "kind": "-", "status": "fail", "count": 0, "error": f"{type(e).__name__}: {e}"}]}


def dash_today(vault, today=None, now=None, widgets=None, agenda=None):
    """/api/today. vault=None이면 볼트 항목은 비움(위젯만). agenda=None이면 설정대로 수집."""
    now = now or datetime.now().astimezone()
    today = today or now.date()
    notes = load_notes(vault) if vault else []
    week_ago = (today - timedelta(days=7)).isoformat()
    week = [n for n in notes if n.created >= week_ago]
    widgets = collect_widgets() if widgets is None else widgets
    counts = {s: 0 for s in SUMMARY_STATUSES}
    for w in widgets:
        if w["status"] in counts:
            counts[w["status"]] += 1
    active = [w for w in widgets if w.get("state") != "paused"]  # 멈춘 자동화는 경고 후보에서 제외
    top = sorted(active, key=lambda w: STATUS_PRIORITY.get(w["status"], 9))[:5]
    t = {
        "date": today.isoformat(),
        "greeting": greeting_for(now.hour),
        "weekday": WEEKDAYS_KO[today.weekday()],
        "revisit": _revisit_soon(notes, today),
        "retro_questions": retro_questions(notes, today) if vault else [],
        "week_plan": week_plan(notes, today) if vault else [],
        "inbox": parse_inbox(vault),
        "this_week": {"new_notes": sum(1 for n in week if n.type != "decision"),
                      "new_decisions": sum(1 for n in week if n.type == "decision")},
        "widgets_summary": counts,
        "widgets_total": len(widgets),
        "top_widgets": top,
    }
    ag = dict(agenda if agenda is not None else collect_agenda_safe(7, now=now))
    if vault:
        attach_event_notes(vault, ag)
    t["agenda"] = {k: ag.get(k) for k in ("today", "next", "current", "conflicts", "sources", "total", "steps_today", "steps_upcoming")}
    t["agenda"]["upcoming"] = (ag.get("upcoming") or [])[:6]
    t["agenda"]["upcoming_count"] = len(ag.get("upcoming") or [])
    t["agenda"]["sentence"] = agenda_mod.agenda_sentence(ag)
    tb = dash_tasks(vault, today, widgets, ag)
    t["tasks"] = {"counts": tb["counts"], "today": tb["today"][:8], "waiting": tb["waiting"][:5]}
    m = collect_mail_safe()
    t["mail"] = {k: m.get(k) for k in ("configured", "status", "counts", "user", "web", "error")}
    t["mail"]["reply"] = (m.get("reply") or [])[:3]
    t["mail"]["waiting"] = (m.get("waiting") or [])[:3]
    t["mail"]["sentence"] = mail_mod.mail_sentence(m)
    # 표시용 inbox: 오늘 묶음(직접 적은 것 우선). 예전 계약(문자열 목록) 유지
    own = lambda b: [x["text"] for x in tb[b] if x.get("kind") == "task"]
    t["inbox"] = own("today") or (own("week") + own("someday"))[:6] or t["inbox"]
    pend = pending_suggestions(today=today)
    t["suggestions"] = {"count": len(pend), "by_key": {k: {"items": s["items"], "created": s.get("created")} for k, s in pend.items()}}
    t["kakao"] = kakao_brief(t)
    return t


def today_human(t):
    """사람용 브리핑(8줄 이내) + 카톡용 블록."""
    out = [f"{t['greeting']}! {t['date']} {t['weekday']}"]
    ag = t.get("agenda") or {}
    if ag.get("sentence"):
        out.append(ag["sentence"])
    weather_lines = [f"{e['weather']['place']} {e['weather']['summary']}"
                      for e in ((ag.get("today") or []) + [x for x in (ag.get("upcoming") or []) if x.get("days_left") == 1])
                      if e.get("weather")][:2]
    if weather_lines:
        out.append("날씨: " + " / ".join(weather_lines))
    if (t.get("mail") or {}).get("sentence"):
        out.append(t["mail"]["sentence"])
    bad_src = [s_ for s_ in (ag.get("sources") or []) if s_.get("status") != "ok"]
    if bad_src and not ag.get("today"):
        out.append("일정 연결 안 됨: " + "; ".join(f"{s_['name']} {s_['status']}" for s_ in bad_src[:2]))
    if t["revisit"]:
        out.append(f"되돌아볼 결정 {len(t['revisit'])}: " +
                   ", ".join(f"{d['title']}({_dleft(d)})" for d in t["revisit"][:3]))
    sugg_count = (t.get("suggestions") or {}).get("count") or 0
    retro_q = t.get("retro_questions") or []
    if sugg_count or retro_q:
        out.append(f"준비 제안 {sugg_count}건 대기 · 회고 질문 {len(retro_q)}개")
    week_plan_items = t.get("week_plan") or []
    if week_plan_items:
        out.append("이번 주 계획: " + ", ".join(x["text"] for x in week_plan_items[:2]))
    s = t["widgets_summary"]
    if t["widgets_total"]:
        bad = [w["title"] for w in t["top_widgets"] if w["status"] in ("fail", "stale")]
        out.append(f"자동화: 정상 {s['ok']} · 실패 {s['fail']} · 지연 {s['stale']} · "
                   f"없음 {s['missing']} · 주의 {s['warn']}"
                   + (f" · 멈춤 {s['paused']}" if s.get("paused") else "")
                   + (f" — {', '.join(bad)}" if bad else ""))
    else:
        out.append("자동화: 위젯 없음 — `brain.py config init-widgets`로 예시를 만들어 보세요")
    if t["inbox"]:
        out.append(f"inbox {len(t['inbox'])}: " + ", ".join(t["inbox"][:3]) +
                   (" 외" if len(t["inbox"]) > 3 else ""))
    w = t["this_week"]
    out.append(f"이번 주 신규: 노트 {w['new_notes']} · 결정 {w['new_decisions']}")
    return "\n".join(out[:8]) + "\n\n카톡용(200자)\n" + t["kakao"]


# ---------------------------------------------------------------------------
# HTTP 서버
# ---------------------------------------------------------------------------

def _int_param(qs, name, default, lo=1, hi=10000):
    raw = (qs.get(name) or [None])[0]
    if raw in (None, ""):
        return default
    try:
        v = int(raw)
    except ValueError:
        raise BrainError(f"{name}는 정수여야 합니다: {raw}")
    return max(lo, min(hi, v))


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "second-brain/" + VERSION

    def log_message(self, fmt, *args):  # 요청 로그는 stderr 한 줄
        if getattr(self.server, "quiet", False):
            return
        sys.stderr.write(f"[{datetime.now().strftime('%H:%M:%S')}] {self.address_string()} "
                         f"{fmt % args}\n")

    def _send(self, status, body, ctype):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _write_allowed(self):
        """쓰기 요청 검사: 같은 서버가 낸 토큰 + Host가 루프백. 실패 시 (False, 응답) 반환."""
        tok = self.headers.get("X-Brain-Token", "")
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost", "[::1]"):
            return False, self._json(403, {"error": "로컬에서만 쓸 수 있어요"})
        if not tok or not secrets.compare_digest(tok, self.server.token):
            return False, self._json(403, {"error": "세션 토큰이 없거나 달라요. 페이지를 새로 고쳐 주세요"})
        return True, None

    def _read_json(self, limit=65536):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > limit:
            raise BrainError("요청 본문이 비었거나 너무 커요")
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except (UnicodeError, ValueError):
            raise BrainError("JSON 본문이 아니에요")

    def do_POST(self):
        try:
            route = urllib.parse.urlsplit(self.path).path.rstrip("/")
            ok, resp = self._write_allowed()
            if not ok:
                return resp
            vault = self.server.vault
            if not vault_exists(vault) and route != "/api/ask":
                return self._json(400, {"error": "볼트가 없어요. brain-setup으로 먼저 만들어 주세요"})
            body = self._read_json()
            if route == "/api/event-note":
                res = event_note_action(vault, body)
                self.server.agenda_cache.invalidate()
                return self._json(200, res)
            if route == "/api/note-append":
                return self._json(200, note_append_action(vault, body))
            if route == "/api/person":
                res = person_action(vault, body)
                self.server.agenda_cache.invalidate()
                return self._json(200, res)
            if route == "/api/remember":
                return self._json(200, remember_chat(vault, body))
            if route == "/api/capture":
                return self._json(200, capture_note(vault, body))
            if route == "/api/suggestion":
                res = suggestion_action(vault, body, self.server.today)
                self.server.agenda_cache.invalidate()
                return self._json(200, res)
            if route == "/api/widget":
                res = widget_action(body, collect_widgets(self.server.widget_cache))
                if self.server.widget_cache:
                    self.server.widget_cache.clear()
                return self._json(200, res)
            if route == "/api/task":
                res = task_action(vault, body, self.server.today or date.today())
                return self._json(200, res)
            if route == "/api/config":
                allowed = {"assistant_name": (str, 24)}
                cfg = load_config()
                changed = {}
                for k, v in body.items():
                    if k not in allowed:
                        raise BrainError(f"바꿀 수 없는 설정: {k}")
                    typ, mx = allowed[k]
                    v = typ(v).strip()
                    if not v or len(v) > mx:
                        raise BrainError(f"{k}는 1~{mx}자")
                    cfg[k] = v
                    changed[k] = v
                save_config(cfg)
                return self._json(200, {"ok": True, "changed": changed})
            if route == "/api/ask":
                widgets = collect_widgets(self.server.widget_cache)
                t = dash_today(vault, self.server.today, widgets=widgets, agenda=self.server.agenda_cache.get())
                return self._json(200, ask_assistant(str(body.get("text") or ""), t, vault))
            return self._json(404, {"error": f"없는 경로입니다: {route}"})
        except BrainError as e:
            return self._json(400, {"error": str(e)})
        except (BrokenPipeError, ConnectionResetError):
            return None
        except Exception as e:  # noqa: BLE001 - 서버는 죽지 않는다
            import traceback
            traceback.print_exc(file=sys.stderr)
            return self._json(500, {"error": f"{type(e).__name__}: {e}"})

    def _json(self, status, data):
        self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _static(self, rel):
        web = Path(os.path.realpath(str(self.server.web_dir)))
        target = Path(os.path.realpath(str(web / rel)))
        if (web not in target.parents) or ".." in Path(rel).parts or not target.is_file():
            return self._json(404, {"error": f"파일이 없습니다: {rel}"})
        ctype = CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream")
        self._send(200, target.read_bytes(), ctype)

    def do_GET(self):
        try:
            raw = self.path
            try:  # 인코딩 안 된 UTF-8 요청줄(latin-1로 디코딩됨) 복원
                raw = raw.encode("latin-1").decode("utf-8")
            except UnicodeError:
                pass
            u = urllib.parse.urlsplit(raw)
            route = u.path.rstrip("/") or "/"
            qs = urllib.parse.parse_qs(u.query)
            vault, today = self.server.vault, self.server.today or date.today()
            if route in ("/", "/index.html"):
                return self._static("index.html")
            if route in ("/core", "/core.html"):
                return self._static("core.html")
            if route in ("/office", "/office.html"):
                return self._static("office.html")
            if route in ("/report", "/report.html"):
                return self._static("report.html")
            if route == "/api/office":
                d = dash_office(collect_widgets(self.server.widget_cache))
                wcfg = load_widgets_config()
                allow = bool(wcfg.get("allow_run"))
                cmds = widget_commands(d["widgets"])
                d["runnable"] = {k: v["kind"] for k, v in cmds.items()} if allow else {}
                d["hireable"] = bool(wcfg.get("allow_hire"))
                return self._json(200, d)
            if route == "/api/mail":
                return self._json(200, collect_mail_safe(force=bool(qs.get("force"))))
            if route == "/api/widget-history":
                wid = (qs.get("id") or [""])[0]
                w = next((x for x in collect_widgets(self.server.widget_cache) if x["id"] == wid), None)
                if not w:
                    return self._json(404, {"error": f"위젯이 없어요: {wid}"})
                return self._json(200, widget_history(w, _int_param(qs, "days", 14, hi=90), today))
            if route == "/api/tasks":
                widgets = collect_widgets(self.server.widget_cache)
                ag = dict(self.server.agenda_cache.get())
                attach_event_notes(vault, ag)
                return self._json(200, dash_tasks(vault, today, widgets, ag))
            if route.startswith("/web/"):
                return self._static(urllib.parse.unquote(route[len("/web/"):]))
            if route == "/api/summary":
                return self._json(200, dash_summary(vault, today))
            if route == "/api/graph":
                return self._json(200, dash_graph(vault))
            if route == "/api/search":
                q = (qs.get("q") or [""])[0]
                hits = dash_search(vault, q, _int_param(qs, "limit", 20, hi=200), today)
                return self._json(200, {"hits": list(hits), "facets": hits.facets, "applied": hits.applied})
            if route == "/api/note":
                return self._json(200, dash_note(vault, (qs.get("path") or [""])[0]))
            if route == "/api/person":
                return self._json(200, person_note_payload(vault, (qs.get("path") or [""])[0], self.server.agenda_cache.get()))
            if route == "/api/timeline":
                return self._json(200, dash_timeline(vault, _int_param(qs, "days", 30, hi=3650), today))
            if route == "/api/journals":
                return self._json(200, dash_journals(vault, _int_param(qs, "limit", 14, hi=60)))
            if route == "/api/report":
                widgets = collect_widgets(self.server.widget_cache)
                return self._json(200, dash_report(vault, _int_param(qs, "days", 7, hi=90), today, widgets))
            if route == "/api/decisions":
                return self._json(200, dash_decisions(vault, today))
            if route == "/api/projects":
                return self._json(200, dash_projects(vault))
            if route == "/api/people":
                ag = dict(self.server.agenda_cache.get(14))
                attach_event_notes(vault, ag)
                return self._json(200, dash_people(vault, ag, self.server.today))
            if route == "/api/widgets":
                ws = collect_widgets(self.server.widget_cache)
                cmds = widget_commands(ws)
                allow = bool(load_widgets_config().get("allow_run"))
                for w in ws:
                    w["runnable"] = allow and w["id"] in cmds
                return self._json(200, ws)
            if route == "/api/today":
                widgets = collect_widgets(self.server.widget_cache)
                return self._json(200, dash_today(vault, self.server.today, widgets=widgets,
                                                  agenda=self.server.agenda_cache.get()))
            if route == "/api/suggestions":
                return self._json(200, {"pending": list(pending_suggestions(today=self.server.today).values())})
            if route == "/api/agenda":
                ag = dict(self.server.agenda_cache.get(_int_param(qs, "days", 7, hi=60)))
                attach_event_notes(vault, ag)
                return self._json(200, ag)
            if route == "/api/session":
                cfg = load_config()
                return self._json(200, {"token": self.server.token, "writable": vault_exists(vault),
                                        "assistant_name": str(cfg.get("assistant_name") or "브레인")})
            return self._json(404, {"error": f"없는 경로입니다: {u.path}"})
        except BrainError as e:
            return self._json(400, {"error": str(e)})
        except FileNotFoundError as e:
            return self._json(404, {"error": f"노트가 없습니다: {e}"})
        except (BrokenPipeError, ConnectionResetError):
            return None
        except Exception as e:  # 서버는 죽지 않는다
            import traceback
            traceback.print_exc(file=sys.stderr)
            try:
                return self._json(500, {"error": f"{type(e).__name__}: {e}"})
            except OSError as e2:
                log(f"응답 전송 실패: {e2}")
                return None


def make_server(vault, port=7777, web_dir=None, today=None, quiet=False):
    """127.0.0.1 전용 서버 생성(port=0이면 임의 포트). 호출자가 serve_forever 실행."""
    srv = ThreadingHTTPServer(("127.0.0.1", port), DashboardHandler)
    srv.daemon_threads = True
    srv.vault = Path(vault)
    srv.agenda_cache = AgendaCache()
    srv.token = secrets.token_hex(16)  # 쓰기 요청용 세션 토큰(페이지가 /api/session으로 받아 헤더로 보냄)
    srv.web_dir = Path(web_dir) if web_dir else WEB_DIR
    srv.today = today
    srv.quiet = quiet
    srv.widget_cache = WidgetCache()
    return srv


def _open_browser(url):
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    try:
        subprocess.Popen([opener, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        log(f"브라우저를 열지 못했습니다({e}). 직접 여세요: {url}")


def setup_demo_isolated():
    """데모 서버가 실제 캐시(~/.cache/second-brain의 suggestions.json·core_log.jsonl·staff_briefs.json·
    weather/·agents/·backups/ 등)를 절대 건드리지 않도록, agenda.cache_dir()가 읽는 XDG_CACHE_HOME을
    데모 전용 경로(데모 볼트 안의 _demo/cache_home)로 바꾼 뒤 데모 볼트를 만들어 반환한다.
    cache_dir()는 매 호출마다 환경변수를 다시 읽으므로(임포트 시 캐싱 없음), 볼트 생성
    (build_demo_vault → build_demo_assistant가 캐시에 샘플을 씀) 전에 먼저 지정해야 한다."""
    demo_v = demo_vault_path()
    os.environ["XDG_CACHE_HOME"] = str(demo_v / "_demo" / "cache_home")
    os.environ["SECOND_BRAIN_OFFLINE"] = "1"  # 데모는 네트워크(날씨 등)를 절대 타지 않는다
    v = build_demo_vault()
    CONFIG_OVERRIDES.update(demo_overrides(v))
    return v


def cmd_serve(args):
    if args.demo:
        v = setup_demo_isolated()
        log(f"데모 볼트(가공 샘플 데이터): {v} — 데모 캘린더·위젯·할 일 포함, 캐시 격리: {agenda_mod.cache_dir()}")
    else:
        v = require_vault()
    if not 0 <= args.port <= 65535:
        raise BrainError(f"포트 범위 오류: {args.port}")
    try:
        srv = make_server(v, args.port)
    except OSError as e:
        raise BrainError(f"포트 {args.port}에 바인딩하지 못했습니다({e}). --port로 다른 포트를 지정하세요.")
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    print(url, flush=True)
    if args.open:
        _open_browser(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log("서버 종료")
    finally:
        srv.server_close()
    return EXIT_OK


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def emit(args, data, human):
    if getattr(args, "json", False):
        print(json.dumps(data, ensure_ascii=False, indent=2))
    elif human:
        print(human)


def split_csv(s):
    return [x.strip() for x in s.split(",") if x.strip()] if s else []


def cmd_init(args):
    v = vault_path(args.vault)
    v.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    if args.vault or not config_path().exists():
        cfg["vault"] = str(v)
        save_config(cfg)
    created = init_vault(v, git=args.git)
    hint = "다음: `brain.py doctor`로 점검 → 캘린더 연결(`calendar add ics …`) → 대시보드(`serve`)"
    emit(args, {"vault": str(v), "created": created},
         f"볼트 준비 완료: {v}\n생성/갱신: " + ", ".join(created) + "\n\n" + hint)
    return EXIT_OK


def _read_body(args):
    if args.body_file and args.body is not None:
        raise BrainError("--body와 --body-file은 함께 쓸 수 없습니다.")
    if args.body_file == "-":
        return sys.stdin.read()
    if args.body_file:
        p = ensure_in_home(args.body_file, "본문 파일")
        if not p.is_file():
            raise BrainError(f"본문 파일이 없습니다: {p}")
        return p.read_text(encoding="utf-8")
    return args.body


def cmd_new(args):
    v = require_vault()
    body = _read_body(args)
    path = create_note(v, args.type, args.title, tags=split_csv(args.tags), project=args.project,
                       source=args.source, people=split_csv(args.people), revisit=args.revisit,
                       body=body, status=args.status, created=args.created)
    build_index(v)
    git_commit(v, f"brain: new {path.relative_to(v).as_posix()}")
    emit(args, {"path": str(path), "rel": path.relative_to(v).as_posix(), "stem": path.stem},
         str(path))
    return EXIT_OK


def _format_facets_line(facets):
    types_part = "타입: " + " · ".join(f"{t} {c}" for t, c in facets.get("types", {}).items())
    tags = facets.get("tags") or {}
    if not tags:
        return types_part
    return types_part + " | 태그: " + ", ".join(f"{t} {c}" for t, c in tags.items())


def cmd_capture(args):
    v = require_vault()
    res = capture_note(v, {"text": args.text, "type": args.type, "tags": split_csv(args.tags),
                           "project": args.project})
    emit(args, res, res["path"])
    return EXIT_OK


def cmd_search(args):
    """검색어(query) 안에 type:/tag:/since:7d/has:summary/-부정어/"정확한 문구" 등 연산자를
    그대로 써도 되고(dash_search가 파싱), --type/--tag/--project/--since 플래그는 같은 연산자로
    변환해 뒤에 덧붙인다(기존 CLI 인터페이스 호환)."""
    v = require_vault()
    q = args.query
    for op, val in (("type", args.type), ("project", args.project), ("tag", args.tag), ("since", args.since)):
        if val:
            q = f"{q} {op}:{val}"
    res = dash_search(v, q, limit=args.limit)
    if args.json:
        emit(args, {"query": args.query, "results": list(res), "facets": res.facets, "applied": res.applied}, None)
        return EXIT_OK
    if not res:
        print(f"'{args.query}' 검색 결과 없음 (기록 없음)")
        return EXIT_OK
    lines = []
    for i, r in enumerate(res, 1):
        lines.append(f"{i}. [{r['type']}] {r['title']}  ({r['score']})  {r['path']}")
        lines += [f"     {s}" for s in r["snippet"]]
    lines.append(_format_facets_line(res.facets))
    print("\n".join(lines))
    return EXIT_OK


def cmd_show(args):
    v = require_vault()
    p = resolve_note(v, args.path)
    text = p.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(text)
    emit(args, {"path": p.relative_to(v).as_posix(), "meta": meta, "body": body}, text.rstrip("\n"))
    return EXIT_OK


def cmd_index(args):
    # 훅 주입용: 볼트가 없거나 경로가 잘못돼도 stdout 없이 exit 0
    try:
        v = vault_path()
    except BrainError as e:
        log(f"오류: {e}")
        return EXIT_OK
    if not vault_exists(v):
        return EXIT_OK
    content, stats = build_index(v)
    if args.head is not None:
        lines = content.split("\n")[:max(0, args.head)]
        if args.json:
            emit(args, {"head": "\n".join(lines), **stats}, None)
        else:
            print("\n".join(lines))
        return EXIT_OK
    emit(args, {"path": str(v / "BRAIN.md"), **stats},
         f"BRAIN.md 갱신: 노트 {stats['notes']}개 · 프로젝트 {stats['projects']}개 · "
         f"미해결 결정 {stats['open_decisions']}개 · 고아 {stats['orphans']}개")
    return EXIT_OK


def cmd_review(args):
    v = require_vault()
    if args.days < 1:
        raise BrainError("--days는 1 이상이어야 합니다.")
    r = review(v, args.days)
    if getattr(args, "semantic", False):
        try:
            sem = semantic_link_suggestions(v, r, limit=getattr(args, "limit", 25))
            r["link_suggestions"] = sem + [s for s in r["link_suggestions"] if not any({s["a"], s["b"]} == {x["a"], x["b"]} for x in sem)]
            r["semantic"] = len(sem)
        except BrainError as e:
            log(f"의미 기반 제안 건너뜀: {e}")
    if args.json:
        emit(args, r, None)
        return EXIT_OK
    out = [f"## 최근 {r['days']}일 리뷰 ({r['since']} 이후)", "",
           f"신규 노트 {len(r['new_notes'])}개"]
    out += [f"- {n['created']} [{n['type']}] {n['title']} ({n['path']})" for n in r["new_notes"]]
    out += ["", f"되돌아볼 결정 {len(r['due_decisions'])}개"]
    out += [f"- {d['revisit']} {d['title']} ({d['path']})" for d in r["due_decisions"]]
    out += ["", f"고아 노트 {len(r['orphans'])}개"]
    out += [f"- {o['title']} ({o['path']})" for o in r["orphans"]]
    out += ["", f"링크 제안 {len(r['link_suggestions'])}개"]
    out += [f"- {s['a']} ↔ {s['b']} ({s['reason']})" for s in r["link_suggestions"]]
    print("\n".join(out))
    return EXIT_OK


def cmd_decide(args):
    v = require_vault()
    po, pn = supersede(v, args.supersede, args.new_path)
    build_index(v)
    git_commit(v, f"brain: {pn.stem} supersedes {po.stem}")
    emit(args, {"old": po.relative_to(v).as_posix(), "new": pn.relative_to(v).as_posix(),
                "old_status": "superseded"},
         f"대체 처리: {po.relative_to(v).as_posix()} → superseded, "
         f"{pn.relative_to(v).as_posix()} supersedes [[{po.stem}]]")
    return EXIT_OK


def cmd_link(args):
    v = require_vault()
    pa, pb, changed = add_link(v, args.a, args.b)
    if changed:
        build_index(v)
        git_commit(v, f"brain: link {pa.stem} <-> {pb.stem}")
    emit(args, {"a": pa.relative_to(v).as_posix(), "b": pb.relative_to(v).as_posix(),
                "changed": changed},
         f"연결: [[{pa.stem}]] ↔ [[{pb.stem}]]" + ("" if changed else " (이미 연결됨)"))
    return EXIT_OK


def cmd_import(args):
    v = require_vault()
    if getattr(args, "apple_notes", False):
        return _cmd_import_apple_notes(v, args)
    if not args.path:
        raise BrainError("가져올 경로(path)가 필요합니다(--apple-notes를 쓰지 않는 경우).")
    r = import_path(v, args.path, dry_run=args.dry_run)
    if not args.dry_run and r["imported"]:
        git_commit(v, f"brain: import {len(r['imported'])} notes")
    if args.json:
        emit(args, r, None)
        return EXIT_OK
    mode = " (미리보기, 아무것도 쓰지 않음)" if args.dry_run else ""
    by_type = Counter(x["type"] for x in r["imported"])
    out = [f"가져오기{mode}: 새로 {len(r['imported'])}개 · 건너뜀 {len(r['skipped'])}개 · "
           f"오류 {len(r['errors'])}개 · 링크 재작성 {r.get('relinked', 0)}개 파일"]
    if by_type:
        out.append("타입별: " + ", ".join(f"{k} {c}" for k, c in sorted(by_type.items())))
    out += [f"- {x['dest']} ← {Path(x['file']).name}" for x in r["imported"][:30]]
    if len(r["imported"]) > 30:
        out.append(f"- ... 외 {len(r['imported']) - 30}개")
    out += [f"! {Path(e['file']).name}: {e['error']}" for e in r["errors"]]
    print("\n".join(out))
    return EXIT_OK


def _cmd_import_apple_notes(vault, args):
    r = import_apple_notes(vault, folders=args.folder, since=args.since, dry_run=args.dry_run)
    if r.get("error"):
        if args.json:
            emit(args, r, None)
        else:
            log(f"오류: {r['error']}")
        return EXIT_INPUT
    if not args.dry_run and (r["created"] or r["updated"]):
        git_commit(vault, f"brain: apple notes 가져오기 생성 {len(r['created'])}·갱신 {len(r['updated'])}")
    if args.json:
        emit(args, r, None)
        return EXIT_OK
    mode = " (미리보기, 아무것도 쓰지 않음)" if args.dry_run else ""
    out = [f"Apple Notes 가져오기{mode}: 생성 {len(r['created'])} · 갱신 {len(r['updated'])} · "
           f"건너뜀 {len(r['skipped'])}"]
    out += [f"+ {x['path']}" for x in r["created"][:30]]
    if len(r["created"]) > 30:
        out.append(f"+ ... 외 {len(r['created']) - 30}개")
    out += [f"~ {x['path']}" for x in r["updated"][:30]]
    if r["created"] or r["updated"]:
        out.append("힌트: `brain.py enrich`로 가져온 노트를 Claude가 요약·태그 정제하게 할 수 있어요.")
    print("\n".join(out))
    return EXIT_OK


def cmd_relink(args):
    v = require_vault()
    r = relink(v, dry_run=args.dry_run)
    if not args.dry_run and r["changed"]:
        git_commit(v, f"brain: relink {len(r['changed'])} notes")
    mode = " (미리보기, 아무것도 쓰지 않음)" if args.dry_run else ""
    out = [f"링크 복구{mode}: 변경 파일 {len(r['changed'])}개 · 링크 {r['links']}개"]
    for c in r["changed"][:30]:
        out.append(f"- {c['path']}: " + ", ".join(f"{a}→{b}" for a, b in sorted(c["map"].items())))
    if len(r["changed"]) > 30:
        out.append(f"- ... 외 {len(r['changed']) - 30}개")
    emit(args, r, "\n".join(out))
    return EXIT_OK


def cmd_lint(args):
    v = require_vault()
    report = lint_vault(v)
    fixed_lines = []
    if args.fix:
        fixed = fix_lint_issues(v, report["issues"])
        for f in fixed:
            fixed_lines.append(f"- 고침 {f['path']}: " + ", ".join(f["codes"]))
        if fixed:
            build_index(v)
            git_commit(v, f"brain: lint fix {len(fixed)}")
        report = lint_vault(v)  # 고친 뒤 재점검(남은 이슈·종료 코드 기준)

    lines = []
    if fixed_lines:
        lines.append(f"고침 {len(fixed_lines)}개:")
        lines += fixed_lines
        lines.append("")
    if report["issues"]:
        lines.append(f"검사한 노트 {report['notes']}개 · 남은 이슈 {len(report['issues'])}개")
        for code, n in sorted(report["counts"].items(), key=lambda x: (-x[1], x[0])):
            lines.append(f"- {code}: {n}개")
            for e in [i for i in report["issues"] if i["code"] == code][:5]:
                lines.append(f"    {e['path']}: {e['message']}")
        if any(i["fixable"] for i in report["issues"]):
            lines.append("")
            lines.append("고칠 수 있는 항목은 `brain.py lint --fix`로 적용하세요.")
    else:
        lines.append(f"검사한 노트 {report['notes']}개 · 이슈 없음")
    emit(args, report, "\n".join(lines))
    return EXIT_OK if not report["issues"] else EXIT_INPUT


def _coerce(value):
    low = value.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    return value


def cmd_widgets(args):
    ws = collect_widgets()
    if args.json:
        emit(args, ws, None)
        return EXIT_OK
    if not ws:
        print(f"위젯 없음 — `brain.py config init-widgets`로 예시 {widgets_config_path()}를 만드세요.")
        return EXIT_OK
    lines = []
    for w in ws:
        age = "" if w["age_minutes"] is None else f" ({w['age_minutes']}분 전)"
        lines.append(f"[{w['status'].upper()}] {w['title']} — {w['summary']}{age}")
    print("\n".join(lines))
    return EXIT_OK


def cmd_widget(args):
    """CLI로 위젯(자동화 직원) 채용·이동·이름변경·퇴사·정지·재개·실행·요약·조회·목록. 사용자 자신의 터미널이라 allow_run/allow_hire는 건너뛴다(widget_action의 cli=True)."""
    action = args.action
    if action == "list":
        ws = collect_widgets()
        if args.team:
            ws = [w for w in ws if (w.get("team") or "") == args.team]
        if args.json:
            emit(args, ws, None)
            return EXIT_OK
        if not ws:
            print("위젯 없음")
            return EXIT_OK
        print("\n".join(f"{w['id']} · {w['title']} · {w.get('team') or '-'} · {w['status']}" for w in ws))
        return EXIT_OK
    if action == "show":
        wid = args.arg1 or ""
        if not wid:
            raise BrainError("widget show <id>가 필요해요")
        ws = collect_widgets()
        w = next((x for x in ws if x["id"] == wid), None)
        if not w:
            raise BrainError(f"위젯이 없어요: {wid}")
        cfg = load_widgets_config()
        raw = next((x for x in cfg["widgets"] if str(x.get("id")) == wid), {})
        out = dict(w, config=raw)
        emit(args, out, f"[{w['status'].upper()}] {w['title']} ({w['id']}) · 팀 {w.get('team') or '-'} · {w['summary']}")
        return EXIT_OK
    if action == "add":
        if not args.arg1 or not args.arg2:
            raise BrainError("widget add <제목> <source>가 필요해요")
        body = {"action": "add", "title": args.arg1, "source": args.arg2, "kind": args.kind, "team": args.team,
                "ok_pattern": args.ok_pattern, "fail_pattern": args.fail_pattern, "stale_minutes": args.stale_minutes,
                "lines": args.lines}
        res = widget_action(body, [], cli=True)
        emit(args, res, f"채용 완료: {res['widget']['title']} ({res['id']})")
        return EXIT_OK
    if action == "move":
        if not args.arg1 or not args.arg2:
            raise BrainError("widget move <id> <팀>이 필요해요")
        res = widget_action({"action": "move", "id": args.arg1, "team": args.arg2}, [], cli=True)
        emit(args, res, f"부서 이동: {res['id']} → {res['team']}")
        return EXIT_OK
    if action == "rename":
        if not args.arg1 or not args.arg2:
            raise BrainError("widget rename <id> <제목>이 필요해요")
        res = widget_action({"action": "rename", "id": args.arg1, "title": args.arg2}, [], cli=True)
        emit(args, res, f"이름 변경: {res['id']} → {res['title']}")
        return EXIT_OK
    if action == "remove":
        if not args.arg1:
            raise BrainError("widget remove <id>가 필요해요")
        res = widget_action({"action": "remove", "id": args.arg1}, [], cli=True)
        emit(args, res, f"퇴사 처리: {res['id']}")
        return EXIT_OK
    if action == "pause":
        if not args.arg1:
            raise BrainError("widget pause <id>가 필요해요")
        res = widget_action({"action": "pause", "id": args.arg1}, [], cli=True)
        emit(args, res, f"일시 정지: {res['id']}")
        return EXIT_OK
    if action == "resume":
        if not args.arg1:
            raise BrainError("widget resume <id>가 필요해요")
        res = widget_action({"action": "resume", "id": args.arg1}, [], cli=True)
        emit(args, res, f"다시 켜기: {res['id']}")
        return EXIT_OK
    if action == "run":
        if not args.arg1:
            raise BrainError("widget run <id>가 필요해요")
        ws = collect_widgets()
        res = widget_action({"action": "run", "id": args.arg1, "dry": args.dry}, ws, cli=True)
        note = " (미리보기, 실행 안 함)" if res.get("dry") else ""
        emit(args, res, f"실행{note}: {args.arg1}")
        return EXIT_OK
    if action == "brief":
        if not args.arg1:
            raise BrainError("widget brief <id>가 필요해요")
        ws = collect_widgets()
        res = widget_action({"action": "brief", "id": args.arg1, "force": args.force}, ws, cli=True)
        emit(args, res, f"{res.get('did', '')} · {res.get('issue', '')} · {res.get('mood', '')}")
        return EXIT_OK
    raise BrainError("action은 add · move · rename · remove · pause · resume · run · brief · show · list 중 하나")


def cmd_today(args):
    try:
        v = vault_path()
    except BrainError as e:
        log(f"경고: {e}")
        v = None
    if v is not None and not vault_exists(v):
        log(f"볼트가 없어 위젯만 보여줍니다: {v}")
        v = None
    t = dash_today(v)
    emit(args, t, today_human(t))
    return EXIT_OK


CHECKBOX_RE = re.compile(r"^(\s*[-*]\s+\[)( |x|X)(\]\s+)(.*)$")


def _checklist(body):
    items = []
    for i, line in enumerate(body.split("\n")):
        m = CHECKBOX_RE.match(line)
        if m:
            items.append({"line": i, "done": m.group(2).lower() == "x", "text": m.group(4).strip()})
    return items


STEP_RE = re.compile(r"^\s*[-*]\s+(\d{1,2}):(\d{2})\s+(.*?)\s*$")
DAY_HEAD_RE = re.compile(r"^#{2,4}\s*(?:(\d{4})-)?(\d{1,2})[-/.](\d{1,2})\b")
DUR_RE = re.compile(r"\((?:[^()]*?,\s*)?(\d{1,3})\s*분\)")


def parse_steps(body, default_day, year_hint=None):
    """`## 동선` 절의 `- HH:MM 내용 (…NN분)` 줄. `### MM-DD` 소제목이 날짜를 바꾼다. 반환 [{day, time, text, minutes, line}]."""
    steps, in_route, day = [], False, default_day
    yh = int(str(year_hint or default_day or date.today().isoformat())[:4])
    for i, ln in enumerate(body.split("\n")):
        if ln.startswith("## "):
            in_route = ln.strip() in ("## 동선", "## 일정", "## 계획")
            if not in_route:
                continue
            day = default_day
            continue
        if not in_route:
            continue
        dh = DAY_HEAD_RE.match(ln)
        if dh:
            y = int(dh.group(1) or yh)
            try:
                day = date(y, int(dh.group(2)), int(dh.group(3))).isoformat()
            except ValueError:
                pass
            continue
        m = STEP_RE.match(ln)
        if not m:
            continue
        hh, mm = int(m.group(1)), int(m.group(2))
        if not (0 <= hh < 24 and 0 <= mm < 60):
            continue
        text = m.group(3)
        dm = DUR_RE.search(text)
        steps.append({"day": day, "time": f"{hh:02d}:{mm:02d}", "text": text, "minutes": int(dm.group(1)) if dm else 30, "line": i})
    return steps


def note_sections(body):
    """'## 제목' 단위로 본문을 나눈다 → {제목: 내용}. 첫 절(H1 등) 키는 ''."""
    out, cur, buf = {}, "", []
    for ln in body.split("\n"):
        if ln.startswith("## "):
            out[cur] = "\n".join(buf).strip()
            cur, buf = ln[3:].strip(), []
        else:
            buf.append(ln)
    out[cur] = "\n".join(buf).strip()
    return out


def _event_note_payload(n):
    items = _checklist(n.body)
    memo = note_sections(n.body).get("메모", "")
    day = str(n.meta.get("event_date") or n.created)[:10]
    steps = parse_steps(n.body, day)
    days_map = defaultdict(list)
    for st in steps:
        days_map[st["day"]].append(st)
    return {"path": n.rel, "title": n.title, "checklist": items,
            "done": sum(1 for x in items if x["done"]), "total": len(items),
            "memo": memo[:1200], "location": n.meta.get("location") or "",
            "steps": steps, "days": dict(days_map)}


def event_notes_index(vault):
    """event 타입 노트를 event_key → Note로. 기간 노트(event_end)는 (start, end, slug)도 함께."""
    exact, ranged = {}, []
    for n in load_notes(vault):
        if n.type != "event":
            continue
        k = n.meta.get("event_key")
        if k:
            exact[str(k)] = n
        s, e = str(n.meta.get("event_date") or "")[:10], str(n.meta.get("event_end") or "")[:10]
        if s and e and e > s:
            ranged.append((s, e, slugify(n.title), n))
    return exact, ranged


_PLACE_TITLE_RE = re.compile(r"^[가-힣]{2,10}(도|시|군|구|공항)$")


def _looks_like_place_title(title):
    """종일 일정 제목이 "제주도"처럼 장소 이름 그 자체로 보이는지(오탐 줄이려 보수적으로)."""
    return bool(_PLACE_TITLE_RE.match(str(title or "").strip()))


_TRIP_TITLE_RE = re.compile(r"여행|출장|휴가|trip|travel", re.I)


def is_trip(e):
    """일정을 "여행"으로 볼지: 이틀 이상 이어지는 종일 일정이거나,
    "제주도"처럼 지명 그 자체(place title)이거나 제목에 여행/출장/휴가 표현이 있는 종일 일정.
    (하루짜리 지명·여행 표현 일정도 여행 모드로 다룬다 — 짧은 당일 여행도 짐 목록이 필요하다.)"""
    if not e.get("all_day"):
        return False
    title = e.get("title") or ""
    try:
        start_d = date.fromisoformat(str(e.get("start") or "")[:10])
        end_d = date.fromisoformat(str(e.get("end") or "")[:10])
        span = (end_d - start_d).days
    except ValueError:
        span = 0
    if span >= 2:
        return True
    return bool(_TRIP_TITLE_RE.search(title) or _looks_like_place_title(title))


_HONORIFIC_RE = re.compile(r"(님|씨|선생님|팀장|부장|대표)$")


def normalize_person_name(name):
    """참석자 이름 매칭용 정규화: 공백 제거, 소문자, 끝의 존칭 제거."""
    s = re.sub(r"\s+", "", str(name or "")).lower()
    return _HONORIFIC_RE.sub("", s)


def people_index(notes):
    """person 타입 노트를 정규화 이름(우선) + 이메일 로컬파트로 찾을 수 있게 인덱싱.

    이메일 로컬파트를 먼저 채우고 이름 키를 나중에 덮어써서, 우연히 겹쳐도 이름 매칭이 이긴다.
    """
    idx = {}
    for n in notes:
        if n.type != "person":
            continue
        email = str(n.meta.get("email") or "").strip().lower()
        if "@" in email:
            idx.setdefault(email.split("@", 1)[0], n)
    for n in notes:
        if n.type != "person":
            continue
        key = normalize_person_name(n.title)
        if key:
            idx[key] = n
    return idx


def _is_self_attendee(name, ag, e=None):
    """캘린더 이름(구글 계정 주소)·소스 이름·config `me`와 같은 참석자는 '나'로 보고 사람 목록에서 뺀다."""
    key = " ".join(str(name).lower().split())
    if not key:
        return True
    mine = set()
    if e and e.get("calendar"):
        mine.add(str(e["calendar"]).lower().strip())
    for src in ag.get("sources") or []:
        nm = str(src.get("name") or "").lower().strip()
        if nm:
            mine.add(nm)
    try:
        for x in as_list(load_config().get("me")):
            mine.add(str(x).lower().strip())
    except BrainError:
        pass
    return any(k and (k == key or (("@" in k or "@" in key) and (k in key or key in k))) for k in mine)


def attach_event_notes(vault, ag):
    """agenda(dict)의 today/upcoming 각 일정에 note 필드 부착(없으면 None)."""
    if not vault or not vault_exists(Path(vault)):
        return ag
    exact, ranged = event_notes_index(Path(vault))
    all_notes = load_notes(Path(vault))
    pidx = people_index(all_notes)
    _, adj = link_graph(all_notes)
    for lst in ("today", "upcoming", "current"):
        for e in ag.get(lst) or []:
            n = exact.get(e.get("key"))
            if not n:
                day = e["start"][:10]
                for s, en, slug, note in ranged:
                    if s <= day <= en and (slug == slugify(e["title"]) or slug in slugify(e["title"])):
                        n = note
                        break
            e["note"] = _event_note_payload(n) if n else None
            attendees = [str(a or "").strip() for a in (e.get("attendees") or []) if str(a or "").strip()]
            attendees = [a for a in attendees if not _is_self_attendee(a, ag, e)]  # 내 캘린더 계정(주최자)은 참석자에서 제외
            if attendees and (e.get("days_left") or 0) <= 7:
                people = []
                for a in attendees:
                    person = pidx.get(normalize_person_name(a))
                    if not person and "@" in a:
                        person = pidx.get(a.split("@", 1)[0].strip().lower())
                    people.append({"name": a, "path": person.rel if person else None, "matched": bool(person),
                                  "mentions": len(adj.get(person.stem, ())) if person else 0})
                e["people"] = people
    if ag.get("next"):
        k = ag["next"].get("key")
        ag["next"]["note"] = _event_note_payload(exact[k]) if k in exact else None
    # 관련 기록(미팅 준비): 3일 안 일정마다 제목·장소·참석자로 볼트 검색 상위 3건
    for e in (ag.get("today") or []) + [x for x in (ag.get("upcoming") or []) if (x.get("days_left") or 0) <= 3]:
        q = " ".join([e.get("title") or ""] + (e.get("attendees") or [])[:3] + ([e["location"]] if e.get("location") else []))
        try:
            hits = dash_search(Path(vault), q, 3)
        except Exception:  # noqa: BLE001 - 검색 실패는 준비 카드만 비운다
            hits = []
        # 2-gram 검색은 느슨해서 우연한 겹침이 많다 → 질문 낱말(2자 이상)이 제목·발췌에 그대로 들어 있는 것만 관련으로 인정
        words = [w for w in re.split(r"[\s,·/()]+", q) if len(w) >= 2]
        def _relevant(h):
            hay = (h.get("title") or "") + " " + " ".join(h.get("snippets") or [])
            return any(w in hay for w in words)
        e["related"] = [{"path": h["path"], "title": h["title"], "type": h["type"], "snippet": (h.get("snippets") or [""])[0][:120]}
                        for h in hits if h.get("score", 0) > 0 and not h["path"].startswith("events/") and _relevant(h)][:3]
        e["prep"] = bool(e["related"]) or bool(e.get("note")) or bool(e.get("attendees")) or bool(re.search(r"회의|미팅|면접|상담|발표|인터뷰|meeting", e.get("title") or "", re.I))
    # 날씨: location이 있거나(없으면 "제주도"처럼 장소 이름인 종일 일정 제목) 7일 이내인 일정에 한 줄 부착
    weather_cache_dir = agenda_mod.cache_dir() / "weather"
    for e in (ag.get("today") or []) + [x for x in (ag.get("upcoming") or []) if (x.get("days_left") or 0) <= 7]:
        e["weather"] = None
        place = e.get("location") or (e["title"] if e.get("all_day") and _looks_like_place_title(e.get("title")) else None)
        if place:
            try:
                e["weather"] = weather_mod.weather_for(place, e["start"][:10], weather_cache_dir)
            except Exception:  # noqa: BLE001 - 날씨는 읽기 전용 보조 정보, 실패해도 일정 표시는 막지 않는다
                e["weather"] = None
    # 여행 모드: 이틀 이상 종일 일정(또는 지명/여행 표현 제목)을 "여행"으로 묶고, 그 기간 안에
    # 시작하는 다른 일정(항공편·숙소 등, 여행 자신은 제외)을 하위 일정으로 연결한다. ≤60개 일정 기준 O(n²).
    trip_candidates = (ag.get("today") or []) + (ag.get("upcoming") or [])
    for e in trip_candidates:
        if not is_trip(e):
            continue
        try:
            start_d = date.fromisoformat(str(e.get("start") or "")[:10])
            end_d = date.fromisoformat(str(e.get("end") or "")[:10])
        except ValueError:
            continue
        n_days = max(1, (end_d - start_d).days)
        dates = [(start_d + timedelta(days=i)).isoformat() for i in range(n_days)]
        children = []
        for o in trip_candidates:
            if o is e or is_trip(o):
                continue
            o_day = str(o.get("start") or "")[:10]
            if o_day and dates[0] <= o_day <= dates[-1]:
                children.append(o["key"])
                o["parent_trip"] = e["key"]
        weather_list = []
        place = e.get("location") or e["title"]
        for d in dates[:5]:
            try:
                w = weather_mod.weather_for(place, d, weather_cache_dir)
            except Exception:  # noqa: BLE001 - 날씨는 읽기 전용 보조 정보, 실패해도 여행 표시는 막지 않는다
                w = None
            if w:
                weather_list.append({"date": d, "summary": w.get("summary") or "", "umbrella": bool(w.get("umbrella"))})
        e["trip"] = {"days": n_days, "dates": dates, "children": children, "weather": weather_list}
    # 동선: 오늘과 다가오는 날의 단계들(같은 노트가 여러 일정에 붙어도 한 번만)
    today_iso = str(ag.get("now") or datetime.now().isoformat())[:10]
    seen, steps = set(), []
    for e in (ag.get("today") or []) + (ag.get("upcoming") or []):
        n = e.get("note")
        if not n or n["path"] in seen:
            continue
        seen.add(n["path"])
        for st in n.get("steps") or []:
            steps.append(dict(st, event_title=e["title"], event_key=e.get("key"), note_path=n["path"],
                              start=f"{st['day']}T{st['time']}", is_today=st["day"] == today_iso))
    steps.sort(key=lambda s: s["start"])
    ag["steps_today"] = [s for s in steps if s["is_today"]]
    ag["steps_upcoming"] = [s for s in steps if s["day"] > today_iso][:20]
    return ag


def find_or_create_event_note(vault, key, title=None, day=None, end=None, location=None):
    """event_key로 노트를 찾고 없으면 events/YYYY/에 만든다."""
    if "|" not in str(key):
        raise BrainError("일정 키는 'YYYY-MM-DD|제목' 형식이에요")
    day = day or key.split("|", 1)[0]
    title = title or key.split("|", 1)[1]
    parse_date(day, "일정 날짜")
    exact, _ = event_notes_index(vault)
    if key in exact:
        return exact[key], False
    extra = {"event_key": key, "event_date": day}
    if end:
        parse_date(end, "종료 날짜")
        extra["event_end"] = end
    if location:
        extra["location"] = location
    path = create_note(vault, "event", title, created=day, extra=extra)  # 경로(events/YYYY/날짜-slug)는 일정 날짜로
    n = Note(vault, path)
    n.meta["created"] = date.today().isoformat()  # 생성일은 오늘(타임라인·통계용), 일정 날짜는 event_date
    write_note(path, n.meta, n.body)
    return Note(vault, path), True


def _append_section(body, header, text):
    """'## header' 절 끝에 text 줄 추가(절이 없으면 끝에 절 생성)."""
    lines = body.rstrip("\n").split("\n")
    try:
        start = next(i for i, l in enumerate(lines) if l.strip() == header)
    except StopIteration:
        lines += ["", header]
        start = len(lines) - 1
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("## "):
            end = j
            break
    block = lines[start:end]
    while block and not block[-1].strip():
        block.pop()
    block.append(text)
    return "\n".join(lines[:start] + block + ([""] if end < len(lines) else []) + lines[end:]) + "\n"


def event_note_action(vault, body):
    """대시보드·CLI 공용. body: {action: memo|todo|check, key, title?, date?, end?, location?, text?, line?, done?}"""
    action = body.get("action")
    key = str(body.get("key") or "")
    if action in ("memo", "todo"):
        text = str(body.get("text") or "").strip()
        if not text:
            raise BrainError("내용이 비었어요")
        if len(text) > 4000:
            raise BrainError("한 번에 4000자까지만")
        n, created = find_or_create_event_note(vault, key, body.get("title"), body.get("date"), body.get("end"), body.get("location"))
        if action == "memo":
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            new_body = _append_section(n.body, "## 메모", f"- {stamp}  {text}")
        else:
            new_body = _append_section(n.body, "## 준비", f"- [ ] {text}")
        write_note(n.path, n.meta, new_body)
        git_commit(vault, f"brain: event {action} {n.rel}")
        n = Note(vault, n.path)
        return {"ok": True, "created": created, "note": _event_note_payload(n)}
    if action == "step":
        text = " ".join(str(body.get("text") or "").split())
        m = re.match(r"^(\d{1,2}):(\d{2})\s+(.+)$", text)
        if not m:
            raise BrainError("동선은 'HH:MM 내용' 형식이에요 (예: 14:00 집 출발 (자가용 50분))")
        n, created = find_or_create_event_note(vault, key, body.get("title"), body.get("date"), body.get("end"), body.get("location"))
        day = str(body.get("day") or "")[:10]
        header = "## 동선"
        new_body = n.body
        if day and day != str(n.meta.get("event_date") or "")[:10]:
            parse_date(day, "동선 날짜")
            sub = f"### {day[5:]}"
            if sub not in new_body:
                new_body = _append_section(new_body, header, sub)
            # 소제목 아래에 붙이기: 소제목부터 다음 소제목/절 전까지의 블록 끝에 삽입
            lines = new_body.rstrip("\n").split("\n")
            si = next(i for i, l in enumerate(lines) if l.strip() == sub)
            ei = len(lines)
            for j in range(si + 1, len(lines)):
                if lines[j].startswith("### ") or lines[j].startswith("## "):
                    ei = j
                    break
            while ei - 1 > si and not lines[ei - 1].strip():  # 소제목 블록 끝의 빈 줄 앞에 넣는다
                ei -= 1
            lines.insert(ei, f"- {text}")
            if ei + 1 < len(lines) and lines[ei + 1].startswith("## "):
                lines.insert(ei + 1, "")
            new_body = "\n".join(lines) + "\n"
        else:
            new_body = _append_section(new_body, header, f"- {text}")
        write_note(n.path, n.meta, new_body)
        git_commit(vault, f"brain: event step {n.rel}")
        return {"ok": True, "created": created, "note": _event_note_payload(Note(vault, n.path))}
    if action == "check":
        rel = str(body.get("path") or "")
        vault = Path(vault).resolve()  # 임시 폴더 심볼릭 링크(/var→/private/var)에서도 같은 기준으로 비교
        p = safe_vault_path(vault, rel)
        if not p.is_file():
            raise FileNotFoundError(rel)
        n = Note(vault, p)
        lines = n.body.split("\n")
        i = int(body.get("line", -1))
        if not (0 <= i < len(lines)) or not CHECKBOX_RE.match(lines[i]):
            raise BrainError("체크 항목 줄이 아니에요")
        m = CHECKBOX_RE.match(lines[i])
        mark = "x" if body.get("done", True) else " "
        lines[i] = f"{m.group(1)}{mark}{m.group(3)}{m.group(4)}"
        write_note(n.path, n.meta, "\n".join(lines))
        git_commit(vault, f"brain: event check {n.rel}")
        return {"ok": True, "note": _event_note_payload(Note(vault, n.path))}
    raise BrainError("action은 memo · todo · step · check 중 하나")


# ───────────────────────── 사무실(/office): 직원=자동화, 팀=방, Claude 배경 작업 ─────────────────────────
DEFAULT_TEAMS = (
    ("콘텐츠팀", ("gen-daily", "cafe-daily", "danggeun-bridge", "weekly-review", "cafe-growth", "topic-recommend", "cafe-")),
    ("생활팀", ("marketset", "flight", "dongtan", "market")),
    ("커리어팀", ("jobscout", "career", "job")),
)
OFFICE_EVENTS_MAX = 24


def team_for(widget):
    """widgets.json의 team이 있으면 그것, 없으면 id 접두어로 기본 팀. 어디에도 안 맞으면 운영팀."""
    if widget.get("team"):
        return widget["team"]
    wid = str(widget.get("id") or "")
    for name, keys in DEFAULT_TEAMS:
        if any(wid.startswith(k) or k in wid for k in keys):
            return name
    return "운영팀"


def _crontab_lines():
    try:
        r = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=5)
        return r.stdout.splitlines() if r.returncode == 0 else []
    except (OSError, subprocess.TimeoutExpired):
        return []


def _ps_lines():
    try:
        r = subprocess.run(["ps", "-axo", "pid=,etime=,command="], capture_output=True, text=True, timeout=5)
        return r.stdout.splitlines() if r.returncode == 0 else []
    except (OSError, subprocess.TimeoutExpired):
        return []


def launch_agents_dir():
    d = os.environ.get("SECOND_BRAIN_LAUNCH_AGENTS")
    return Path(os.path.expanduser(d)) if d else home_dir() / "Library" / "LaunchAgents"


def _cron_commands(cron_lines):
    """크론 줄 → {로그 절대경로: 전체 명령(스케줄 5칸 뒤)}. 로그 리다이렉트가 없으면 {스크립트 stem: 명령}."""
    out = {}
    for line in cron_lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue
        cmd = parts[5]
        m = re.search(r">>?\s*(\S+\.log)", cmd)
        run = re.split(r"\s*>>?\s*\S+\.log", cmd)[0].strip() if m else cmd.split("#")[0].strip()
        if m:
            out[os.path.expanduser(m.group(1))] = run
        else:
            script = next((c for c in cmd.split() if c.endswith((".py", ".sh"))), None)
            if script:
                out[Path(script).stem] = run
    return out


def _launchd_commands(plist_dir=None):
    """~/Library/LaunchAgents/*.plist → {StandardOutPath 절대경로: Label}."""
    import plistlib
    d = plist_dir or launch_agents_dir()
    out = {}
    if not d.is_dir():
        return out
    for pl in d.glob("*.plist"):
        try:
            data = plistlib.loads(pl.read_bytes())
        except Exception:  # noqa: BLE001 - 깨진 plist는 건너뜀
            continue
        label, log_path = data.get("Label"), data.get("StandardOutPath")
        if label and log_path:
            out[os.path.expanduser(str(log_path))] = str(label)
    return out


def widget_commands(widgets, cron_lines=None, plist_dir=None):
    """위젯별 실행 방법: {id: {kind: cron|launchd, cmd|label}}. 로그 경로가 크론 리다이렉트 또는 launchd StandardOutPath와 같을 때만."""
    cron = _cron_commands(_crontab_lines() if cron_lines is None else cron_lines)
    agents = _launchd_commands(plist_dir)
    out = {}
    for w in widgets:
        src = os.path.expanduser(str(w.get("source") or ""))
        if not src:
            continue
        if src in agents:
            out[w["id"]] = {"kind": "launchd", "label": agents[src]}
        elif src in cron:
            out[w["id"]] = {"kind": "cron", "cmd": cron[src]}
        elif Path(src).stem in cron:
            out[w["id"]] = {"kind": "cron", "cmd": cron[Path(src).stem]}
    return out


def run_widget(wid, widgets, dry=False, cron_lines=None, plist_dir=None):
    """위젯 자동화를 지금 한 번 실행. cron은 같은 명령을 백그라운드 셸로, launchd는 kickstart. 실행 전 allow_run 확인은 호출자가 한다."""
    cmds = widget_commands(widgets, cron_lines, plist_dir)
    if wid not in cmds:
        raise BrainError("이 자동화는 실행 방법을 몰라요(크론 로그 경로나 launchd StandardOutPath와 위젯 source가 같아야 해요)")
    how = cmds[wid]
    if dry or os.environ.get("SECOND_BRAIN_RUN_DRY"):
        return dict(how, started=False, dry=True)
    env = dict(os.environ)
    env["PATH"] = "/Users/" + os.environ.get("USER", "") + "/.local/bin:/opt/homebrew/bin:/opt/miniconda3/bin:/usr/local/bin:" + env.get("PATH", "/usr/bin:/bin")
    if how["kind"] == "launchd":
        uid = os.getuid()
        r = subprocess.run(["launchctl", "kickstart", "-k", f"gui/{uid}/{how['label']}"], capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            raise BrainError(f"launchctl 실패: {(r.stderr or r.stdout).strip()[:160]}")
        return dict(how, started=True)
    w = next(x for x in widgets if x["id"] == wid)
    log_path = os.path.expanduser(str(w.get("source") or ""))
    log_f = open(log_path, "ab") if log_path.endswith(".log") else subprocess.DEVNULL
    subprocess.Popen(["/bin/sh", "-c", how["cmd"]], stdout=log_f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                     start_new_session=True, env=env, cwd=str(home_dir()))
    return dict(how, started=True)


def set_widget_state(wid, state):
    """widgets.json의 해당 위젯 state를 active|paused로 바꿔 저장."""
    if state not in WIDGET_STATES:
        raise BrainError("state는 active 또는 paused")
    p = widgets_config_path()
    if not p.is_file():
        raise BrainError("widgets.json이 없어요")
    data = json.loads(p.read_text(encoding="utf-8"))
    hit = next((w for w in data.get("widgets", []) if str(w.get("id")) == wid), None)
    if not hit:
        raise BrainError(f"위젯이 없어요: {wid}")
    hit["state"] = state
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"ok": True, "id": wid, "state": state}


WIDGET_HIRE_KINDS = ("log", "json", "csv", "markdown")  # command는 채용 대상 아님(임의 명령 등록 금지)


def _load_widgets_raw():
    """widgets.json 원본(allow_* 포함 전체 dict)을 그대로 로드. add/move/remove/rename의 쓰기용.

    load_widgets_config()은 읽기 전용 정규화 뷰(누락 키 기본값)를 주기 때문에,
    다시 쓸 때는 원본 파일을 그대로 열어 다른 필드를 보존한다."""
    p = widgets_config_path()
    if not p.is_file():
        raise BrainError("widgets.json이 없어요")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise BrainError(f"widgets.json을 읽지 못했어요: {e}")
    if not isinstance(data, dict):
        raise BrainError("widgets.json 최상위는 객체여야 합니다.")
    if not isinstance(data.get("widgets"), list):
        data["widgets"] = []
    return p, data


def _save_widgets_raw(p, data):
    """set_widget_state와 같은 안전 쓰기 패턴(전체 dict → JSON, 개행 추가)."""
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _unique_widget_id(base, taken):
    if base not in taken:
        return base
    k = 2
    while f"{base}-{k}" in taken:
        k += 1
    return f"{base}-{k}"


def _require_hire(cfg):
    if not cfg.get("allow_hire"):
        raise BrainError("채용·퇴사 버튼은 widgets.json에 \"allow_hire\": true 를 적어야 켜져요")


def add_widget(body):
    """직원 채용: widgets.json에 새 위젯 추가. source는 홈 경로 규칙(ensure_in_home)을 그대로 적용,
    kind는 log·json·csv·markdown만(command는 임의 명령 등록이라 채용으로 못 만든다)."""
    title = str(body.get("title") or "").strip()
    if not title or len(title) > 40:
        raise BrainError("직원 이름(제목)은 1~40자여야 해요")
    kind = str(body.get("kind") or "log").strip().lower()
    if kind not in WIDGET_HIRE_KINDS:
        raise BrainError(f"kind는 {' · '.join(WIDGET_HIRE_KINDS)} 중 하나여야 해요(명령 실행 위젯은 채용으로 못 만들어요)")
    source = str(body.get("source") or "").strip()
    resolve_widget_source(source)  # 홈 밖·상대경로·`..`는 여기서 BrainError로 거부
    ok_pattern = str(body.get("ok_pattern") or "").strip() or None
    fail_pattern = str(body.get("fail_pattern") or "").strip() or None
    _regex(ok_pattern)
    _regex(fail_pattern)
    team = str(body.get("team") or "").strip()
    if team and len(team) > 20:
        raise BrainError("부서(팀) 이름은 1~20자여야 해요")
    stale = _num(body.get("stale_minutes")) if str(body.get("stale_minutes") or "").strip() else None
    lines = _pos_int(body.get("lines"), 5)
    p, data = _load_widgets_raw()
    taken = {str(w.get("id")) for w in data["widgets"] if isinstance(w, dict)}
    wid = _unique_widget_id(slugify(title), taken)
    status = {}
    if ok_pattern:
        status["ok_pattern"] = ok_pattern
    if fail_pattern:
        status["fail_pattern"] = fail_pattern
    if stale is not None and stale > 0:
        status["stale_minutes"] = stale
    entry = {"id": wid, "title": title, "kind": kind, "source": source, "team": team, "status": status, "lines": lines}
    data["widgets"].append(entry)
    _save_widgets_raw(p, data)
    return {"ok": True, "id": wid, "widget": entry}


def move_widget(wid, team):
    """부서 이동: 위젯의 team만 바꿔 저장."""
    team = str(team or "").strip()
    if not team or len(team) > 20:
        raise BrainError("부서(팀) 이름은 1~20자여야 해요")
    p, data = _load_widgets_raw()
    hit = next((w for w in data["widgets"] if isinstance(w, dict) and str(w.get("id")) == wid), None)
    if not hit:
        raise BrainError(f"위젯이 없어요: {wid}")
    hit["team"] = team
    _save_widgets_raw(p, data)
    return {"ok": True, "id": wid, "team": team}


def rename_widget(wid, title):
    """이름 변경: title만 바꾼다(id·슬러그는 유지 — 다른 곳에서 id로 참조하기 때문)."""
    title = str(title or "").strip()
    if not title or len(title) > 40:
        raise BrainError("직원 이름(제목)은 1~40자여야 해요")
    p, data = _load_widgets_raw()
    hit = next((w for w in data["widgets"] if isinstance(w, dict) and str(w.get("id")) == wid), None)
    if not hit:
        raise BrainError(f"위젯이 없어요: {wid}")
    hit["title"] = title
    _save_widgets_raw(p, data)
    return {"ok": True, "id": wid, "title": title}


def remove_widget(wid):
    """퇴사: widgets.json에서 항목 삭제. 로그 파일 자체는 지우지 않는다.
    비서 에이전트(id가 brain-로 시작)는 별도 관리 대상이라 여기서 막는다."""
    if wid.startswith("brain-"):
        raise BrainError("비서 에이전트 직원은 `agents remove`로")
    p, data = _load_widgets_raw()
    before = len(data["widgets"])
    data["widgets"] = [w for w in data["widgets"] if not (isinstance(w, dict) and str(w.get("id")) == wid)]
    if len(data["widgets"]) == before:
        raise BrainError(f"위젯이 없어요: {wid}")
    _save_widgets_raw(p, data)
    return {"ok": True, "id": wid}


def widget_action(body, widgets, cli=False):
    """cli=True면 사용자 자신의 터미널에서 부르는 것이라 allow_run/allow_hire 게이트만 건너뛴다.
    그 밖의 검증(홈 경로 규칙·kind 허용 목록·정규식 검사·brain- 접두 퇴사 거부)은 그대로 유지."""
    action, wid = body.get("action"), str(body.get("id") or "")
    cfg = load_widgets_config()
    if action == "run":
        if not cli and not cfg.get("allow_run"):
            raise BrainError("실행 버튼은 widgets.json에 \"allow_run\": true 를 적어야 켜져요")
        return dict(run_widget(wid, widgets, dry=bool(body.get("dry"))), action="run")
    if action in ("pause", "resume"):
        return dict(set_widget_state(wid, "paused" if action == "pause" else "active"), action=action)
    if action == "brief":
        w = next((x for x in widgets if x["id"] == wid), None)
        if not w:
            raise BrainError(f"위젯이 없어요: {wid}")
        return dict(staff_brief(w, force=bool(body.get("force"))), action="brief")
    if action == "add":
        if not cli:
            _require_hire(cfg)
        return dict(add_widget(body), action="add")
    if action == "move":
        if not cli:
            _require_hire(cfg)
        return dict(move_widget(wid, body.get("team")), action="move")
    if action == "rename":
        if not cli:
            _require_hire(cfg)
        return dict(rename_widget(wid, body.get("title")), action="rename")
    if action == "remove":
        if not cli:
            _require_hire(cfg)
        return dict(remove_widget(wid), action="remove")
    raise BrainError("action은 run · pause · resume · brief · add · move · rename · remove 중 하나")


DATE_IN_LINE_RE = re.compile(r"(20\d{2})-(\d{2})-(\d{2})")
HISTORY_MAX_BYTES = 2 * 1024 * 1024


def widget_history(w, days=14, today=None):
    """로그 위젯의 날짜별 활동: 날짜가 적힌 줄 수(runs)와 그중 실패 패턴 줄(fails). 최근 days일, 로그 끝 2MB만 읽는다."""
    today = today or date.today()
    src = os.path.expanduser(str(w.get("source") or ""))
    out = {"id": w.get("id"), "days": [{"date": (today - timedelta(days=days - 1 - i)).isoformat(), "runs": 0, "fails": 0} for i in range(days)]}
    if w.get("kind") != "log" or not src or not os.path.isfile(src):
        return out
    idx = {d["date"]: d for d in out["days"]}
    st = w.get("status_cfg") or {}
    fail_re = re.compile(st.get("fail_pattern"), re.I) if st.get("fail_pattern") else None
    try:
        size = os.path.getsize(src)
        with open(src, "rb") as f:
            if size > HISTORY_MAX_BYTES:
                f.seek(size - HISTORY_MAX_BYTES)
            text = f.read().decode("utf-8", "replace")
    except OSError:
        return out
    for ln in text.split("\n"):
        m = DATE_IN_LINE_RE.search(ln)
        if not m:
            continue
        d = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        if d in idx:
            idx[d]["runs"] += 1
            if fail_re and fail_re.search(ln):
                idx[d]["fails"] += 1
    out["total_runs"] = sum(d["runs"] for d in out["days"])
    out["total_fails"] = sum(d["fails"] for d in out["days"])
    return out


def running_widgets(widgets, ps_lines=None, cron_lines=None):
    """지금 도는 자동화 감지. 크론 줄의 '>> 로그'와 위젯 source를 짝지어 스크립트 경로를 얻고, ps 명령줄에 그 경로(또는 로그 stem)가 있으면 가동 중."""
    ps_lines = _ps_lines() if ps_lines is None else ps_lines
    cron_lines = _crontab_lines() if cron_lines is None else cron_lines
    log_to_script = {}
    for line in cron_lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.search(r">>?\s*(\S+\.log)", line)
        parts = line.split()
        if len(parts) < 6:
            continue
        cmd = parts[5:]
        script = next((c for c in cmd if c.endswith((".py", ".sh"))), None)
        if m and script:
            log_to_script[os.path.expanduser(m.group(1))] = script
        elif script:
            log_to_script[Path(script).stem] = script
    out = {}
    for w in widgets:
        src = os.path.expanduser(str(w.get("source") or ""))
        stem = Path(src).stem if src else ""
        keys = set()
        if src in log_to_script:
            keys.add(log_to_script[src])
        if stem and stem in log_to_script:
            keys.add(log_to_script[stem])
        wid = str(w.get("id") or "")
        # 스크립트 파일명 패턴(…/stem*.py|.sh)만 인정. 단어 부분 일치는 다른 앱 명령줄(예: Claude 앱의 '--…flight…')을 오탐한다
        pats = [re.compile(re.escape(k), re.I) if "/" in k else re.compile(r"[\s/]" + re.escape(k) + r"[\w-]*\.(py|sh|js|rb)\b", re.I) for k in keys if k]
        for extra in (stem, wid.replace("-", "_")):
            if extra and len(extra) >= 4 and extra not in ("daily", "gen", "review", "bridge", "growth", "state", "result"):
                pats.append(re.compile(r"[\s/]" + re.escape(extra) + r"[\w-]*\.(py|sh|js|rb)\b", re.I))
        for line in ps_lines:
            low = line.lower()
            if "brain.py" in low or " grep " in low or "claude helper" in low:
                continue
            if any(pt.search(line) for pt in pats):
                cols = line.split(None, 2)
                out[wid] = {"pid": int(cols[0]) if cols and cols[0].isdigit() else None, "etime": cols[1] if len(cols) > 1 else ""}
                break
    return out


def jobs_dir():
    d = os.environ.get("SECOND_BRAIN_JOBS_DIR")
    if d:
        return Path(os.path.expanduser(d))
    cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    base = Path(os.path.expanduser(cfg)) if cfg else home_dir() / ".claude"
    return base / "jobs"


def claude_jobs(now=None, max_age_hours=24):
    """Claude Code 배경 작업(state.json)을 읽어 직원 목록으로. 최근 24시간 안에 갱신된 것만."""
    now = now or datetime.now()
    d = jobs_dir()
    out = []
    if not d.is_dir():
        return out
    for sj in sorted(d.glob("*/state.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            age_min = (now.timestamp() - sj.stat().st_mtime) / 60
            if age_min > max_age_hours * 60:
                continue
            data = json.loads(sj.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        # 일하는 중·막힘은 하루, 끝난 작업은 3시간까지만 자리에 남긴다
        if str(data.get("state") or "") not in ("working", "blocked", "needs_input") and age_min > 180:
            continue
        recent = []
        tl = sj.parent / "timeline.jsonl"
        if tl.is_file():
            try:
                lines = tl.read_text(encoding="utf-8").splitlines()[-5:]
                for ln in lines:
                    try:
                        e = json.loads(ln)
                        txt = e.get("detail") or e.get("summary") or e.get("text") or e.get("state") or ""
                        if txt:
                            recent.append(str(txt)[:120])
                    except ValueError:
                        continue
            except OSError:
                pass
        title = ""
        for c in data.get("children") or []:
            if isinstance(c, dict) and c.get("title"):
                title = str(c["title"])[:24]
                break
        out.append({"id": sj.parent.name, "state": str(data.get("state") or ""), "detail": str(data.get("detail") or "")[:200],
                    "tempo": str(data.get("tempo") or ""), "updated_min": round(age_min, 1), "title": title, "recent": recent})
    return out[:8]


def office_events(widgets, jobs, now=None):
    now = now or datetime.now().astimezone()
    ev = []
    for w in widgets:
        if not w.get("updated_at"):
            continue
        try:
            t = datetime.fromisoformat(w["updated_at"])
        except ValueError:
            continue
        what = w.get("summary") or ""
        ev.append({"ts": t.isoformat(), "time": t.strftime("%H:%M") if t.date() == now.date() else t.strftime("%m-%d %H:%M"),
                   "who": re.sub(r"\s*\(.*\)\s*$", "", str(w.get("title") or w["id"])), "what": what[:80], "bad": w.get("status") in ("fail", "stale")})
    for j in jobs:
        if j.get("detail"):
            t = now - timedelta(minutes=j.get("updated_min") or 0)
            ev.append({"ts": t.isoformat(), "time": t.strftime("%H:%M"), "who": "Claude " + j["id"][:6], "what": j["detail"][:80], "bad": False})
    ev.sort(key=lambda e: e["ts"], reverse=True)
    return ev[:OFFICE_EVENTS_MAX]


OFFICE_KPI_DAYS = 7


def office_kpis(widgets, today=None, days=OFFICE_KPI_DAYS):
    """팀별·전체 7일 KPI: 실행/실패/성공률/일별 막대. 멈춘(state=paused) 위젯과 로그가 아닌 위젯은 뺀다.
    팀 키는 team_for(w)로 dash_office의 방 이름과 맞춘다(팀 미지정 위젯은 「운영팀」 등 기본 팀으로 묶임)."""
    today = today or date.today()
    date_keys = [(today - timedelta(days=days - 1 - i)).isoformat() for i in range(days)]

    def blank_days():
        return {d: {"date": d, "runs": 0, "fails": 0} for d in date_keys}

    teams = {}
    total = {"runs": 0, "fails": 0, "members": 0, "days": blank_days()}
    for w in widgets:
        if w.get("state") == "paused" or w.get("kind") != "log":
            continue
        name = team_for(w)
        t = teams.setdefault(name, {"runs": 0, "fails": 0, "members": 0, "days": blank_days()})
        t["members"] += 1
        total["members"] += 1
        try:
            h = widget_history(w, days=days, today=today)
        except Exception:  # noqa: BLE001 - KPI 한 위젯 오류로 전체를 막지 않는다
            continue
        for d in h["days"]:
            if d["date"] not in t["days"]:
                continue
            t["days"][d["date"]]["runs"] += d["runs"]
            t["days"][d["date"]]["fails"] += d["fails"]
            total["days"][d["date"]]["runs"] += d["runs"]
            total["days"][d["date"]]["fails"] += d["fails"]
        t["runs"] += h.get("total_runs", 0)
        t["fails"] += h.get("total_fails", 0)
        total["runs"] += h.get("total_runs", 0)
        total["fails"] += h.get("total_fails", 0)

    def finalize(rec):
        runs, fails = rec["runs"], rec["fails"]
        rate = round(100 * (runs - fails) / runs) if runs else None
        return {"runs": runs, "fails": fails, "rate": rate, "members": rec["members"],
                "days": [rec["days"][d] for d in date_keys]}

    return {"teams": {name: finalize(t) for name, t in teams.items()}, "total": finalize(total)}


# ───────────────────────── 오늘 근무표: 크론·launchd 스케줄을 오늘 시각으로 펼치기 ─────────────────────────

def _cron_field_values(field, lo, hi):
    """크론 필드(*, 5, 1,4, 1-5, */10, 1-5/2)를 허용값 집합으로. dow는 0-7(7=일요일 별칭)까지 lo/hi로 받는다."""
    out = set()
    for part in field.split(","):
        part = part.strip()
        if not part:
            continue
        base, step = part, 1
        if "/" in base:
            base, step_s = base.split("/", 1)
            step = int(step_s)
        if base == "*":
            start, end = lo, hi
        elif "-" in base:
            a, b = base.split("-")
            start, end = int(a), int(b)
        else:
            start = end = int(base)
        v = start
        while v <= end:
            out.add(v)
            v += step
    return out


def schedule_for(cron_line, today=None):
    """크론 줄(분 시 일 월 요일 + 명령) → 오늘 실행 시각 목록 ["HH:MM", ...] 정렬.

    *, a,b, a-b, */n, a-b/n 지원. 요일은 0-7(0·7 모두 일요일). dom·dow 둘 다 와일드카드가 아니면
    크론 규칙대로 OR(둘 중 하나만 맞아도 실행)로 판정한다."""
    today = today or date.today()
    parts = (cron_line or "").strip().split(None, 5)
    if len(parts) < 5:
        return []
    minute_f, hour_f, dom_f, month_f, dow_f = parts[:5]
    try:
        minutes = _cron_field_values(minute_f, 0, 59)
        hours = _cron_field_values(hour_f, 0, 23)
        doms = _cron_field_values(dom_f, 1, 31)
        months = _cron_field_values(month_f, 1, 12)
        dows = _cron_field_values(dow_f, 0, 7)
    except ValueError:
        return []
    if today.month not in months:
        return []
    dom_wild, dow_wild = dom_f.strip() == "*", dow_f.strip() == "*"
    py_wd = today.weekday()  # 월=0 … 일=6
    dow_hit = any((c + 6) % 7 == py_wd for c in dows)
    dom_hit = today.day in doms
    if dom_wild and dow_wild:
        day_ok = True
    elif dom_wild:
        day_ok = dow_hit
    elif dow_wild:
        day_ok = dom_hit
    else:
        day_ok = dom_hit or dow_hit
    if not day_ok:
        return []
    return sorted(f"{h:02d}:{m:02d}" for h in hours for m in minutes)


def _launchd_calendar_times(cal, today=None):
    """launchd StartCalendarInterval(dict 또는 list[dict]) → 오늘 실행 시각 목록. Weekday 0·7 모두 일요일."""
    today = today or date.today()
    entries = cal if isinstance(cal, list) else [cal]
    py_wd = today.weekday()
    out = set()
    for e in entries:
        if not isinstance(e, dict):
            continue
        wd = e.get("Weekday")
        if wd is not None:
            wds = wd if isinstance(wd, list) else [wd]
            if not any((int(w) + 6) % 7 == py_wd for w in wds):
                continue
        day = e.get("Day")
        if day is not None and int(day) != today.day:
            continue
        month = e.get("Month")
        if month is not None and int(month) != today.month:
            continue
        out.add(f"{int(e.get('Hour', 0)):02d}:{int(e.get('Minute', 0)):02d}")
    return sorted(out)


def _cron_schedule_lines(cron_lines):
    """크론 줄 → {로그 절대경로 또는 스크립트 stem: 원본 크론 줄 전체(스케줄 필드 포함)}.

    widget_commands가 쓰는 _cron_commands는 명령부만 돌려줘 스케줄 필드가 없어서, schedule_for에 넘길
    원본 줄을 따로 인덱싱한다. 매칭 순서(로그 절대경로 우선, 그다음 스크립트 stem)는 _cron_commands와 맞춘다."""
    out = {}
    for line in cron_lines:
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        parts = s.split(None, 5)
        if len(parts) < 6:
            continue
        cmd = parts[5]
        m = re.search(r">>?\s*(\S+\.log)", cmd)
        if m:
            out.setdefault(os.path.expanduser(m.group(1)), []).append(s)  # 같은 로그에 여러 스케줄(10시·15시) 허용
        else:
            script = next((c for c in cmd.split() if c.endswith((".py", ".sh"))), None)
            if script:
                out[Path(script).stem] = s
    return out


def _widget_schedule_plists(plist_dir=None):
    """~/Library/LaunchAgents/*.plist → {StandardOutPath 절대경로: {label, calendar, interval}}.

    _launchd_commands와 같은 파일을 읽지만 라벨만이 아니라 StartCalendarInterval·StartInterval도 함께
    돌려준다(스케줄 계산에 필요)."""
    import plistlib
    d = plist_dir or launch_agents_dir()
    out = {}
    if not d.is_dir():
        return out
    for pl in d.glob("*.plist"):
        try:
            data = plistlib.loads(pl.read_bytes())
        except Exception:  # noqa: BLE001 - 깨진 plist는 건너뜀
            continue
        log_path = data.get("StandardOutPath")
        if not log_path:
            continue
        out[os.path.expanduser(str(log_path))] = {
            "label": data.get("Label"), "calendar": data.get("StartCalendarInterval"), "interval": data.get("StartInterval"),
        }
    return out


def office_schedule(widgets, cron_lines=None, plist_dir=None, today=None, now=None):
    """오늘 근무표: {slots:[{id,title,team,time,state}], intervals:[{id,title,every_min}], next:{id,title,time,in_min}|None}.

    상태는 근사치다(위젯당 하나의 updated_at만 있어 슬롯별 실행 기록은 없음): 미래 슬롯은 upcoming, 과거
    슬롯은 updated_at이 그 슬롯 시각 이후면 done, 아니면 그 위젯의 '가장 최근 지난 슬롯'이고 status가
    fail이면 failed, 그 외에는 due(놓침)로 본다. 멈춘(state=paused) 위젯은 뺀다."""
    now = now or datetime.now().astimezone()
    today = today or now.date()
    now_hm = now.strftime("%H:%M")
    cron_raw = _cron_schedule_lines(_crontab_lines() if cron_lines is None else cron_lines)
    plists = _widget_schedule_plists(plist_dir)
    slots, intervals, upcoming = [], [], []
    for w in widgets:
        if w.get("state") == "paused":
            continue
        src = os.path.expanduser(str(w.get("source") or ""))
        if not src:
            continue
        title = w.get("title") or w["id"]
        times = None
        if src in plists:
            info = plists[src]
            if info.get("calendar"):
                times = _launchd_calendar_times(info["calendar"], today)
            elif info.get("interval"):
                every = max(1, int(info["interval"]) // 60)
                intervals.append({"id": w["id"], "title": title, "every_min": every})
                continue
        if times is None:
            lines_ = cron_raw.get(src) or cron_raw.get(Path(src).stem) or []
            if isinstance(lines_, str):
                lines_ = [lines_]
            if lines_:
                times = sorted({t for ln in lines_ for t in schedule_for(ln, today)})
        if not times:
            continue
        past = [t for t in times if t <= now_hm]
        latest_past = max(past) if past else None
        updated_at = None
        if w.get("updated_at"):
            try:
                updated_at = datetime.fromisoformat(w["updated_at"])
            except ValueError:
                updated_at = None
        for t in times:
            hh, mm = t.split(":")
            slot_dt = datetime(today.year, today.month, today.day, int(hh), int(mm), tzinfo=now.tzinfo)
            if t > now_hm:
                state = "upcoming"
                upcoming.append((slot_dt, w["id"], title, t))
            elif updated_at and updated_at >= slot_dt:
                state = "done"
            elif w.get("status") == "fail" and t == latest_past:
                state = "failed"
            else:
                state = "due"
            slots.append({"id": w["id"], "title": title, "team": team_for(w), "time": t, "state": state})
    slots.sort(key=lambda s: s["time"])
    nxt = None
    if upcoming:
        upcoming.sort(key=lambda u: u[0])
        slot_dt, wid, title, t = upcoming[0]
        nxt = {"id": wid, "title": title, "time": t, "in_min": max(0, round((slot_dt - now).total_seconds() / 60))}
    return {"slots": slots, "intervals": intervals, "next": nxt}


def dash_office(widgets, ps_lines=None, cron_lines=None, now=None):
    """/api/office 응답: teams, widgets(요약), running, jobs, events, assistant_name, kpis, schedule."""
    cfg = load_config()
    teams = {}
    order = []
    for w in widgets:
        name = team_for(w)
        if name not in teams:
            teams[name] = []
            order.append(name)
        teams[name].append(w["id"])
    jobs = claude_jobs()
    return {
        "teams": [{"name": n, "members": teams[n]} for n in order],
        "widgets": widgets,
        "running": running_widgets(widgets, ps_lines, cron_lines),
        "jobs": jobs,
        "events": office_events(widgets, jobs, now),
        "assistant_name": str(cfg.get("assistant_name") or "브레인"),
        "kpis": office_kpis(widgets, today=(now.date() if now else None)),
        "schedule": office_schedule(widgets, cron_lines=cron_lines, now=now),
    }


ASK_TIMEOUT_SEC = 120


def ask_assistant(question, today, vault=None):
    """코어 화면의 자유 질문. 오늘 상태 JSON + 볼트 검색 상위 5건(제목·발췌)을 붙여 헤드리스 Claude에 묻는다.
    명령은 config `ask_cmd`(기본 `claude -p`), 테스트·오프라인은 환경변수 SECOND_BRAIN_ASK_CMD로 대체."""
    q = " ".join(question.split())
    if not q:
        raise BrainError("질문이 비었어요")
    if len(q) > 1000:
        raise BrainError("질문은 1000자까지")
    cfg = load_config()
    cmd = os.environ.get("SECOND_BRAIN_ASK_CMD") or cfg.get("ask_cmd") or "claude -p --output-format text"
    name = str(cfg.get("assistant_name") or "브레인")
    ctx = {k: today.get(k) for k in ("date", "weekday", "agenda", "revisit", "inbox", "tasks", "widgets_summary", "top_widgets", "this_week", "retro_questions", "week_plan") if k in today}
    if (today.get("suggestions") or {}).get("count"):  # 채택 대기 중인 준비 제안: 일정별 항목 텍스트만
        ctx["pending_suggestions"] = {k.split("|", 1)[1]: [i["text"] for i in v.get("items", [])][:6] for k, v in today["suggestions"].get("by_key", {}).items()}
    if ctx.get("agenda"):
        ctx["agenda"] = {k: v for k, v in ctx["agenda"].items() if k in ("today", "next", "upcoming", "steps_today", "sentence")}
    memory = []
    if vault and vault_exists(Path(vault)):
        try:
            for h in dash_search(Path(vault), q, 5):
                if h.get("score", 0) > 0:
                    memory.append({"title": h["title"], "type": h["type"], "created": h.get("created"), "snippet": " ".join((h.get("snippets") or [""])[0].split())[:220]})
        except Exception as e:  # noqa: BLE001
            log(f"경고: 볼트 검색 실패({e})")
    recent = core_chat_today(date.today())[-4:]
    prompt = (f"너는 사용자의 개인 비서 「{name}」이다. 아래 [오늘 상태]는 일정·할 일·되돌아볼 결정·자동화, [기억]은 사용자의 노트 창고에서 질문과 관련 있어 보이는 기록 발췌, [최근 대화]는 오늘 코어 화면에서 나눈 직전 문답이다. "
              f"이 자료와 상식으로 질문에 한국어 해요체로 2~3문장, 220자 안에서 답한다. 기억에 근거하면 어느 기록인지 제목을 짧게 밝힌다. 일정에 weather가 있으면 우산·옷차림을, pending_suggestions가 있으면 보드에서 채택하면 된다고 짧게 덧붙일 수 있다. 자료에 없으면 모른다고 말한다. 목록·마크다운 없이 말로.\n\n"
              f"[오늘 상태]\n{json.dumps(ctx, ensure_ascii=False)}\n\n[기억]\n{json.dumps(memory, ensure_ascii=False)}\n\n[최근 대화]\n{json.dumps(recent, ensure_ascii=False)}\n\n[질문]\n{q}")
    import shlex
    argv = shlex.split(cmd)
    try:
        r = subprocess.run(argv, input=prompt, capture_output=True, text=True, timeout=ASK_TIMEOUT_SEC,
                           env=dict(os.environ, CLAUDE_CONFIG_DIR=os.environ.get("CLAUDE_CONFIG_DIR", "")) if os.environ.get("CLAUDE_CONFIG_DIR") else None)
    except FileNotFoundError:
        raise BrainError(f"답변 명령을 찾을 수 없어요: {argv[0]}")
    except subprocess.TimeoutExpired:
        raise BrainError("답이 늦어요. 잠시 뒤 다시 물어봐 주세요")
    if r.returncode != 0:
        raise BrainError(f"답변 실패: {(r.stderr or r.stdout).strip()[:160]}")
    ans = " ".join(r.stdout.split())[:600]
    log_core_chat(q, ans)
    return {"answer": ans, "via": argv[0], "memory_used": [m["title"] for m in memory]}


def core_log_path():
    return agenda_mod.cache_dir() / "core_log.jsonl"


def log_core_chat(q, a):
    """코어 문답 한 줄 기록(JSONL). 실패해도 답변은 막지 않는다."""
    try:
        p = core_log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": datetime.now().isoformat(timespec="minutes"), "q": q[:500], "a": a[:600]}, ensure_ascii=False) + "\n")
    except OSError as e:
        log(f"경고: 대화 기록 실패({e})")


def core_chat_today(today, limit=8):
    p = core_log_path()
    if not p.exists():
        return []
    day = today.isoformat()
    out = []
    try:
        for ln in _tail_lines(p, 200):
            try:
                d = json.loads(ln)
            except ValueError:
                continue
            if str(d.get("ts", ""))[:10] == day:
                out.append({"time": str(d.get("ts", ""))[11:16], "q": d.get("q", ""), "a": d.get("a", "")})
    except OSError:
        return []
    return out[-limit:]


def note_append_action(vault, body):
    """보드 노트 패널 공용: 메모/태그/할 일을 노트 본문·프론트매터에 바로 추가. body: {action: memo|tag|todo, path, text}"""
    action = body.get("action")
    if action not in ("memo", "tag", "todo"):
        raise BrainError("action은 memo · tag · todo 중 하나")
    rel = str(body.get("path") or "")
    vault = Path(vault).resolve()  # 임시 폴더 심볼릭 링크(/var→/private/var)에서도 같은 기준으로 비교
    p = safe_vault_path(vault, rel)
    if not p.is_file():
        raise FileNotFoundError(rel)
    rel_norm = p.relative_to(vault)
    if rel_norm.parent == Path(".") and rel_norm.name in SKIP_FILES:
        raise BrainError("이 노트는 여기서 수정할 수 없어요")
    text = " ".join(str(body.get("text") or "").split())
    if not text:
        raise BrainError("내용이 비었어요")
    if len(text) > 2000:
        raise BrainError("한 번에 2000자까지만")
    n = Note(vault, p)
    if action == "memo":
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        new_body = _append_section(n.body, "## 메모", f"- {stamp}  {text}")
        write_note(n.path, n.meta, new_body)
    elif action == "todo":
        new_body = _append_section(n.body, "## 할 일", f"- [ ] {text}")
        write_note(n.path, n.meta, new_body)
    else:  # tag
        tag = text.lstrip("#").strip()
        if not tag:
            raise BrainError("태그가 비었어요")
        tags = n.tags
        if tag not in tags:
            tags = (tags + [tag])[:12]
        meta = dict(n.meta)
        meta["tags"] = tags
        write_note(n.path, meta, n.body)
    git_commit(vault, f"brain: note {action} {n.rel}")
    return {"ok": True, "note": dash_note(vault, n.rel)}


def person_action(vault, body):
    """보드 일정 패널 「사람 노트 만들기」 공용: 참석자 이름으로 사람 노트를 찾거나 만든다.

    body: {action: "create", name, event_key?}. 이미 같은(정규화) 이름의 사람 노트가 있으면 그걸
    그대로 쓰고(created=False), event_key가 있으면 그 일정 노트의 프론트매터 people 목록에
    위키링크를 남긴다(일정 노트가 없으면 새로 만든다).
    """
    action = body.get("action")
    if action != "create":
        raise BrainError("action은 create만 지원해요")
    name = " ".join(str(body.get("name") or "").split())
    if not name:
        raise BrainError("이름이 비었어요")
    ek = str(body.get("event_key") or "").strip()
    if ek and "|" not in ek:
        raise BrainError("event_key 형식이 잘못됐어요(YYYY-MM-DD|제목)")
    vault = Path(vault)
    existing = people_index(load_notes(vault)).get(normalize_person_name(name))
    if existing:
        n, created = existing, False
    else:
        ctx = ""
        if ek and "|" in ek:
            day, title = ek.split("|", 1)
            ctx = f"- {day} {title}에서 만남\n"
        path = create_note(vault, "person", name, body=f"# {name}\n\n## 맥락\n{ctx}")
        build_index(vault)
        n, created = Note(vault, path), True
    if ek:
        en, _ = find_or_create_event_note(vault, ek)
        people_list = as_list(en.meta.get("people"))
        link = wikilink(n.stem)
        if link not in people_list:
            meta = dict(en.meta)
            meta["people"] = people_list + [link]
            write_note(en.path, meta, en.body)
    git_commit(vault, f"brain: person create {n.rel}")
    return {"ok": True, "path": n.rel, "created": created}


def person_note_payload(vault, rel, agenda=None):
    """GET /api/person: 사람 노트(dash_note) + 이 사람이 참석자로 매칭된 일정(오늘·다가오는).

    agenda_cache.get()이 돌려준(아직 people이 안 붙은) 원본 agenda dict를 받아 이 호출에서만
    attach_event_notes로 채운다(캐시 자체는 건드리지 않음).
    """
    n = dash_note(vault, rel)
    ag = dict(agenda or {})
    attach_event_notes(vault, ag)
    key = normalize_person_name(n.get("title"))
    events, seen = [], set()
    for lst in ("today", "upcoming"):
        for e in ag.get(lst) or []:
            for p in e.get("people") or []:
                if p.get("path") == rel or normalize_person_name(p.get("name")) == key:
                    ek = e.get("key")
                    if ek and ek not in seen:
                        seen.add(ek)
                        events.append({"key": ek, "title": e.get("title"), "start": e.get("start"),
                                      "days_left": e.get("days_left")})
                    break
    n["events"] = events
    return n


def remember_chat(vault, body):
    """코어 문답을 볼트 노트로. body: {question, answer, title?, tags?}"""
    q = " ".join(str(body.get("question") or "").split())
    a = " ".join(str(body.get("answer") or "").split())
    if not q or not a:
        raise BrainError("질문과 답이 모두 있어야 기억할 수 있어요")
    title = " ".join(str(body.get("title") or "").split()) or q[:40].rstrip("?？.!")
    tags = ["코어", "대화"] + [str(t).strip() for t in (body.get("tags") or []) if str(t).strip()][:3]
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    text = f"# {title}\n\n## 질문\n{q}\n\n## 답\n{a}\n\n맥락: 코어 화면 대화 ({stamp}). 「기억해」로 저장.\n"
    path = create_note(vault, "note", title, tags=tags, body=text)
    build_index(vault)
    git_commit(vault, f"brain: remember {path.stem}")
    return {"ok": True, "path": path.relative_to(Path(vault)).as_posix(), "title": title}


_CAPTURE_URL_RE = re.compile(r"^https?://\S+", re.IGNORECASE)
_CAPTURE_TYPES = ("note", "idea", "source")


def capture_note(vault, body):
    """빠른 캡처: 보드 커맨드 팔레트(메모:/아이디어:/링크:)·코어("메모해: ...")에서 텍스트 한 줄 → 노트 한 장.

    body: {text (1~4000자), type? (note|idea|source, 기본 note; http(s) URL로 시작하면 기본이 source),
          title? (기본: 첫 줄 40자 이내, URL이면 host+path), tags? (최대 5개), project?}
    """
    text = str(body.get("text") or "").strip()
    if not text:
        raise BrainError("기록할 내용이 비어 있어요")
    if len(text) > 4000:
        raise BrainError("내용이 너무 길어요(4000자 이내)")
    ntype = str(body.get("type") or "").strip()
    url_m = _CAPTURE_URL_RE.match(text)
    if ntype:
        if ntype not in _CAPTURE_TYPES:
            raise BrainError(f"알 수 없는 타입: {ntype} (가능: {', '.join(_CAPTURE_TYPES)})")
    else:
        ntype = "source" if url_m else "note"
    title = " ".join(str(body.get("title") or "").split())
    source = None
    if ntype == "source" and url_m:
        source = url_m.group(0)
        if not title:
            parts = urllib.parse.urlsplit(source)
            tail = parts.path.rstrip("/").rsplit("/", 1)[-1]
            title = (parts.netloc + ("/" + tail if tail else ""))[:40]
    if not title:
        first_line = text.splitlines()[0].strip()
        title = (first_line or text)[:40]
    tags = [str(t).strip() for t in (body.get("tags") or []) if str(t).strip()][:5]
    project = str(body.get("project") or "").strip() or None
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    note_body = f"# {title}\n\n{text}\n\n맥락: 보드/코어 빠른 캡처 ({stamp})\n"
    path = create_note(vault, ntype, title, tags=tags, project=project, source=source, body=note_body)
    build_index(vault)
    git_commit(vault, f"brain: capture {path.stem}")
    return {"ok": True, "path": path.relative_to(Path(vault)).as_posix(), "title": title, "type": ntype}


STAFF_BRIEF_LINES = 80


def staff_briefs_path():
    return agenda_mod.cache_dir() / "staff_briefs.json"


def staff_brief(w, today=None, force=False):
    """자동화 직원 한 명의 「이번 주 한 줄」: 최근 로그 80줄 → Claude {did, issue, mood}. 하루 한 번 캐시."""
    today = today or date.today()
    p = staff_briefs_path()
    cache = {}
    if p.exists():
        try:
            cache = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cache = {}
    hit = cache.get(w["id"])
    if hit and hit.get("date") == today.isoformat() and not force:
        return dict(hit, cached=True)
    src = os.path.expanduser(str(w.get("source") or ""))
    lines = []
    if w.get("kind") == "log" and src and os.path.isfile(src):
        lines = [l.strip()[:200] for l in _tail_lines(Path(src), STAFF_BRIEF_LINES) if l.strip()]
    if not lines:
        lines = [str(w.get("summary") or "")]
    hist = widget_history(w, days=7, today=today) if w.get("kind") == "log" else {}
    if not hist.get("total_runs"):  # 로그 줄에 날짜가 없으면 날짜별 집계가 안 되니 숫자를 주지 않는다(0으로 오해 방지)
        hist = {}
    ctx = {"title": w.get("title"), "status": w.get("status"), "state": w.get("state"), "runs_7d": hist.get("total_runs"), "fails_7d": hist.get("total_fails"),
           "since": (today - timedelta(days=6)).isoformat(), "until": today.isoformat(), "log_tail": lines[-STAFF_BRIEF_LINES:]}
    prompt = ("너는 자동화 직원(크론·launchd 작업)의 팀장이다. 아래 [기록]은 이 직원의 최근 로그다. JSON 객체 하나로만 답한다(설명·마크다운 금지). 형식:\n"
              '{"did": "이번 주 한 일 한 문장(60자 이내, 로그에 근거, 숫자 있으면 포함)", "issue": "문제 한 문장(50자 이내, 없으면 \"문제 없음\")", '
              '"mood": "직원 기분 한 마디(15자 이내, 가볍게. 예: 순조로움 / 지쳤어요 / 억울해요)"}\n'
              "로그에 없는 사실을 만들지 마라.\n\n[기록]\n" + json.dumps(ctx, ensure_ascii=False))
    raw = _run_claude_json(prompt, timeout=180)
    if isinstance(raw, list):
        raw = raw[0] if raw and isinstance(raw[0], dict) else {}
    if not isinstance(raw, dict):
        raise BrainError("요약 응답이 객체가 아니에요")
    rec = {"id": w["id"], "date": today.isoformat(), "did": " ".join(str(raw.get("did") or "").split())[:120],
           "issue": " ".join(str(raw.get("issue") or "").split())[:100] or "문제 없음", "mood": " ".join(str(raw.get("mood") or "").split())[:20],
           "runs_7d": hist.get("total_runs"), "fails_7d": hist.get("total_fails")}
    cache[w["id"]] = rec
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as e:
        log(f"경고: 직원 요약 캐시 실패({e})")
    return dict(rec, cached=False)


def cmd_task(args):
    v = require_vault()
    today = date.today()
    if args.action == "list":
        widgets = collect_widgets()
        ag = collect_agenda_safe(7)
        attach_event_notes(v, ag)
        b = dash_tasks(v, today, widgets, ag)
        lines = []
        for k, label in (("today", "오늘"), ("week", "이번 주"), ("someday", "언젠가"), ("waiting", "기다림")):
            lines.append(f"[{label} {len(b[k])}]")
            for t in b[k]:
                tag = {"decision": "결정", "automation": "자동화", "prep": "준비"}.get(t.get("kind"), "")
                num = f"#{t['line']}" if t.get("kind") == "task" else f"({tag})"
                extra = f"  마감 {t['due']}" if t.get("due") and t.get("kind") == "task" else ""
                if t.get("waiting"):
                    extra = f"  ← {t['waiting']}" + (f" {t['waiting_days']}일째" if t.get("waiting_days") is not None else "")
                lines.append(f"  {num:>8}  {t['text']}{extra}")
        emit(args, b, "\n".join(lines))
        return EXIT_OK
    if args.action == "add":
        if not args.arg1:
            raise BrainError("내용이 필요해요: task add \"내용\"")
        res = task_action(v, {"action": "add", "text": args.arg1, "due": args.due or ("tomorrow" if args.tomorrow else None),
                              "someday": args.someday, "waiting": args.waiting, "project": args.project}, today)
        emit(args, res, f"추가: {res['text']}  (#{res['line']})")
        return EXIT_OK
    if args.action == "carry":
        res = task_action(v, {"action": "carry"}, today)
        emit(args, res, f"오늘 남은 할 일 {res['moved']}개를 {res['to']}로 옮겼어요" if res["moved"] else "옮길 것이 없어요")
        return EXIT_OK
    if args.arg1 is None or not str(args.arg1).lstrip("#").isdigit():
        raise BrainError("줄 번호가 필요해요(task list의 # 값)")
    line = int(str(args.arg1).lstrip("#"))
    if args.action in ("done", "undo"):
        res = task_action(v, {"action": "check", "line": line, "done": args.action == "done"}, today)
        emit(args, res, "완료 표시" if args.action == "done" else "완료 해제")
        return EXIT_OK
    if args.action == "move":
        res = task_action(v, {"action": "move", "line": line, "to": args.arg2 or "tomorrow"}, today)
        emit(args, res, f"옮김 → {args.arg2 or 'tomorrow'}")
        return EXIT_OK
    res = task_action(v, {"action": "remove", "line": line}, today)
    emit(args, res, "지움")
    return EXIT_OK


REMIND_STATE = "reminded.json"


def _remind_state_path():
    return agenda_mod.cache_dir() / REMIND_STATE


def due_reminders(ag, now, steps_before=10, events_before=30):
    """지금부터 N분 안에 시작하는 동선 단계·시간 일정. 반환 [{id, when, text}]."""
    out = []
    tz = now.tzinfo
    for st in ag.get("steps_today") or []:
        t = datetime.fromisoformat(st["start"]).replace(tzinfo=tz)
        lead = (t - now).total_seconds() / 60
        if -2 <= lead <= steps_before:
            out.append({"id": f"step|{st['start']}|{st['text']}", "when": st["time"], "text": f"{st['time']} {st['text']}" + (f" ({st['event_title']})" if st.get("event_title") else "")})
    for e in ag.get("today") or []:
        if e.get("all_day"):
            continue
        t = datetime.fromisoformat(e["start"])
        lead = (t - now).total_seconds() / 60
        if -2 <= lead <= events_before:
            out.append({"id": f"event|{e['start']}|{e['title']}", "when": e["start"][11:16], "text": f"{e['start'][11:16]} {e['title']}" + (f" @ {e['location']}" if e.get("location") else "") + f" ({int(max(0, lead))}분 뒤)"})
    return out


def cmd_remind(args):
    """출발·시작 알림. launchd로 10분마다 돌리고, 같은 알림은 한 번만 보낸다."""
    try:
        v = vault_path()
        if not vault_exists(v):
            v = None
    except BrainError:
        v = None
    now = datetime.now().astimezone()
    ag = collect_agenda_safe(2, now=now)
    if v:
        attach_event_notes(v, ag)
    due = due_reminders(ag, now, args.steps_before, args.events_before)
    sp = _remind_state_path()
    try:
        state = json.loads(sp.read_text(encoding="utf-8")) if sp.is_file() else {}
    except (OSError, ValueError):
        state = {}
    today = now.date().isoformat()
    state = {k: v2 for k, v2 in state.items() if str(v2)[:10] == today}  # 어제 것은 잊는다
    fresh = [d for d in due if d["id"] not in state]
    sent = 0
    if fresh and args.kakao:
        msg = "⏰ " + " / ".join(d["text"] for d in fresh)[:190]
        res = notify(msg)
        if res["sent"]:
            sent = len(fresh)
        elif not res["channels"]:
            log("알림 채널이 없어요(카톡 헬퍼 또는 macOS 알림 센터).")
        else:
            errs = "; ".join(c["error"] for c in res["channels"] if c.get("error"))
            log(f"알림 발송 실패: {errs[:160]}")
    if fresh and (sent or not args.kakao):
        for d in fresh:
            state[d["id"]] = now.isoformat(timespec="minutes")
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    emit(args, {"due": due, "fresh": fresh, "sent": sent},
         ("알림 " + str(len(fresh)) + "건" + (" 발송" if sent else "") + ": " + " / ".join(d["text"] for d in fresh)) if fresh else "지금 알릴 것 없음")
    return EXIT_OK


def cmd_event(args):
    v = require_vault()
    if args.action == "show":
        exact, _ = event_notes_index(v)
        n = exact.get(args.key)
        if not n:
            emit(args, {"found": False, "key": args.key}, f"아직 메모가 없는 일정이에요: {args.key}")
            return EXIT_OK
        emit(args, dict(_event_note_payload(n), found=True, body=n.body), n.path.read_text(encoding="utf-8").rstrip("\n"))
        return EXIT_OK
    text = args.text
    if args.body_file:
        text = sys.stdin.read() if args.body_file == "-" else Path(os.path.expanduser(args.body_file)).read_text(encoding="utf-8")
    res = event_note_action(v, {"action": args.action, "key": args.key, "text": text, "end": args.end, "location": args.location, "day": args.day})
    note = res["note"]
    what = {"memo": "메모 추가", "todo": "준비 항목 추가", "step": "동선 추가"}[args.action]
    emit(args, res, f"{'새 일정 노트 생성 후 ' if res['created'] else ''}{what}: {note['path']}"
                    + (f" (준비 {note['done']}/{note['total']})" if note["total"] else "")
                    + (f" (동선 {len(note['steps'])}단계)" if note.get("steps") else ""))
    return EXIT_OK


def cmd_agenda(args):
    ag = collect_agenda_safe(args.days)
    try:
        attach_event_notes(vault_path(), ag)
    except BrainError:
        pass
    emit(args, ag, agenda_mod.agenda_human(ag))
    return EXIT_OK


def kakao_helper_path(cfg=None):
    cfg = cfg or load_config()
    p = Path(os.path.expanduser(cfg.get("kakao_cmd") or "~/.local/k-skill-cron/notify_kakao.py"))
    return p if p.is_file() else None


NOTIFY_CHANNELS_DEFAULT = ("kakao", "center")


def _osa_quote(text):
    """AppleScript 문자열 리터럴에 안전히 넣도록 백슬래시·쌍따옴표를 이스케이프."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _notify_log(channel, ok, text):
    """SECOND_BRAIN_NOTIFY_LOG가 설정돼 있으면 시도 한 건을 한 줄로 남긴다(테스트용)."""
    path = os.environ.get("SECOND_BRAIN_NOTIFY_LOG")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{channel}\t{ok}\t{text[:80]}\n")
    except OSError:
        pass


def _notify_center_available(cfg):
    return sys.platform == "darwin" and cfg.get("notify_center") is not False


def notify(text, title="세컨드브레인", cfg=None):
    """알림 발송(카톡 헬퍼 → 없으면 macOS 알림 센터). 절대 예외를 던지지 않는다.

    config notify_channels(list, 기본 ["kakao", "center"])로 채널·순서를 고른다.
    앞에서부터 실제로 쓸 수 있는(설치돼 있거나 플랫폼이 맞는) 첫 채널 하나만 시도한다
    (둘 다 매번 울리면 카톡을 이미 쓰는 사람에게 중복 알림이 되므로).
    env SECOND_BRAIN_NOTIFY_DRY=1이면 아무것도 실행하지 않고 시도할 채널만 ok로 반환(테스트용).
    env SECOND_BRAIN_NOTIFY_LOG=경로면 시도마다 한 줄(채널\tok\ttext[:80])을 남긴다.
    반환: {"sent": bool, "channels": [{"name", "ok", "error"}], "text": text}
    """
    cfg = cfg or load_config()
    dry = os.environ.get("SECOND_BRAIN_NOTIFY_DRY") == "1"
    wanted = cfg.get("notify_channels")
    if not isinstance(wanted, list) or not wanted:
        wanted = list(NOTIFY_CHANNELS_DEFAULT)
    wanted = [c for c in wanted if c in NOTIFY_CHANNELS_DEFAULT]

    channels = []
    for name in wanted:
        if name == "kakao":
            helper = kakao_helper_path(cfg)
            if not helper:
                continue
            if dry:
                channels.append({"name": "kakao", "ok": True, "error": None})
                _notify_log("kakao", True, text)
                break
            try:
                r = subprocess.run([sys.executable, str(helper), text], capture_output=True, text=True, timeout=60)
                ok, err = r.returncode == 0, None
                if not ok:
                    err = (r.stderr or r.stdout).strip()[:200]
            except Exception as e:  # noqa: BLE001 - 알림은 실패해도 앱을 죽이지 않는다
                ok, err = False, str(e)[:200]
            channels.append({"name": "kakao", "ok": ok, "error": err})
            _notify_log("kakao", ok, text)
            break
        elif name == "center":
            if not _notify_center_available(cfg):
                continue
            if dry:
                channels.append({"name": "center", "ok": True, "error": None})
                _notify_log("center", True, text)
                break
            script = f'display notification "{_osa_quote(text)}" with title "{_osa_quote(title)}"'
            try:
                r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=10)
                ok, err = r.returncode == 0, None
                if not ok:
                    err = (r.stderr or r.stdout).strip()[:200]
            except Exception as e:  # noqa: BLE001 - 알림은 실패해도 앱을 죽이지 않는다
                ok, err = False, str(e)[:200]
            channels.append({"name": "center", "ok": ok, "error": err})
            _notify_log("center", ok, text)
            break

    return {"sent": any(c["ok"] for c in channels), "channels": channels, "text": text}


def evening_brief(t, tomorrow_events):
    """저녁 마감 문장(≤200자): 오늘 남은 할 일, 내일 첫 일정·동선, 되돌아볼 결정."""
    tk = t.get("tasks") or {}
    left = [x for x in (tk.get("today") or []) if x.get("kind") == "task" and not x.get("done")]
    parts = [f"[{t['date']} 저녁 마감]"]
    if left:
        parts.append(f"남은 할 일 {len(left)}: " + ", ".join(x["text"] for x in left[:3]) + (" 등" if len(left) > 3 else "") + " → 내일로 옮길까요? (코어에 '남은 할 일 내일로')")
    else:
        parts.append("오늘 할 일은 다 끝났어요")
    if tomorrow_events:
        e = tomorrow_events[0]
        parts.append("내일 " + ("종일 " if e["all_day"] else e["start"][11:16] + " ") + e["title"] + (f" 외 {len(tomorrow_events) - 1}" if len(tomorrow_events) > 1 else ""))
        w = e.get("weather")
        if w and (w.get("umbrella") or w.get("cold") or w.get("hot")):
            # weather_mod.weather_sentence는 우산 문구만 다루고 형식도 달라 재사용하지 않고 직접 구성한다.
            word = "우산" if w.get("umbrella") else ("겉옷" if w.get("cold") else "더위")
            parts.append(f"내일 {w.get('place')} {w.get('summary')}, {word}")
    else:
        parts.append("내일 일정 없음")
    return _clip(" / ".join(parts), KAKAO_MAX)


def cmd_ask(args):
    """터미널에서 비서에게 자유 질문: 코어 화면의 /api/ask와 같은 맥락(오늘 상태·볼트 검색·최근 대화)으로 헤드리스 Claude에 묻는다."""
    try:
        v = vault_path()
        if not vault_exists(v):
            v = None
    except BrainError:
        v = None
    t = dash_today(v)
    r = ask_assistant(" ".join(args.text), t, v)
    emit(args, r, r["answer"] + (("\n\n참고한 기록: " + ", ".join(r["memory_used"][:3])) if r.get("memory_used") else ""))
    return EXIT_OK


def cmd_brief(args):
    """아침 브리핑: today와 같은 내용. --evening이면 저녁 마감 문장. --kakao면 200자 카톡 발송(헬퍼 있을 때)."""
    try:
        v = vault_path()
        if not vault_exists(v):
            v = None
    except BrainError:
        v = None
    t = dash_today(v)
    if getattr(args, "evening", False):
        ag = collect_agenda_safe(2)
        if v:
            attach_event_notes(v, ag)  # 날씨(umbrella/cold/hot) 필드가 있어야 evening_brief가 옷차림을 알려줄 수 있다
        tomorrow = [e for e in ag.get("upcoming") or [] if e.get("days_left") == 1]
        t["kakao"] = evening_brief(t, tomorrow)
        t["evening"] = True
        if getattr(args, "journal", False) and v:
            try:
                j = make_journal(v, date.today(), force=True)
                if not j["empty"]:
                    t["journal"] = {"path": j["path"], "kakao": j["kakao"]}
                    t["kakao"] = _clip(t["kakao"] + (" / 일지: " + j["kakao"] if j.get("kakao") else ""), KAKAO_MAX)
            except BrainError as e:
                log(f"일지 건너뜀: {e}")
    if args.kakao:
        res = notify(t["kakao"])
        t["sent"] = res["sent"]
        if not res["channels"]:
            log("알림 채널이 없어요(카톡 헬퍼 또는 macOS 알림 센터). ~/.local/k-skill-cron/notify_kakao.py 또는 config kakao_cmd를 확인하세요.")
            emit(args, t, today_human(t))
            return EXIT_INPUT
        if not t["sent"]:
            errs = "; ".join(c["error"] for c in res["channels"] if c.get("error"))
            log(f"알림 발송 실패: {errs[:200]}")
        emit(args, t, (t["kakao"] if t.get("evening") else today_human(t)) + ("\n\n알림 발송 완료" if t["sent"] else "\n\n알림 발송 실패"))
        return EXIT_OK if t["sent"] else EXIT_INPUT
    emit(args, t, (t["kakao"] if t.get("evening") else today_human(t)))
    return EXIT_OK


AGENT_SPECS = {
    "brief": {"label": "com.secondbrain.brief", "args": ["brief", "--kakao"], "calendar": {"Hour": 7, "Minute": 0}, "title": "아침 브리핑 카톡 (매일 7시)"},
    "remind": {"label": "com.secondbrain.remind", "args": ["remind", "--kakao"], "interval": 600, "title": "출발·시작 알림 (10분마다)"},
    "evening": {"label": "com.secondbrain.evening", "args": ["brief", "--evening", "--journal", "--kakao"], "calendar": {"Hour": 21, "Minute": 30}, "title": "저녁 마감 카톡 + 하루 일지 (매일 21:30)"},
    "backup": {"label": "com.secondbrain.backup", "args": ["backup"], "calendar": {"Hour": 23, "Minute": 0}, "title": "볼트 백업 (매일 23시)"},
    "prepare": {"label": "com.secondbrain.prepare", "args": ["prepare"], "calendar": {"Hour": 6, "Minute": 40}, "title": "일정 준비 제안 (매일 6:40)"},
    "retro": {"label": "com.secondbrain.retro", "args": ["retro", "--kakao"], "calendar": {"Weekday": 1, "Hour": 9, "Minute": 0}, "title": "주간 회고 (월 9시)"},
    "serve": {"label": "com.secondbrain.serve", "args": ["serve", "--port", "7777"], "keepalive": True, "title": "대시보드 서버 (상주)"},
}

# `agents install`/`remove`를 이름 없이 실행할 때의 기본 대상. serve(상주 서버)는 이름을 직접 줘야만 설치된다 —
# 새 사용자가 `agents install`만 실행했다가 launchd에 상주 프로세스가 조용히 깔리는 걸 막기 위함.
DEFAULT_AGENT_NAMES = [n for n in AGENT_SPECS if n != "serve"]

# 위젯 자동 등록용 로그 판정 패턴(공용 vs serve 전용). serve의 시작 줄은 cmd_serve가 찍는 `http://127.0.0.1:<port>` 그대로다.
_AGENT_STATUS_OK = "카톡 발송 완료|알릴 것 없음|발송|백업 완료|준비 제안|제안할 일정이 없어요|주간 회고|회고를 쓰지 않았어요"
_AGENT_STATUS_FAIL = "Traceback|실패"
_SERVE_STATUS_OK = r"http://127\.0\.0\.1:\d+"
_SERVE_STATUS_FAIL = "Traceback|Address already in use|실패"


def agents_log_dir():
    d = agenda_mod.cache_dir() / "agents"
    d.mkdir(parents=True, exist_ok=True)
    return d


def agent_plist(name, spec, brain_path=None, python=None):
    """launchd plist(dict). 파이썬·brain.py 경로는 지금 실행 중인 것을 쓴다."""
    import plistlib
    brain_path = brain_path or str(Path(__file__).resolve())
    python = python or sys.executable
    env = {"HOME": str(home_dir()), "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")}
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        env["CLAUDE_CONFIG_DIR"] = os.environ["CLAUDE_CONFIG_DIR"]
    log = str(agents_log_dir() / f"{name}.log")
    d = {"Label": spec["label"], "ProgramArguments": [python, brain_path] + spec["args"], "EnvironmentVariables": env,
         "StandardOutPath": log, "StandardErrorPath": log}
    if spec.get("keepalive"):
        # 상주 서버: 로그인 시 시작하고 죽으면(비정상 종료 시에만) 재시작. `agents remove`가 bootout하면 그대로 내려간다.
        d["RunAtLoad"] = True
        d["KeepAlive"] = {"SuccessfulExit": False}
        d["ThrottleInterval"] = 10
    else:
        d["RunAtLoad"] = False
        if "calendar" in spec:
            d["StartCalendarInterval"] = spec["calendar"]
        else:
            d["StartInterval"] = spec["interval"]
    return plistlib.dumps(d), log


def _launchctl(*args):
    if os.environ.get("SECOND_BRAIN_NO_LAUNCHCTL"):  # 테스트·드라이런: 실제 launchd를 건드리지 않는다
        return 0, "skipped"
    try:
        r = subprocess.run(["launchctl", *args], capture_output=True, text=True, timeout=20)
        return r.returncode, (r.stdout + r.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def agents_install(names, dry=False, force=False, port=None):
    """plist를 ~/Library/LaunchAgents에 쓰고 bootstrap. 이미 같은 Label이 있으면 --force 없이는 건너뜀. 위젯도 추가.
    port는 serve에만 적용되는 재정의(기본 7777)."""
    out = []
    uid = os.getuid()
    plist_dir = launch_agents_dir()
    plist_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        spec = AGENT_SPECS[name]
        if name == "serve" and port is not None:
            spec = dict(spec, args=["serve", "--port", str(port)])
        target = plist_dir / f"{spec['label']}.plist"
        data, log = agent_plist(name, spec)
        rec = {"name": name, "label": spec["label"], "plist": str(target), "log": log, "action": "install"}
        if target.exists() and not force:
            rec["action"] = "skip(이미 있음, --force로 덮어쓰기)"
            out.append(rec)
            continue
        if dry:
            rec["action"] = "dry"
            out.append(rec)
            continue
        target.write_bytes(data)
        _launchctl("bootout", f"gui/{uid}/{spec['label']}")
        rc, msg = _launchctl("bootstrap", f"gui/{uid}", str(target))
        rec["bootstrap"] = "ok" if rc == 0 else msg[:160]
        if not Path(log).exists():
            Path(log).write_text(f"{datetime.now():%F %T} 등록됨\n", encoding="utf-8")
        out.append(rec)
    # 위젯: 있으면 그대로
    if not dry:
        p = widgets_config_path()
        try:
            cfg = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {"allow_commands": False, "widgets": []}
        except ValueError:
            cfg = None
        if isinstance(cfg, dict):
            ids = {str(w.get("id")) for w in cfg.get("widgets", []) if isinstance(w, dict)}
            for rec in out:
                wid = f"brain-{rec['name']}"
                if wid in ids or rec["action"].startswith("skip"):
                    continue
                if rec["name"] == "serve":
                    # 상주 서버는 알림 발송류 패턴이 아니라 시작 줄(`http://127.0.0.1:<port>`)로 판정한다.
                    # 로그가 tick마다 갱신되지 않으므로 stale_minutes는 생략(경과 판정 비활성).
                    title, status = "대시보드 서버", {"ok_pattern": _SERVE_STATUS_OK, "fail_pattern": _SERVE_STATUS_FAIL}
                else:
                    title = AGENT_SPECS[rec["name"]]["title"]
                    status = {"ok_pattern": _AGENT_STATUS_OK, "fail_pattern": _AGENT_STATUS_FAIL,
                              "stale_minutes": {"remind": 40, "retro": 8 * 1440 + 120}.get(rec["name"], 1560)}
                cfg.setdefault("widgets", []).insert(0, {"id": wid, "title": title, "kind": "log", "source": rec["log"], "team": "운영팀",
                                                          "status": status, "lines": 3})
                rec["widget"] = wid
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def _serve_default_port():
    args = AGENT_SPECS["serve"]["args"]
    return int(args[args.index("--port") + 1]) if "--port" in args else 7777


def _serve_installed_port(target):
    """설치된 plist에서 실제 --port 값을 읽는다. 없거나 못 읽으면 기본값."""
    if target.is_file():
        import plistlib
        try:
            d = plistlib.loads(target.read_bytes())
            pa = d.get("ProgramArguments", [])
            if "--port" in pa:
                return int(pa[pa.index("--port") + 1])
        except (ValueError, plistlib.InvalidFileException, KeyError, IndexError):
            pass  # 손상된 plist: 기본 포트로 대체
    return _serve_default_port()


def _probe_serve_port(port, timeout=1.0):
    """127.0.0.1:<port>/api/session 이 응답하면 서버가 살아있다고 본다(4xx/5xx여도 응답은 응답)."""
    import urllib.error
    import urllib.request
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/api/session", timeout=timeout)
        return True
    except urllib.error.HTTPError:
        return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def agents_status(serve_port=None):
    uid = os.getuid()
    out = []
    for name, spec in AGENT_SPECS.items():
        target = launch_agents_dir() / f"{spec['label']}.plist"
        rc, msg = _launchctl("print", f"gui/{uid}/{spec['label']}")
        state = "loaded" if rc == 0 else "not loaded"
        rec = {"name": name, "label": spec["label"], "installed": target.exists(), "state": state, "title": spec["title"]}
        if name == "serve":
            port = serve_port if serve_port is not None else _serve_installed_port(target)
            rec["port"] = port
            rec["reachable"] = _probe_serve_port(port)
        out.append(rec)
    return out


def agents_remove(names):
    uid = os.getuid()
    out = []
    for name in names:
        spec = AGENT_SPECS[name]
        target = launch_agents_dir() / f"{spec['label']}.plist"
        _launchctl("bootout", f"gui/{uid}/{spec['label']}")
        if target.exists():
            target.unlink()
        out.append({"name": name, "label": spec["label"], "removed": True})
    return out


BACKUP_KEEP = 14


def backup_vault(vault, dest_dir=None, keep=BACKUP_KEEP, now=None):
    """볼트를 zip으로 백업(~/.cache/second-brain/backups/brain-YYYYMMDD-HHMM.zip). 같은 날 여러 번이면 덮어씀. 오래된 것은 keep개만 남김."""
    import zipfile
    now = now or datetime.now()
    vault = Path(vault)
    dest = Path(dest_dir) if dest_dir else agenda_mod.cache_dir() / "backups"
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / f"brain-{now:%Y%m%d}.zip"
    count = 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for root_dir, dirs, files in os.walk(vault):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for fn in files:
                fp = Path(root_dir) / fn
                z.write(fp, fp.relative_to(vault).as_posix())
                count += 1
    olds = sorted(dest.glob("brain-*.zip"))
    removed = []
    while len(olds) > keep:
        victim = olds.pop(0)
        victim.unlink()
        removed.append(victim.name)
    return {"path": str(target), "files": count, "bytes": target.stat().st_size, "removed": removed}


def _restore_zip_targets(vault, names):
    """zip 멤버 이름을 볼트 경로로 매핑하면서 zip-slip(절대경로·.. ·볼트 밖 탈출)을 거부한다."""
    from pathlib import PurePosixPath
    vault_real = vault.resolve()
    targets = {}
    for name in names:
        p = PurePosixPath(name.replace("\\", "/"))
        if p.is_absolute() or ".." in p.parts:
            raise BrainError(f"안전하지 않은 백업 zip입니다(위험한 경로): {name}")
        target = (vault / name).resolve()
        try:
            target.relative_to(vault_real)
        except ValueError:
            raise BrainError(f"안전하지 않은 백업 zip입니다(볼트 밖 경로): {name}")
        targets[name] = target
    return targets


def restore_vault(vault, zip_path, dry=False, keep_current=True, safety_backup=True, dest_dir=None, now=None):
    """백업 zip을 볼트에 복구한다(backup_vault의 역연산).

    - zip이 볼트 백업 형태(BRAIN.md 또는 notes/ 포함)가 아니면 거부.
    - zip-slip(절대경로/..) 멤버가 있으면 거부.
    - keep_current=True(기본)면 zip에 없는 기존 파일은 그대로 둔다(보존).
      False(--replace)면 그런 파일을 지운다.
    - dry=True면 아무것도 쓰지 않고 계획만 돌려준다.
    - safety_backup=True(기본)면 덮어쓰기 전에 backup_vault로 안전 백업을 만든다.
    """
    import shutil
    import zipfile
    vault = Path(vault)
    now = now or datetime.now()
    zip_path = Path(zip_path)
    if not zip_path.is_file():
        raise BrainError(f"백업 zip을 찾을 수 없습니다: {zip_path}")
    with zipfile.ZipFile(zip_path) as z:
        names = [i.filename for i in z.infolist() if not i.filename.endswith("/")]
    if "BRAIN.md" not in names and not any(n.startswith("notes/") for n in names):
        raise BrainError(f"볼트 백업 zip이 아닌 것 같아요(BRAIN.md·notes/ 없음): {zip_path.name}")
    targets = _restore_zip_targets(vault, names)

    existing = {}
    for root_dir, dirs, files in os.walk(vault):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fn in files:
            fp = Path(root_dir) / fn
            existing[fp.relative_to(vault).as_posix()] = fp

    zip_names = set(names)
    created = sorted(n for n in zip_names if n not in existing)
    overwritten = sorted(n for n in zip_names if n in existing)
    preserved = sorted(n for n in existing if n not in zip_names)

    result = {"zip": str(zip_path), "created": created, "overwritten": overwritten,
              "preserved": preserved, "removed": [], "dry_run": bool(dry), "safety_backup": None}
    if dry:
        return result

    if safety_backup:
        b = backup_vault(vault, dest_dir=dest_dir, now=now)
        src = Path(b["path"])
        dst = src.parent / f"brain-{now:%Y%m%d}-before-restore-{now:%H%M%S}.zip"
        if src != dst:
            shutil.move(str(src), str(dst))
        result["safety_backup"] = str(dst)

    if not keep_current:
        removed = []
        for rel, fp in existing.items():
            if rel in zip_names:
                continue
            fp.unlink()
            removed.append(rel)
        for root_dir, dirs, files in os.walk(vault, topdown=False):
            rd = Path(root_dir)
            if rd == vault or any(part in SKIP_DIRS for part in rd.relative_to(vault).parts):
                continue
            try:
                if not any(rd.iterdir()):
                    rd.rmdir()
            except OSError:
                pass
        result["removed"] = sorted(removed)

    with zipfile.ZipFile(zip_path) as z:
        for name in names:
            target = targets[name]
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(name) as src_f, open(target, "wb") as dst_f:
                shutil.copyfileobj(src_f, dst_f)

    build_index(vault)
    git_commit(vault, f"brain: restore from {zip_path.name}")
    return result


ENRICH_BATCH = 6
ENRICH_BODY_CHARS = 1600


def _run_claude_json(prompt, timeout=240):
    """헤드리스 Claude(또는 SECOND_BRAIN_ASK_CMD)에 프롬프트를 주고 JSON을 파싱해 돌려준다."""
    import shlex
    cfg = load_config()
    cmd = os.environ.get("SECOND_BRAIN_ASK_CMD") or cfg.get("ask_cmd") or "claude -p --output-format text"
    argv = shlex.split(cmd)
    r = subprocess.run(argv, input=prompt, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise BrainError(f"Claude 호출 실패: {(r.stderr or r.stdout).strip()[:200]}")
    out = r.stdout.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", out, re.S)
    if m:
        out = m.group(1).strip()
    cands = [i for i in (out.find("["), out.find("{")) if i >= 0]
    start = min(cands) if cands else 0
    end = max(out.rfind("]"), out.rfind("}"))
    for chunk in (out[start:], out[start:end + 1] if end >= start else ""):
        if not chunk:
            continue
        try:
            return json.loads(chunk)
        except ValueError:
            continue
    raise BrainError(f"Claude 응답이 JSON이 아니에요: {out[:160]}")


def journal_material(vault, today, widgets=None, agenda=None):
    """오늘 하루의 재료를 한 덩어리로. 비어 있으면 일지를 쓰지 않는다."""
    day = today.isoformat()
    notes = load_notes(vault) if vault else []
    new_notes = [{"type": n.type, "title": n.title, "summary": str(n.meta.get("summary") or "")[:160]}
                 for n in notes if n.created == day and n.type not in ("journal", "event")][:12]
    memos = []
    for n in notes:
        if n.type != "event":
            continue
        for ln in n.body.split("\n"):
            m = re.match(r"^- (\d{4}-\d{2}-\d{2}) \d{2}:\d{2}\s+(.+)$", ln.strip())
            if m and m.group(1) == day:
                memos.append(f"{n.title}: {m.group(2)[:120]}")
    ag = dict(agenda if agenda is not None else collect_agenda_safe(1, now=datetime.combine(today, datetime.min.time()).astimezone()))
    events = [(("종일 " if e.get("all_day") else e["start"][11:16] + " ") + e["title"]) for e in (ag.get("today") or [])][:8]
    weather_today = next((e["weather"]["summary"] for e in (ag.get("today") or []) if e.get("weather")), None)
    tb = dash_tasks(vault, today, widgets or [], ag) if vault else {"done_recent": [], "today": []}
    is_today = today == date.today()
    done = [x["text"] for x in tb.get("done_recent") or []][-6:] if is_today else []  # 완료 시각이 없어 오늘 일지에만 넣는다
    own_open = lambda b: [x["text"] for x in tb.get(b) or [] if x.get("kind") == "task" and not x.get("done")]
    left = (own_open("today") or own_open("week") or own_open("someday"))[:5]  # 내일 첫 일 후보: 오늘 남은 것 → 이번 주 → 언젠가
    bad = [f"{w['title']}: {w.get('summary') or w['status']}" for w in (widgets or []) if w.get("state") != "paused" and w.get("status") in ("fail", "stale")][:4]
    jobs = []
    for j in claude_jobs() if is_today else []:
        if j.get("title") or j.get("detail"):
            jobs.append((j.get("title") or "") + (" — " + j["detail"][:80] if j.get("detail") else ""))
    chat = [{"q": c["q"][:120], "a": c["a"][:160]} for c in core_chat_today(today)][-6:]
    mat = {"date": day, "weekday": WEEKDAYS_KO[today.weekday()], "new_notes": new_notes, "event_memos": memos[:8],
           "events": events, "tasks_done": done, "tasks_left": left, "automation_issues": bad, "claude_jobs": jobs[:6], "core_chat": chat,
           "weather_today": weather_today}
    mat["empty"] = not (new_notes or memos or events or done or jobs or chat)
    return mat


def journal_prompt(mat):
    return ("너는 사용자의 하루를 대신 기록하는 비서다. 아래 [재료]만 근거로 오늘 일지를 JSON 객체 하나로 답한다(설명·마크다운 금지). 형식:\n"
            '{"today": ["오늘 한 일" 3~5문장, 각 60자 이내, 과거형 평서문, 재료에 있는 사실만], '
            '"win": "오늘 잘한 것 한 줄(40자 이내, 없으면 빈 문자열)", '
            '"tomorrow": "내일 첫 일로 삼을 것 한 줄(40자 이내, tasks_left·events를 우선)", '
            '"kakao": "카톡용 한 줄 요약 70자 이내"}\n'
            "규칙: 재료에 없는 일을 만들지 마라. 자기 칭찬·감탄사·이모지 없이 담담하게. 사용자를 '나'로 쓴다(1인칭 일지).\n\n[재료]\n"
            + json.dumps(mat, ensure_ascii=False))


def write_journal(vault, today, raw, mat, force=False):
    """일지 노트 생성/갱신. 반환 (path, created)."""
    day = today.isoformat()
    lines = [" ".join(str(x).split()) for x in (raw.get("today") or []) if str(x).strip()][:5]
    if not lines:
        raise BrainError("일지 본문이 비었어요")
    win = " ".join(str(raw.get("win") or "").split())[:80]
    tomorrow = " ".join(str(raw.get("tomorrow") or "").split())[:80]
    kakao = " ".join(str(raw.get("kakao") or "").split())[:100] or lines[0][:70]
    title = f"{day} 일지"
    body = f"# {title}\n\n## 오늘\n" + "".join(f"- {l}\n" for l in lines) + "\n## 잘한 것\n" + (f"- {win}\n" if win else "") + "\n## 내일 첫 일\n" + (f"- {tomorrow}\n" if tomorrow else "")
    if mat.get("events"):
        body += "\n## 일정\n" + "".join(f"- {e}\n" for e in mat["events"])
    if mat.get("automation_issues"):
        body += "\n## 자동화\n" + "".join(f"- {b}\n" for b in mat["automation_issues"])
    existing = [n for n in load_notes(vault) if n.type == "journal" and str(n.meta.get("journal_date") or n.created) == day]
    if existing:
        if not force:
            raise BrainError(f"오늘 일지가 이미 있어요: {existing[0].rel} (--force로 다시)")
        n = existing[0]
        meta = dict(n.meta, summary=kakao)
        write_note(n.path, meta, body)
        git_commit(vault, f"brain: journal {day} (rewrite)")
        return n.path, False
    path = create_note(vault, "journal", title, tags=["일지"], body=body, created=day, extra={"journal_date": day, "summary": kakao})
    git_commit(vault, f"brain: journal {day}")
    return path, True


def make_journal(vault, today, widgets=None, agenda=None, force=False):
    """재료 → Claude → 노트. 반환 dict(path, created, kakao, material) 또는 empty=True."""
    mat = journal_material(vault, today, widgets, agenda)
    if mat["empty"]:
        return {"empty": True, "material": mat}
    raw = _run_claude_json(journal_prompt(mat))
    if isinstance(raw, list):
        raw = raw[0] if raw and isinstance(raw[0], dict) else {}
    if not isinstance(raw, dict):
        raise BrainError("일지 응답이 객체가 아니에요")
    path, created = write_journal(vault, today, raw, mat, force=force)
    kakao = " ".join(str(raw.get("kakao") or "").split())[:100]
    return {"empty": False, "path": str(path.relative_to(Path(vault))), "created": created, "kakao": kakao, "material": mat, "lines": raw.get("today")}


def cmd_journal(args):
    """오늘(또는 --date) 일지를 Claude가 5줄로 쓴다. --dry-run이면 재료만."""
    v = require_vault()
    today = parse_date(args.date, "--date") if args.date else date.today()
    widgets = collect_widgets()
    if args.dry_run:
        mat = journal_material(v, today, widgets)
        emit(args, mat, "일지 재료 (dry-run)\n" + json.dumps(mat, ensure_ascii=False, indent=1))
        return EXIT_OK
    r = make_journal(v, today, widgets, force=args.force)
    if r["empty"]:
        emit(args, r, f"{today} 기록이 없어 일지를 쓰지 않았어요 (새 노트·결정·완료한 할 일·일정 메모 중 하나라도 있으면 써요)")
        return EXIT_OK
    sent = None
    if getattr(args, "kakao", False) and r.get("kakao"):
        res = notify(f"[{today.isoformat()[5:]} 일지] {r['kakao']}")
        sent = res["sent"]
    r["sent"] = sent
    emit(args, r, f"일지 {'작성' if r['created'] else '갱신'}: {r['path']}\n" + "\n".join(f"- {l}" for l in (r.get("lines") or [])) + (f"\n\n카톡: {r['kakao']}" if r.get("kakao") else "") + ("\n알림 발송 완료" if sent else ""))
    return EXIT_OK


def _section(n, name, limit=240):
    return " ".join(note_sections(n.body).get(name, "").split())[:limit]


def retro_material(vault, days, today, widgets=None):
    """지난 days일의 회고 재료."""
    since = (today - timedelta(days=days)).isoformat()
    notes = load_notes(vault)
    by_stem, adj = link_graph(notes)
    recent = [n for n in notes if n.created >= since and n.type not in ("event",)]
    new_notes = [{"type": n.type, "title": n.title, "summary": str(n.meta.get("summary") or "")[:160]} for n in recent if n.type not in ("decision", "journal")][:20]
    decisions = [{"title": n.title, "status": n.meta.get("status", "open"), "decision": _section(n, "결정"), "why": _section(n, "이유"), "revisit": str(n.meta.get("revisit") or "")}
                 for n in recent if n.type == "decision"][:8]
    t = today.isoformat()
    soon = (today + timedelta(days=14)).isoformat()
    revisit = [{"title": n.title, "decision": _section(n, "결정", 160), "revisit": str(n.meta.get("revisit"))} for n in notes
               if n.type == "decision" and n.meta.get("status", "open") == "open" and n.meta.get("revisit") and str(n.meta.get("revisit")) <= soon][:6]
    journals = []
    for n in sorted(recent, key=lambda x: x.created):
        if n.type == "journal" and not n.stem.endswith("-weekly"):
            journals.append({"date": n.created, "lines": [l[2:].strip() for l in note_sections(n.body).get("오늘", "").split("\n") if l.startswith("- ")][:5]})
    projects = {}
    for n in recent:
        if n.project:
            projects[n.project] = projects.get(n.project, 0) + 1
    orphans = [n.title for n in recent if not adj.get(n.stem)][:8]
    issues = []
    for w in widgets or []:
        if w.get("state") == "paused" or w.get("kind") != "log":
            continue
        try:
            h = widget_history(w, days=days, today=today)
        except Exception:  # noqa: BLE001 - 회고 재료는 최선 노력
            continue
        if h.get("total_fails"):
            issues.append(f"{w['title']}: {days}일 중 실패 {h['total_fails']}회")
    kpi = office_kpis(widgets or [], today=today, days=days)
    kpi_total = kpi["total"]
    worst_team = None
    teams_with_fails = [(name, tm["fails"]) for name, tm in kpi["teams"].items() if tm.get("fails")]
    if teams_with_fails:
        wname, wfails = max(teams_with_fails, key=lambda kv: kv[1])
        worst_team = {"team": wname, "fails": wfails}
    automation_kpi = {"runs": kpi_total["runs"], "fails": kpi_total["fails"], "rate": kpi_total["rate"], "worst_team": worst_team}
    mat = {"since": since, "until": t, "days": days, "new_notes": new_notes, "decisions": decisions, "revisit_due": revisit, "journals": journals[-7:],
           "projects": sorted(projects.items(), key=lambda kv: -kv[1])[:6], "orphans": orphans, "automation_issues": issues[:6],
           "automation_kpi": automation_kpi,
           "counts": {"notes": len(new_notes), "decisions": len(decisions), "journals": len(journals)}}
    mat["empty"] = not (new_notes or decisions or journals)
    return mat


def retro_prompt(mat):
    return ("너는 사용자의 한 주를 함께 되돌아보는 코치형 비서다. 아래 [재료]만 근거로 JSON 객체 하나로 답한다(설명·마크다운 금지). 형식:\n"
            '{"week": ["이번 주에 한 일" 3~5문장, 각 70자 이내, 1인칭 과거형], '
            '"patterns": ["눈에 띄는 흐름·반복·치우침" 1~2문장, 재료에 근거], '
            '"questions": ["되돌아볼 질문" 정확히 3개, 각 60자 이내. revisit_due·decisions가 있으면 그 결정을 지목해 유지/변경을 묻고, 없으면 patterns에서 뽑는다. 예/아니오로 끝나지 않는 열린 질문], '
            '"next_week": ["다음 주 우선순위" 1~3개, 각 40자 이내, 재료의 미결·revisit에서], '
            '"kakao": "카톡용 한 줄 80자 이내"}\n'
            "규칙: 재료에 없는 사실을 만들지 마라. 칭찬·감탄사·이모지 없이 담담하게. "
            "automation_kpi.fails가 0보다 크면 patterns에 자동화 안정성(실패 건수·worst_team)을 한 문장 언급하고, 0이면 automation_kpi를 언급하지 마라.\n\n[재료]\n"
            + json.dumps(mat, ensure_ascii=False))


def write_retro(vault, today, raw, mat, force=False):
    day = today.isoformat()
    week = [" ".join(str(x).split()) for x in (raw.get("week") or []) if str(x).strip()][:5]
    if not week:
        raise BrainError("회고 본문이 비었어요")
    pats = [" ".join(str(x).split()) for x in (raw.get("patterns") or []) if str(x).strip()][:2]
    qs = [" ".join(str(x).split()) for x in (raw.get("questions") or []) if str(x).strip()][:3]
    nxt = [" ".join(str(x).split()) for x in (raw.get("next_week") or []) if str(x).strip()][:3]
    kakao = " ".join(str(raw.get("kakao") or "").split())[:100] or week[0][:80]
    title = f"{day} 주간 회고"
    c = mat["counts"]
    body = (f"# {title}\n\n{mat['since']} ~ {mat['until']} · 노트 {c['notes']} · 결정 {c['decisions']} · 일지 {c['journals']}\n\n"
            "## 이번 주\n" + "".join(f"- {l}\n" for l in week) +
            ("\n## 눈에 띄는 것\n" + "".join(f"- {l}\n" for l in pats) if pats else "") +
            "\n## 되돌아볼 질문\n" + "".join(f"- [ ] {q}\n" for q in qs) +
            ("\n## 다음 주\n" + "".join(f"- [ ] {l}\n" for l in nxt) if nxt else "") +
            ("\n## 자동화\n" + "".join(f"- {b}\n" for b in mat["automation_issues"]) if mat.get("automation_issues") else "") +
            ("\n## 고아 노트\n" + "".join(f"- {o}\n" for o in mat["orphans"]) if mat.get("orphans") else ""))
    existing = [n for n in load_notes(vault) if n.type == "journal" and n.stem == f"{day}-weekly"]
    if existing:
        if not force:
            raise BrainError(f"오늘 회고가 이미 있어요: {existing[0].rel} (--force로 다시)")
        n = existing[0]
        write_note(n.path, dict(n.meta, summary=kakao), body)
        git_commit(vault, f"brain: retro {day} (rewrite)")
        return n.path, False, qs, kakao
    path = create_note(vault, "journal", title, tags=["회고"], body=body, created=day, extra={"journal_kind": "weekly", "journal_date": day, "since": mat["since"], "summary": kakao})
    git_commit(vault, f"brain: retro {day}")
    return path, True, qs, kakao


def cmd_retro(args):
    v = require_vault()
    today = date.today()
    widgets = collect_widgets()
    mat = retro_material(v, args.days, today, widgets)
    if args.dry_run:
        emit(args, mat, "회고 재료 (dry-run)\n" + json.dumps(mat, ensure_ascii=False, indent=1))
        return EXIT_OK
    if mat["empty"]:
        emit(args, {"empty": True}, f"지난 {args.days}일에 기록이 없어 회고를 쓰지 않았어요")
        return EXIT_OK
    raw = _run_claude_json(retro_prompt(mat))
    if isinstance(raw, list):
        raw = raw[0] if raw and isinstance(raw[0], dict) else {}
    if not isinstance(raw, dict):
        raise BrainError("회고 응답이 객체가 아니에요")
    path, created, qs, kakao = write_retro(v, today, raw, mat, force=args.force)
    rel = str(path.relative_to(Path(v)))
    sent = None
    if args.kakao and kakao:
        res = notify(f"[주간 회고] {kakao}" + (" / 질문: " + qs[0] if qs else ""))
        sent = res["sent"]
    emit(args, {"path": rel, "created": created, "questions": qs, "kakao": kakao, "sent": sent},
         f"주간 회고 {'작성' if created else '갱신'}: {rel}\n" + "\n".join(f"- {l}" for l in (raw.get("week") or [])) + "\n\n되돌아볼 질문\n" + "\n".join(f"- {q}" for q in qs) + (f"\n\n카톡: {kakao}" if kakao else "") + ("\n알림 발송 완료" if sent else ""))
    return EXIT_OK


def semantic_link_suggestions(vault, r, limit=25):
    """고아·최근 노트를 후보로, 카탈로그(stem|제목|요약)와 견줘 Claude가 내용상 관련 쌍을 고른다. 적용은 하지 않는다."""
    notes = load_notes(vault)
    by_stem, adj = link_graph(notes)
    stems = {n.stem: n for n in notes if n.type not in ("event", "journal")}
    cand_paths = [o["path"] for o in r.get("orphans", [])] + [n["path"] for n in r.get("new_notes", [])]
    seen, cands = set(), []
    for p in cand_paths:
        st = Path(p).stem
        if st in stems and st not in seen:
            seen.add(st)
            cands.append(stems[st])
        if len(cands) >= limit:
            break
    if not cands or len(stems) < 3:
        return []
    catalog = [f"{n.stem} | {n.title[:40]} | {str(n.meta.get('summary') or '')[:90]}" for n in stems.values()]
    items = [{"stem": n.stem, "title": n.title, "summary": str(n.meta.get("summary") or "")[:200], "body": " ".join(n.body.split())[:400], "linked": sorted(adj.get(n.stem, set()))[:8]} for n in cands]
    prompt = ("너는 개인 지식 창고의 사서다. [후보] 노트 각각에 대해 [카탈로그]에서 내용상 정말 관련 있는 노트(같은 주제·같은 결정의 근거·같은 도구)를 최대 2개 고른다. "
              "이미 linked에 있는 것, 자기 자신, 단순히 같은 사람이 쓴 것·시기만 비슷한 것은 제외. 확신이 없으면 비운다. "
              'JSON 배열로만 답한다: [{"a": 후보 stem, "b": 카탈로그 stem, "reason": "20자 이내 이유"}]\n\n[카탈로그 stem | 제목 | 요약]\n'
              + "\n".join(catalog) + "\n\n[후보]\n" + json.dumps(items, ensure_ascii=False))
    raw = _run_claude_json(prompt)
    out, dup = [], set()
    for it in raw if isinstance(raw, list) else []:
        if not isinstance(it, dict):
            continue
        a, b = str(it.get("a") or ""), str(it.get("b") or "")
        if a not in stems or b not in stems or a == b or b in adj.get(a, set()) or frozenset((a, b)) in dup:
            continue
        dup.add(frozenset((a, b)))
        out.append({"a": stems[a].rel, "b": stems[b].rel, "reason": "의미: " + " ".join(str(it.get("reason") or "").split())[:40]})
    return out[:10]


SUGGEST_DAYS = 7
SUGGEST_MAX_ITEMS = 8


def suggestions_path():
    return agenda_mod.cache_dir() / "suggestions.json"


def load_suggestions():
    p = suggestions_path()
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_suggestions(d):
    p = suggestions_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def pending_suggestions(sugg=None, today=None):
    """지난 일정 것은 걸러낸 대기 중 제안 {key: sug}."""
    sugg = load_suggestions() if sugg is None else sugg
    today = (today or date.today()).isoformat()
    return {k: s for k, s in sugg.items() if s.get("status") == "pending" and str(s.get("date") or "")[:10] >= today}


def prepare_prompt(e, note, related):
    have = [c["text"] for c in ((note or {}).get("checklist") or [])]
    steps = [f"{s['time']} {s['text']}" for s in ((note or {}).get("steps") or [])]
    when = e["start"][:10] + (" 종일" if e.get("all_day") else " " + e["start"][11:16] + "–" + str(e.get("end") or "")[11:16])
    trip = e.get("trip")
    ctx = {"title": e["title"], "when": when, "location": e.get("location") or "", "description": (e.get("description") or "")[:600],
           "attendees": (e.get("attendees") or [])[:5], "already_prep": have, "already_steps": steps,
           "weather": e.get("weather"),
           "related_notes": [{"title": r["title"], "snippet": (r.get("snippet") or "")[:200]} for r in (related or [])[:3]]}
    if trip:
        ctx["trip_days"] = trip.get("days")
        ctx["trip_dates"] = trip.get("dates")
        ctx["trip_weather"] = trip.get("weather") or []
        return ("너는 꼼꼼한 개인 비서다. 아래 여행 일정 하나를 보고 JSON 객체 하나로만 답한다(설명·마크다운 금지). 형식:\n"
                '{"prep": ["예약: ..." 또는 "짐: ..." 또는 "서류: ..." 접두어로 시작하는 준비 항목 6~10개, '
                '각 30자 이내, already_prep와 겹치지 않게, trip_days(여행 일수)와 trip_weather(날짜별 날씨)를 반영], '
                '"steps": ["HH:MM 내용" 0~4개, 출발일(when) 당일의 이동·출발 동선만. 시각을 합리적으로 추정할 수 없으면 빈 배열], '
                '"memo": "한 줄 조언 60자 이내(없으면 빈 문자열)"}\n'
                "규칙: 일정·설명·관련 노트에 없는 고유 사실(예약번호·전화번호 등)을 만들지 마라. "
                "짐(짐:) 항목은 trip_days 일수와 trip_weather의 날짜별 날씨(우산·추위·더위)를 참고해 실제로 챙길 것만 넣는다. "
                "예약(예약:) 항목은 숙소·렌터카·항공 등 이미 있는 것(already_*)과 겹치지 않게, 서류(서류:) 항목은 여권·신분증처럼 흔한 것만. "
                "공항은 출발 2시간 전 도착 기준으로 동선을 잡는다. 이미 있는 항목(already_*)은 다시 내지 마라.\n\n[여행]\n"
                + json.dumps(ctx, ensure_ascii=False))
    return ("너는 꼼꼼한 개인 비서다. 아래 일정 하나를 보고 JSON 객체 하나로만 답한다(설명·마크다운 금지). 형식:\n"
            '{"prep": ["준비 항목" 3~6개, 각 20자 이내 명사구, already_prep와 겹치지 않게], '
            '"steps": ["HH:MM 내용" 0~4개, 그 날의 이동·출발 동선. 시각을 합리적으로 추정할 수 없으면 빈 배열], '
            '"memo": "한 줄 조언 60자 이내(없으면 빈 문자열)"}\n'
            "규칙: 일정·설명·관련 노트에 없는 고유 사실(예약번호·전화번호 등)을 만들지 마라. 항공·기차·병원·면접·회의 같은 일반 상식 준비물은 허용. "
            "공항은 출발 2시간 전 도착 기준으로 동선을 잡는다. 이미 있는 항목(already_*)은 다시 내지 마라. "
            "날씨가 궂으면(weather.umbrella=true) 우산을, cold/hot이면 겉옷/무더위 대비를 준비 항목에 넣어도 된다.\n\n[일정]\n"
            + json.dumps(ctx, ensure_ascii=False))


def _norm_suggestion(raw, e, trip=False):
    items = []
    prep_cap = 10 if trip else 6
    for t in (raw.get("prep") or [])[:prep_cap]:
        t = " ".join(str(t).split())
        if t:
            items.append({"kind": "prep", "text": t[:80]})
    for t in (raw.get("steps") or [])[:4]:
        t = " ".join(str(t).split())
        if re.match(r"^\d{1,2}:\d{2}\s+\S", t):
            items.append({"kind": "step", "text": t[:120]})
    memo = " ".join(str(raw.get("memo") or "").split())
    if memo:
        items.append({"kind": "memo", "text": memo[:160]})
    cap = max(SUGGEST_MAX_ITEMS, prep_cap) if trip else SUGGEST_MAX_ITEMS
    return items[:cap]


def suggest_targets(ag, sugg, days, force=False):
    """제안할 일정: days일 안, 준비 항목 3개 미만, 아직 제안한 적 없는 것(--force면 무시)."""
    out = []
    for e in (ag.get("today") or []) + (ag.get("upcoming") or []):
        if (e.get("days_left") or 0) > days:
            continue
        n = e.get("note")
        if n and (n.get("total") or 0) >= 3 and (n.get("steps") or []):
            continue
        if e.get("key") in sugg and not force:
            continue
        out.append(e)
    return out


def cmd_prepare(args):
    """다가오는 일정에 Claude가 준비 체크리스트·동선 초안을 제안해 보류함에 넣는다(노트에는 쓰지 않음)."""
    try:
        v = vault_path()
        if not vault_exists(v):
            v = None
    except BrainError:
        v = None
    ag = dict(collect_agenda_safe(args.days))
    if v:
        attach_event_notes(v, ag)
    sugg = load_suggestions()
    targets = suggest_targets(ag, sugg, args.days, force=args.force)
    if args.key:
        targets = [e for e in targets if e.get("key") == args.key] or [e for e in (ag.get("today") or []) + (ag.get("upcoming") or []) if e.get("key") == args.key]
    if not targets:
        emit(args, {"done": 0, "pending": len(pending_suggestions(sugg))}, f"제안할 일정이 없어요 (대기 중 {len(pending_suggestions(sugg))}건)")
        return EXIT_OK
    done, failed = [], []
    for e in targets:
        if args.dry_run:
            done.append({"key": e["key"], "items": []})
            continue
        try:
            raw = _run_claude_json(prepare_prompt(e, e.get("note"), e.get("related")))
            if isinstance(raw, list):
                raw = raw[0] if raw and isinstance(raw[0], dict) else {}
            items = _norm_suggestion(raw if isinstance(raw, dict) else {}, e, trip=bool(e.get("trip")))
        except BrainError as err:
            log(f"경고: {e['title']} 제안 실패: {err}")
            failed.append(e["key"])
            continue
        if not items:
            failed.append(e["key"])
            continue
        sugg[e["key"]] = {"key": e["key"], "title": e["title"], "date": e["start"][:10], "end": (e.get("end") or "")[:10] if e.get("all_day") else "",
                          "location": e.get("location") or "", "items": items, "status": "pending", "created": datetime.now().isoformat(timespec="minutes")}
        done.append({"key": e["key"], "items": items})
    if not args.dry_run:
        save_suggestions(sugg)
    lines = []
    for d in done:
        lines.append(f"- {d['key'].split('|', 1)[1]} ({d['key'][:10]}): " + (", ".join(i['text'] for i in d['items'][:4]) if d['items'] else "(dry)"))
    if failed:
        lines.append("실패 " + ", ".join(k.split('|', 1)[1] for k in failed[:4]))
    emit(args, {"done": len(done), "failed": failed, "suggestions": done},
         f"준비 제안 {len(done)}건" + (" (dry-run)" if args.dry_run else " — 보드의 일정 카드에서 채택/무시") + "\n" + "\n".join(lines))
    return EXIT_OK if not failed else EXIT_INPUT


def suggestion_action(vault, body, today=None):
    """{action: accept|dismiss, key, indexes?: [i…]} — accept는 고른 항목만 일정 노트에 쓴다."""
    action = body.get("action")
    key = str(body.get("key") or "")
    sugg = load_suggestions()
    s = sugg.get(key)
    if not s:
        raise BrainError("그 일정의 제안이 없어요(이미 처리됐을 수 있어요)")
    if action == "dismiss":
        s["status"] = "dismissed"
        s["decided"] = datetime.now().isoformat(timespec="minutes")
        save_suggestions(sugg)
        return {"ok": True, "status": "dismissed", "key": key}
    if action != "accept":
        raise BrainError("action은 accept 또는 dismiss")
    if not vault:
        raise BrainError("볼트가 없어요")
    idx = body.get("indexes")
    items = s["items"] if idx is None else [s["items"][i] for i in idx if isinstance(i, int) and 0 <= i < len(s["items"])]
    if not items:
        raise BrainError("채택할 항목을 하나 이상 골라 주세요")
    note = None
    for it in items:
        act = {"prep": "todo", "step": "step", "memo": "memo"}[it["kind"]]
        res = event_note_action(vault, {"action": act, "key": key, "title": s.get("title"), "date": s.get("date"),
                                        "end": s.get("end") or None, "location": s.get("location") or None, "text": it["text"]})
        note = res.get("note")
    s["status"] = "accepted"
    s["accepted"] = [it["text"] for it in items]
    s["decided"] = datetime.now().isoformat(timespec="minutes")
    save_suggestions(sugg)
    return {"ok": True, "status": "accepted", "key": key, "count": len(items), "note": note}


def enrich_prompt(notes, catalog):
    items = []
    for n in notes:
        items.append({"stem": n.stem, "type": n.type, "title": n.title, "tags": n.tags, "body": " ".join(n.body.split())[:ENRICH_BODY_CHARS]})
    return ("너는 개인 지식 창고(마크다운 노트) 사서다. 아래 [노트들] 각각에 대해 JSON 배열로만 답한다(설명·마크다운 금지). 항목 형식:\n"
            '{"stem": 그대로, "title": 40자 이내 한국어 명사구(원래 뜻 유지, 이모지·체크표시·따옴표·"사용자가 …" 같은 서술 제거, 고유명사·명령 이름은 그대로), '
            '"summary": 2~3문장 220자 이내 평서문(무엇을 왜, 어떻게 적용하는지), "tags": 3~5개 한국어 명사(기존 태그 중 쓸 만한 것은 유지, 영문 도구명 허용), '
            '"links": [카탈로그] 중 내용상 관련 있는 stem 최대 3개(자기 자신 제외, 억지로 채우지 말 것)}\n'
            "노트에 없는 사실을 만들지 마라. 요약은 노트 원문 근거로만.\n\n[카탈로그 stem | 제목]\n"
            + "\n".join(f"{s} | {t}" for s, t in catalog) + "\n\n[노트들]\n" + json.dumps(items, ensure_ascii=False))


def apply_enrichment(vault, note, item, stems, force=False):
    """한 노트에 Claude 결과 적용. 반환: 바뀐 필드 목록."""
    changed = []
    meta = dict(note.meta)
    title = " ".join(str(item.get("title") or "").split())
    if title and title != note.title and len(title) <= 60:
        if not meta.get("original_title"):
            meta["original_title"] = note.title
        meta["title"] = title
        changed.append("title")
    summary = " ".join(str(item.get("summary") or "").split())
    if summary and (force or not meta.get("summary")):
        meta["summary"] = summary[:300]
        changed.append("summary")
    tags = [str(t).strip().lstrip("#") for t in (item.get("tags") or []) if str(t).strip()]
    if tags:
        merged = [t for t in dict.fromkeys(note.tags + tags) if t != "claude-memory"][:7]  # 가져온 표식 태그는 정제 후 제거
        if merged != note.tags:
            meta["tags"] = merged
            changed.append("tags")
    links = [str(x) for x in (item.get("links") or []) if str(x) in stems and str(x) != note.stem]
    if links:
        cur = as_list(meta.get("links"))
        new = [wikilink(x) for x in links if wikilink(x) not in cur]
        if new:
            meta["links"] = cur + new[:3]
            changed.append("links")
    if changed:
        write_note(note.path, meta, note.body)
    return changed


def cmd_enrich(args):
    """Claude로 노트 정제: 제목·요약·태그·링크. 기본은 가져온(imported_from) 노트 중 요약이 없는 것."""
    v = require_vault()
    notes = load_notes(v)
    stems = {n.stem for n in notes}
    if args.paths:
        targets = [n for n in notes if n.rel in args.paths or n.stem in args.paths]
    elif args.all:
        targets = [n for n in notes if n.type != "event"]
    else:
        targets = [n for n in notes if n.meta.get("imported_from")]
    if not args.force:
        targets = [n for n in targets if not n.meta.get("summary")]
    if args.limit:
        targets = targets[:args.limit]
    if not targets:
        emit(args, {"done": 0}, "정제할 노트가 없어요(이미 요약이 있거나 대상이 없음). --force 또는 --all")
        return EXIT_OK
    catalog = [(n.stem, n.title[:40]) for n in notes]
    report, failed = [], []
    for i in range(0, len(targets), ENRICH_BATCH):
        batch = targets[i:i + ENRICH_BATCH]
        if args.dry_run:
            report.extend({"stem": n.stem, "changed": ["(dry)"]} for n in batch)
            continue
        try:
            items = _run_claude_json(enrich_prompt(batch, catalog))
        except BrainError as e:
            log(f"경고: 배치 {i // ENRICH_BATCH + 1} 실패: {e}")
            failed.extend(n.stem for n in batch)
            continue
        by = {str(it.get("stem")): it for it in items if isinstance(it, dict)}
        for n in batch:
            it = by.get(n.stem)
            if not it:
                failed.append(n.stem)
                continue
            ch = apply_enrichment(v, n, it, stems, force=args.force)
            report.append({"stem": n.stem, "changed": ch, "title": it.get("title")})
        log(f"정제 {min(i + ENRICH_BATCH, len(targets))}/{len(targets)}")
    if not args.dry_run and report:
        build_index(v)
        git_commit(v, f"brain: enrich {len(report)} notes")
    lines = [f"- {r['stem']}: {', '.join(r['changed']) or '변경 없음'}" + (f" → {r['title']}" if r.get('title') and 'title' in r['changed'] else "") for r in report]
    if failed:
        lines.append(f"실패 {len(failed)}: " + ", ".join(failed[:6]))
    emit(args, {"done": len(report), "failed": failed, "report": report}, f"정제 {len(report)}개" + (" (dry-run)" if args.dry_run else "") + "\n" + "\n".join(lines))
    return EXIT_OK if not failed else EXIT_INPUT


def doctor_report(today=None):
    """설치·연결 점검. 각 항목 {name, ok, detail, fix}. 외부 호출 없음(캘린더는 캐시·설정만 본다)."""
    import shutil
    items = []
    def add(name, ok, detail, fix=None):
        items.append({"name": name, "ok": bool(ok), "detail": detail, "fix": fix})
    cfg = load_config()
    try:
        v = vault_path()
        ok = vault_exists(v)
        add("볼트", ok, f"{v}" + ("" if ok else " (없음)"), None if ok else "brain-setup 또는 `brain.py init`")
        if ok:
            notes = load_notes(v)
            add("노트", True, f"{len(notes)}개 · 요약 있는 노트 {sum(1 for n in notes if n.meta.get('summary'))}개", None if len(notes) else "「기억해둬」로 첫 노트를 남겨 보세요")
            imported = [n for n in notes if n.meta.get("imported_from") and not n.meta.get("summary")]
            if imported:
                add("정제 대기", False, f"가져온 노트 {len(imported)}개에 요약이 없음", "`brain.py enrich`")
            try:
                li = lint_vault(v)
                add("볼트 점검", not li["issues"], f"이슈 {len(li['issues'])}개" if li["issues"] else "이슈 없음",
                    None if not li["issues"] else "`brain.py lint --fix`")
            except Exception as e:  # lint가 죽어도 doctor 전체는 죽지 않아야 함
                log(f"경고: 볼트 점검 실패: {e}")
    except BrainError as e:
        add("볼트", False, str(e), "brain-setup")
    py = sys.version_info
    add("Python", py >= (3, 9), f"{py.major}.{py.minor}.{py.micro}", None if py >= (3, 9) else "3.9 이상 필요")
    ask_cmd = os.environ.get("SECOND_BRAIN_ASK_CMD") or cfg.get("ask_cmd") or "claude -p --output-format text"
    exe = ask_cmd.split()[0]
    found = shutil.which(exe) or (os.path.isfile(os.path.expanduser(exe)) and exe)
    add("Claude CLI", bool(found), f"{exe} → {found or '못 찾음'}", None if found else "claude 설치 후 PATH에 넣거나 `config set ask_cmd <경로> -p --output-format text`")
    srcs = agenda_mod.normalize_sources(cfg)
    if srcs:
        add("캘린더", True, ", ".join(f"{s['name']}({s['kind']})" for s in srcs), None)
        for s in srcs:
            if s["kind"] == "ics" and s.get("url_file") and not os.path.isfile(os.path.expanduser(str(s["url_file"]))):
                add("캘린더 주소 파일", False, f"{s['url_file']} 없음", "구글 캘린더 비공개 ICS 주소를 그 파일 첫 줄에 저장")
    else:
        add("캘린더", False, "연결된 캘린더 없음", "`calendar add ics 구글 --url-file ~/.config/second-brain/google.ics.url`")
    helper = kakao_helper_path(cfg)
    add("카톡 헬퍼", bool(helper), str(helper) if helper else "없음(알림은 로그에만)", None if helper else "`config set kakao_cmd <나에게 보내기 스크립트>`")
    wp = widgets_config_path()
    if wp.is_file():
        try:
            ws = collect_widgets()
            bad = [w["title"] for w in ws if w.get("state") != "paused" and w.get("status") in ("fail", "stale", "missing")]
            add("자동화 위젯", not bad, f"{len(ws)}개" + (f" · 문제 {len(bad)}: " + ", ".join(bad[:3]) if bad else " · 모두 정상"), "보드 「자동화」에서 확인" if bad else None)
        except BrainError as e:
            add("자동화 위젯", False, str(e), "widgets.json 문법 확인")
    else:
        add("자동화 위젯", False, "widgets.json 없음", "`config init-widgets`")
    if sys.platform == "darwin":
        st = agents_status()
        loaded = [a["name"] for a in st if a["state"] == "loaded"]
        missing = [a["name"] for a in st if a["installed"] and a["state"] != "loaded"]
        add("알림 에이전트", not missing, f"실행 중 {len(loaded)}/{len(st)}: " + (", ".join(loaded) or "없음") + (f" · 설치됐지만 안 뜸: {', '.join(missing)}" if missing else ""),
            "`agents install --force`" if missing else (None if loaded else "`agents install`"))
    dash_port = _serve_installed_port(launch_agents_dir() / f"{AGENT_SPECS['serve']['label']}.plist")
    dash_ok = _probe_serve_port(dash_port)
    add("대시보드", dash_ok, f"127.0.0.1:{dash_port} " + ("응답함" if dash_ok else "응답 없음"),
        None if dash_ok else "`agents install serve` 또는 `brain.py serve`")
    cache = agenda_mod.cache_dir()
    add("캐시 폴더", os.access(cache, os.W_OK) if cache.exists() else True, str(cache), None)
    return {"date": (today or date.today()).isoformat(), "items": items, "ok": all(i["ok"] for i in items)}


def cmd_doctor(args):
    r = doctor_report()
    lines = [("✓ " if i["ok"] else "✗ ") + f"{i['name']}: {i['detail']}" + (f"  → {i['fix']}" if i.get("fix") and not i["ok"] else "") for i in r["items"]]
    emit(args, r, "\n".join(lines) + ("\n\n모두 정상이에요." if r["ok"] else "\n\n✗ 항목의 → 안내대로 고치면 돼요."))
    return EXIT_OK if r["ok"] else EXIT_INPUT


def cmd_backup(args):
    v = require_vault()
    res = backup_vault(v, args.dest, args.keep)
    emit(args, res, f"백업 완료: {res['path']} ({res['files']}개 파일, {res['bytes'] // 1024}KB)" + (f" · 오래된 백업 {len(res['removed'])}개 정리" if res["removed"] else ""))
    return EXIT_OK


def cmd_restore(args):
    import zipfile
    v = require_vault()
    dest = agenda_mod.cache_dir() / "backups"
    if args.list:
        zips = sorted(dest.glob("brain-*.zip"))
        items = []
        for p in zips:
            st = p.stat()
            try:
                with zipfile.ZipFile(p) as z:
                    n = len([i for i in z.infolist() if not i.filename.endswith("/")])
            except zipfile.BadZipFile:
                n = 0
            items.append({"name": p.name, "bytes": st.st_size, "files": n,
                          "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")})
        lines = [f"- {i['name']} ({i['files']}개 파일, {i['bytes'] // 1024}KB, {i['mtime']})" for i in items]
        emit(args, {"backups": items}, "\n".join(lines) or f"백업이 없어요: {dest}")
        return EXIT_OK

    if args.zip:
        zpath = Path(args.zip).expanduser()
        if not zpath.is_file():
            cand = dest / args.zip
            if cand.is_file():
                zpath = cand
    else:
        zips = sorted(dest.glob("brain-*.zip"))
        if not zips:
            raise BrainError(f"백업이 없어요: {dest} — 먼저 `brain.py backup`을 실행하세요.")
        zpath = zips[-1]
    if not zpath.is_file():
        raise BrainError(f"백업 zip을 찾을 수 없습니다: {args.zip}")

    res = restore_vault(v, zpath, dry=args.dry_run, keep_current=not args.replace,
                        safety_backup=not args.no_safety_backup)
    lines = [f"백업: {Path(res['zip']).name}" + (" (dry-run, 실제로 바뀌지 않음)" if res["dry_run"] else "")]
    lines.append(f"덮어쓰기 {len(res['overwritten'])}개 · 새로 생성 {len(res['created'])}개 · 보존 {len(res['preserved'])}개"
                + (f" · 삭제(--replace) {len(res['removed'])}개" if res["removed"] else ""))
    if res["safety_backup"]:
        lines.append(f"복구 전 안전 백업: {res['safety_backup']}")
    emit(args, res, "\n".join(lines))
    return EXIT_OK


def cmd_agents(args):
    names = [n for n in (args.names or DEFAULT_AGENT_NAMES) if n in AGENT_SPECS]
    if not names:
        raise BrainError(f"에이전트 이름은 {', '.join(AGENT_SPECS)} 중에서")
    if sys.platform != "darwin" and args.action in ("install", "remove") and not args.dry_run:
        raise BrainError("launchd 에이전트는 macOS에서만 설치할 수 있어요(다른 OS는 cron에 brief/remind를 직접 등록)")
    if args.action == "install":
        res = agents_install(names, dry=args.dry_run, force=args.force, port=getattr(args, "port", None))
        lines = [f"- {r['name']}: {r['action']}" + (f" → {r['plist']}" if r["action"] in ("install", "dry") else "") + (f" (bootstrap {r['bootstrap']})" if r.get("bootstrap") else "") for r in res]
        if not args.dry_run and any(r["action"] == "install" for r in res):
            lines.append("카톡을 쓰려면 `config set kakao_cmd <나에게 보내기 헬퍼 경로>`. 없으면 로그에만 남아요.")
        emit(args, res, "\n".join(lines))
        return EXIT_OK
    if args.action == "status":
        res = agents_status(serve_port=getattr(args, "port", None))
        emit(args, res, "\n".join(
            f"- {r['name']} ({r['title']}): {'설치됨' if r['installed'] else '미설치'} · {r['state']}"
            + (f" · {'응답함' if r.get('reachable') else '응답 없음'}(포트 {r.get('port')})" if "reachable" in r else "")
            for r in res))
        return EXIT_OK
    res = agents_remove(names)
    emit(args, res, "\n".join(f"- {r['name']}: 제거" for r in res))
    return EXIT_OK


def cmd_calendar(args):
    # 위치 인자 해석: add <kind> <name> / remove <name>
    args.kind, args.name = (args.arg1, args.arg2) if args.action == "add" else (None, args.arg1)
    if args.action == "add" and args.kind not in ("ics", "eventkit"):
        raise BrainError("사용법: calendar add <ics|eventkit> <이름> [--url-file F | --path P | --calendars a,b]")
    cfg = load_config()
    cal = cfg.get("calendar") if isinstance(cfg.get("calendar"), dict) else {}
    srcs = [x for x in (cal.get("sources") or []) if isinstance(x, dict)]
    if args.action == "list":
        data = agenda_mod.normalize_sources(cfg)
        emit(args, data, "\n".join(f"- {x['name']} ({x['kind']})" + (" [기본]" if x.get("_default") else "") +
                                  (f" {x.get('url_file') or x.get('path') or ''}" if x["kind"] == "ics" else
                                   (f" 캘린더: {', '.join(x['calendars'])}" if x.get("calendars") else "")) for x in data) or "소스 없음")
        return EXIT_OK
    if args.action == "add":
        if not args.kind or not args.name:
            raise BrainError("사용법: calendar add <ics|eventkit> <이름> [--url-file F | --path P | --calendars a,b]")
        src = {"kind": args.kind, "name": args.name}
        if args.kind == "ics":
            if args.url_file:
                src["url_file"] = args.url_file
            elif args.path:
                src["path"] = args.path
            else:
                raise BrainError("ics 소스는 --url-file(비공개 ICS 주소가 든 파일) 또는 --path(.ics 파일)가 필요합니다")
            for k in ("url_file", "path"):
                if k in src:
                    p = Path(os.path.expanduser(src[k]))
                    if not p.is_file():
                        raise BrainError(f"파일이 없어요: {p}")
        elif args.calendars:
            src["calendars"] = [c.strip() for c in args.calendars.split(",") if c.strip()]
        srcs = [x for x in srcs if x.get("name") != args.name] + [src]
        cfg["calendar"] = dict(cal, sources=srcs)
        save_config(cfg)
        emit(args, src, f"일정 소스 추가: {args.name} ({args.kind}). `brain.py agenda`로 확인해 보세요.")
        return EXIT_OK
    if args.action == "remove":
        if not args.name:
            raise BrainError("사용법: calendar remove <이름>")
        new = [x for x in srcs if x.get("name") != args.name]
        if len(new) == len(srcs):
            raise BrainError(f"그런 소스가 없어요: {args.name}")
        cfg["calendar"] = dict(cal, sources=new)
        save_config(cfg)
        emit(args, {"removed": args.name}, f"일정 소스 제거: {args.name}")
        return EXIT_OK
    if args.action == "test":
        ag = collect_agenda_safe(args.days)
        lines = []
        for src in ag["sources"]:
            lines.append(f"{'OK ' if src['status'] == 'ok' else '!! '}{src['name']} ({src['kind']}): {src['status']}, 일정 {src['count']}개"
                         + (f" — {src['error']}" if src.get("error") else "")
                         + (f"\n     캘린더: {', '.join(src['calendars'])}" if src.get("calendars") else "")
                         + (f"\n     {src['hint']}" if src.get("hint") else ""))
        emit(args, ag["sources"], "\n".join(lines))
        return EXIT_OK if all(x["status"] == "ok" for x in ag["sources"]) else EXIT_INPUT
    raise BrainError("action은 list · add · remove · test 중 하나")


def cmd_notify(args):
    """알림 채널 시험 발송: 카톡 헬퍼 → 없으면 macOS 알림 센터. 설정 확인용."""
    if args.action != "test":
        raise BrainError("action은 test만 지원합니다")
    text = args.message or "세컨드브레인 알림 테스트예요."
    res = notify(text)
    if not res["channels"]:
        lines = ["알림 채널이 없어요(카톡 헬퍼 또는 macOS 알림 센터). ~/.local/k-skill-cron/notify_kakao.py를 두거나 macOS에서 실행하세요."]
    else:
        lines = [f"{'OK ' if c['ok'] else '!! '}{c['name']}" + (f": {c['error']}" if c.get("error") else "") for c in res["channels"]]
    emit(args, res, f"알림 테스트: {text}\n" + "\n".join(lines))
    return EXIT_OK if res["sent"] else EXIT_INPUT


def cmd_reminders(args):
    """맥 미리알림(읽기 전용): on [--lists A,B] · off · list · test. 기본 꺼짐(EventKit 캘린더와 같은 이유)."""
    cfg = load_config()
    rcfg = dict(cfg.get("reminders")) if isinstance(cfg.get("reminders"), dict) else {"enabled": False, "lists": []}
    if args.action == "on":
        rcfg["enabled"] = True
        if args.lists:
            rcfg["lists"] = split_csv(args.lists)
        cfg["reminders"] = rcfg
        save_config(cfg)
        emit(args, rcfg, "미리알림 연결 켬" + (f" (목록: {', '.join(rcfg['lists'])})" if rcfg.get("lists") else " (모든 목록)")
             + ". 처음 실행하면 권한 창이 떠요. `brain.py reminders test`로 확인해 보세요.")
        return EXIT_OK
    if args.action == "off":
        rcfg["enabled"] = False
        cfg["reminders"] = rcfg
        save_config(cfg)
        emit(args, rcfg, "미리알림 연결 끔")
        return EXIT_OK
    if args.action in ("list", "test"):
        items = reminders_mod.fetch_reminders(lists=rcfg.get("lists") or None, force=True)
        err = reminders_mod.LAST_ERROR
        if args.action == "test":
            emit(args, {"count": len(items), "error": err},
                 f"미리알림 {len(items)}개 확인" if not err else f"미리알림 조회 실패: {err}")
        else:
            human = "\n".join(f"- [{x['list']}] {x['title']}" + (f" (마감 {x['due']})" if x.get("due") else "") for x in items)
            emit(args, {"items": items, "error": err}, human or (f"미리알림 조회 실패: {err}" if err else "미리알림이 없어요."))
        return EXIT_OK if not err else EXIT_INPUT
    raise BrainError("action은 on · off · list · test 중 하나")


def cmd_config(args):
    if args.action == "init-widgets":
        p, created = init_widgets_config()
        emit(args, {"path": str(p), "created": created},
             f"예시 위젯 설정 생성: {p}" if created else f"이미 있어요(덮어쓰지 않음): {p}")
        return EXIT_OK
    cfg = load_config()
    if args.action == "get":
        require_vault()  # 계약: 볼트 없으면 config get도 종료 코드 3
        if args.key:
            if args.key not in cfg:
                raise BrainError(f"알 수 없는 설정 키: {args.key}")
            emit(args, {args.key: cfg[args.key]}, str(cfg[args.key]))
        else:
            emit(args, cfg, "\n".join(f"{k} = {v}" for k, v in cfg.items()))
        return EXIT_OK
    if not args.key or args.value is None:
        raise BrainError("사용법: config set KEY VALUE")
    val = _coerce(args.value)
    if args.key == "vault":
        val = str(ensure_in_home(args.value, "볼트 경로"))
    elif args.key == "index_head" and (not isinstance(val, int) or val < 0):
        raise BrainError("index_head는 0 이상의 정수여야 합니다.")
    elif args.key == "git_autocommit" and not isinstance(val, bool):
        raise BrainError("git_autocommit은 true/false여야 합니다.")
    cfg[args.key] = val
    save_config(cfg)
    emit(args, {args.key: val}, f"설정 저장: {args.key} = {val}")
    return EXIT_OK


class KoreanParser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        log(f"오류: {message}")
        sys.exit(EXIT_INPUT)


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="JSON으로 출력")
    p = KoreanParser(
        prog="brain.py",
        description="세컨드브레인 — 마크다운 개인 지식 창고 CLI. "
                    "볼트 경로는 ~/.config/second-brain/config.json의 vault(기본 ~/brain).",
        epilog="종료 코드: 0 성공 · 2 입력 오류 · 3 볼트 없음. 모든 서브커맨드는 --json을 지원합니다.",
        add_help=False,
    )
    p.add_argument("-h", "--help", action="help", help="도움말을 보여주고 종료")
    p.add_argument("--version", action="version", version=f"second-brain {VERSION}",
                   help="버전 출력")
    sub = p.add_subparsers(dest="cmd", metavar="<명령>", parser_class=KoreanParser)

    def add(name, help_, func):
        sp = sub.add_parser(name, help=help_, description=help_, parents=[common], add_help=False)
        sp.add_argument("-h", "--help", action="help", help="도움말을 보여주고 종료")
        sp.set_defaults(func=func)
        return sp

    s = add("init", "볼트 생성(폴더·BRAIN.md·inbox.md·샘플 노트)", cmd_init)
    s.add_argument("--vault", help="볼트 경로(홈 아래). 지정 시 설정에 저장")
    s.add_argument("--git", action="store_true", help="볼트에 git init")

    s = add("new", "새 노트 생성 후 경로 출력", cmd_new)
    s.add_argument("--type", required=True, choices=ALL_TYPES, help="노트 타입")
    s.add_argument("--title", required=True, help="제목")
    s.add_argument("--tags", help="쉼표 구분 태그")
    s.add_argument("--project", help="프로젝트 이름")
    s.add_argument("--source", help="원문 URL")
    s.add_argument("--people", help="쉼표 구분 사람")
    s.add_argument("--revisit", help="되돌아볼 날짜 YYYY-MM-DD")
    s.add_argument("--status", choices=DECISION_STATUSES, help="결정 상태(decision 기본 open)")
    s.add_argument("--created", help="생성일 YYYY-MM-DD(기본 오늘)")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--body-file", help="본문 파일('-'이면 stdin)")
    g.add_argument("--body", help="본문 텍스트")

    s = add("capture", "빠른 캡처: 텍스트 한 줄 → 노트 한 장(경로 출력)", cmd_capture)
    s.add_argument("text", help="캡처할 텍스트(1~4000자)")
    s.add_argument("--type", choices=("note", "idea", "source"), help="타입(기본 note, URL이면 source)")
    s.add_argument("--tags", help="쉼표 구분 태그(최대 5개)")
    s.add_argument("--project", help="프로젝트 이름")

    s = add("search", "볼트 검색(BM25-lite + 최근성)", cmd_search)
    s.add_argument("query", help="검색어")
    s.add_argument("--type", choices=ALL_TYPES, help="타입 필터")
    s.add_argument("--project", help="프로젝트 필터")
    s.add_argument("--tag", help="태그 필터")
    s.add_argument("--since", help="이 날짜 이후 생성(YYYY-MM-DD)")
    s.add_argument("--limit", type=int, default=10, help="최대 결과 수(기본 10)")

    s = add("show", "노트의 프론트매터+본문 출력", cmd_show)
    s.add_argument("path", help="노트 경로 또는 파일명")

    s = add("index", "BRAIN.md 재생성", cmd_index)
    s.add_argument("--head", type=int, help="첫 N줄만 출력(훅 주입용). 볼트 없으면 무음 exit 0")

    s = add("review", "기간 리뷰: 신규·되돌아볼 결정·고아·링크 제안", cmd_review)
    s.add_argument("--days", type=int, default=7, help="기간(일, 기본 7)")
    s.add_argument("--semantic", action="store_true", help="Claude가 내용상 관련 노트 쌍을 추가로 제안(태그·프로젝트 겹침 외)")
    s.add_argument("--limit", type=int, default=25, help="--semantic 후보 노트 수(고아·최근 우선, 기본 25)")

    s = add("retro", "주간 회고: Claude가 지난 N일을 되돌아본 노트(journal/YYYY/날짜-weekly.md) + 코칭 질문 3개", cmd_retro)
    s.add_argument("--days", type=int, default=7, help="기간(일, 기본 7)")
    s.add_argument("--force", action="store_true", help="같은 날 회고가 있어도 다시")
    s.add_argument("--kakao", "--notify", dest="kakao", action="store_true", help="알림 보내기(카톡 헬퍼 → 없으면 macOS 알림 센터)")
    s.add_argument("--dry-run", action="store_true", help="재료만 보여주고 호출하지 않음")

    s = add("decide", "결정 대체 처리(옛 결정을 superseded로)", cmd_decide)
    s.add_argument("--supersede", required=True, metavar="OLD_PATH", help="대체될 옛 결정")
    s.add_argument("new_path", metavar="NEW_PATH", help="새 결정")

    s = add("link", "두 노트를 서로 연결(프론트매터 links)", cmd_link)
    s.add_argument("a", help="노트 A")
    s.add_argument("b", help="노트 B")

    s = add("import", "마크다운 폴더·Obsidian 볼트·Claude Code 메모리·애플 메모 가져오기", cmd_import)
    s.add_argument("path", nargs="?", help="가져올 파일/폴더(홈 아래). --apple-notes면 생략")
    s.add_argument("--dry-run", action="store_true", help="쓰지 않고 결과만 미리보기")
    s.add_argument("--apple-notes", action="store_true", help="맥 메모 앱(Notes.app)에서 가져오기(JXA, 읽기 전용)")
    s.add_argument("--folder", action="append", metavar="이름", help="애플 메모 폴더 이름 필터(여러 번 지정 가능)")
    s.add_argument("--since", metavar="YYYY-MM-DD", help="애플 메모: 이 날짜 이후 생성된 노트만")

    s = add("relink", "끊어진 [[링크]] 복구(`_`→`-`·메모리 타입 접두어 제거로 찾기)", cmd_relink)
    s.add_argument("--dry-run", action="store_true", help="쓰지 않고 변경 대상만 출력")

    s = add("lint", "볼트 데이터 품질 점검(프론트매터·타입·링크·중복 등). --fix로 안전한 항목만 고침", cmd_lint)
    s.add_argument("--fix", action="store_true", help="고칠 수 있는 이슈만 자동으로 고치고 커밋")

    s = add("config", "설정 조회/변경", cmd_config)
    s.add_argument("action", choices=("get", "set", "init-widgets"),
                   help="get · set · init-widgets(예시 widgets.json 생성, 기존 파일 보존)")
    s.add_argument("key", nargs="?", help="vault | git_autocommit | index_head | assistant_name | ask_cmd | kakao_cmd | notify_channels | notify_center")
    s.add_argument("value", nargs="?", help="set할 값")

    s = add("notify", "알림 채널 시험 발송(카톡 헬퍼 → 없으면 macOS 알림 센터). 설정 점검용", cmd_notify)
    s.add_argument("action", choices=("test",), help="test: 시험 메시지 발송")
    s.add_argument("message", nargs="?", help="보낼 메시지(기본: 안내 문구)")

    s = add("serve", "로컬 대시보드 서버(127.0.0.1 전용)", cmd_serve)
    s.add_argument("--port", type=int, default=7777, help="포트(기본 7777, 0이면 임의)")
    s.add_argument("--open", action="store_true", help="브라우저 자동 열기")
    s.add_argument("--demo", action="store_true", help="가공 샘플 데모 볼트로 서빙")

    add("today", "오늘 브리핑: 일정·되돌아볼 결정·자동화 상태·inbox·이번 주 신규(+카톡용 200자)", cmd_today)
    add("widgets", "위젯(~/.config/second-brain/widgets.json) 상태 조회", cmd_widgets)

    s = add("widget", "위젯(자동화 직원) 채용·이동·이름변경·퇴사·정지·재개·실행·요약·조회·목록. 터미널이라 allow_run/allow_hire를 건너뛴다", cmd_widget)
    s.add_argument("action", choices=("add", "move", "rename", "remove", "pause", "resume", "run", "brief", "show", "list"))
    s.add_argument("arg1", nargs="?", help="add: 제목 · 그 외: id")
    s.add_argument("arg2", nargs="?", help="add: source(홈 경로) · move: 팀 · rename: 새 제목")
    s.add_argument("--kind", choices=WIDGET_HIRE_KINDS, default="log", help="add: log · json · csv · markdown(기본 log)")
    s.add_argument("--team", help="add: 소속 팀")
    s.add_argument("--ok", dest="ok_pattern", help="add: 정상 판정 정규식")
    s.add_argument("--fail", dest="fail_pattern", help="add: 실패 판정 정규식")
    s.add_argument("--stale", dest="stale_minutes", type=int, help="add: 이 시간(분) 넘게 갱신 없으면 stale")
    s.add_argument("--lines", type=int, default=5, help="add: log 마지막 N줄(기본 5)")
    s.add_argument("--dry", action="store_true", help="run: 실제로 돌리지 않고 실행 방법만 보여줌")
    s.add_argument("--force", action="store_true", help="brief: 오늘 캐시가 있어도 다시 요약(Claude 호출)")

    s = add("agenda", "일정: 오늘·다가오는 N일(설정된 캘린더 소스에서)", cmd_agenda)
    s.add_argument("--days", type=int, default=7, help="며칠치(기본 7)")

    s = add("remind", "출발·시작 알림: 곧 시작하는 동선 단계(기본 10분 전)·시간 일정(30분 전). 같은 알림은 하루 한 번", cmd_remind)
    s.add_argument("--kakao", "--notify", dest="kakao", action="store_true", help="알림 보내기(카톡 헬퍼 → 없으면 macOS 알림 센터)")
    s.add_argument("--steps-before", type=int, default=10, help="동선 단계 몇 분 전(기본 10)")
    s.add_argument("--events-before", type=int, default=30, help="시간 일정 몇 분 전(기본 30)")

    s = add("ask", "비서에게 자유 질문(오늘 상태·볼트 검색·최근 대화를 붙여 헤드리스 Claude에게)", cmd_ask)
    s.add_argument("text", nargs="+", help="질문")

    s = add("brief", "아침 브리핑(today와 같음). --kakao면 알림으로 200자 발송", cmd_brief)
    s.add_argument("--kakao", "--notify", dest="kakao", action="store_true", help="알림 보내기(카톡 헬퍼 → 없으면 macOS 알림 센터)")
    s.add_argument("--evening", action="store_true", help="저녁 마감: 남은 할 일·내일 첫 일정")
    s.add_argument("--journal", action="store_true", help="(--evening과) Claude가 오늘 일지를 먼저 쓰고 한 줄을 붙임")

    s = add("journal", "오늘 일지를 Claude가 5줄로 씀(journal/YYYY/날짜.md). --dry-run은 재료만", cmd_journal)
    s.add_argument("--date", help="YYYY-MM-DD (기본 오늘)")
    s.add_argument("--force", action="store_true", help="이미 있으면 다시 씀")
    s.add_argument("--kakao", "--notify", dest="kakao", action="store_true", help="알림 보내기(카톡 헬퍼 → 없으면 macOS 알림 센터)")
    s.add_argument("--dry-run", action="store_true", help="재료만 보여주고 호출하지 않음")

    s = add("task", "할 일: add <내용> [--due D|--tomorrow|--someday|--waiting 누구] · list · done <줄> · move <줄> <today|tomorrow|week|someday|clear|날짜> · remove <줄>", cmd_task)
    s.add_argument("action", choices=("add", "list", "done", "undo", "move", "remove", "carry"))
    s.add_argument("arg1", nargs="?", help="add: 내용 · done/undo/move/remove: 줄 번호(list의 #) · carry: 없음(오늘 남은 것 전부 내일로)")
    s.add_argument("arg2", nargs="?", help="move: 목적지")
    s.add_argument("--due", help="마감 YYYY-MM-DD")
    s.add_argument("--tomorrow", action="store_true", help="마감 내일")
    s.add_argument("--someday", action="store_true", help="언젠가")
    s.add_argument("--waiting", help="기다리는 상대")
    s.add_argument("--project", help="프로젝트")

    s = add("event", "일정 노트: memo <키> <내용> · todo <키> <항목> · show <키>  (키: 'YYYY-MM-DD|제목')", cmd_event)
    s.add_argument("action", choices=("memo", "todo", "step", "show"))
    s.add_argument("key", help="일정 키 'YYYY-MM-DD|제목' (agenda --json의 key)")
    s.add_argument("text", nargs="?", help="메모 내용 · 준비 항목 · 동선 'HH:MM 내용 (NN분)'")
    s.add_argument("--day", help="step: 여러 날 계획에서 이 단계의 날짜 YYYY-MM-DD")
    s.add_argument("--body-file", help="내용 파일('-'면 stdin)")
    s.add_argument("--end", help="여러 날 일정이면 종료일 YYYY-MM-DD(같은 이름의 날짜들에 함께 붙음)")
    s.add_argument("--location", help="장소 메모")

    s = add("mail", "메일(IMAP 읽기 전용): add <주소> --password-file F · test · list · remove", cmd_mail)
    s.add_argument("action", choices=("add", "test", "list", "remove"))
    s.add_argument("user", nargs="?", help="add: 메일 주소")
    s.add_argument("--password-file", help="앱 비밀번호가 한 줄 든 파일(기본 ~/.config/second-brain/mail.pass)")
    s.add_argument("--host", help="IMAP 호스트(gmail/naver는 자동)")
    s.add_argument("--sent-folder", help="보낸편지함 폴더 이름")

    s = add("prepare", "다가오는 일정에 Claude가 준비 체크리스트·동선 초안 제안(보류함에 저장, 보드에서 채택)", cmd_prepare)
    s.add_argument("--days", type=int, default=SUGGEST_DAYS, help="며칠 앞까지(기본 7)")
    s.add_argument("--key", help="특정 일정 키 'YYYY-MM-DD|제목'만")
    s.add_argument("--force", action="store_true", help="이미 제안한 일정도 다시")
    s.add_argument("--dry-run", action="store_true", help="대상만 보여주고 호출하지 않음")

    s = add("enrich", "Claude로 노트 정제: 제목·요약·태그·관련 링크 (기본: 가져온 노트 중 요약 없는 것)", cmd_enrich)
    s.add_argument("paths", nargs="*", help="특정 노트 경로/stem만")
    s.add_argument("--all", action="store_true", help="가져온 노트뿐 아니라 전부(일정 노트 제외)")
    s.add_argument("--force", action="store_true", help="요약이 있어도 다시")
    s.add_argument("--limit", type=int, help="최대 N개")
    s.add_argument("--dry-run", action="store_true", help="대상만 보여주고 호출하지 않음")

    s = add("doctor", "설치·연결 점검: 볼트·Python·Claude CLI·캘린더·카톡·위젯·알림 에이전트·대시보드", cmd_doctor)

    s = add("backup", "볼트를 zip으로 백업(~/.cache/second-brain/backups/, 기본 14개 보관)", cmd_backup)
    s.add_argument("--dest", help="백업 폴더(기본 ~/.cache/second-brain/backups)")
    s.add_argument("--keep", type=int, default=BACKUP_KEEP, help="보관 개수(기본 14)")

    s = add("restore", "백업 zip에서 볼트 복구(덮어쓰기 전 안전 백업 자동)", cmd_restore)
    s.add_argument("zip", nargs="?", help="복구할 백업 zip 경로/파일명(기본: 최신 백업)")
    s.add_argument("--list", action="store_true", help="사용 가능한 백업 목록만 보여주고 종료")
    s.add_argument("--dry-run", action="store_true", help="실제로 바꾸지 않고 계획만 보여줌")
    s.add_argument("--replace", action="store_true", help="zip에 없는 기존 파일을 삭제(기본은 보존)")
    s.add_argument("--no-safety-backup", action="store_true", help="복구 전 안전 백업을 만들지 않음")

    s = add("agents","비서 알림 에이전트(launchd): install [brief remind evening backup prepare retro serve] · status · remove", cmd_agents)
    s.add_argument("action", choices=("install", "status", "remove"))
    s.add_argument("names", nargs="*", help="brief(07:00 브리핑) remind(10분 알림) evening(21:30 마감+일지) backup(23:00 백업) prepare(06:40 준비 제안) retro(월 09:00 회고) serve(대시보드 상주, 이름을 직접 줘야 설치됨). 비우면 serve를 제외한 전부")
    s.add_argument("--dry-run", action="store_true", help="쓰지 않고 만들 파일만 보여줌")
    s.add_argument("--force", action="store_true", help="이미 있는 plist 덮어쓰기")
    s.add_argument("--port", type=int, help="serve 설치/조회 시 포트 재정의(기본 7777, serve에만 적용)")

    s = add("calendar", "일정 소스 관리: list · add <ics|eventkit> <이름> · remove <이름> · test", cmd_calendar)
    s.add_argument("action", choices=("list", "add", "remove", "test"))
    s.add_argument("arg1", nargs="?", help="add: 종류(ics|eventkit) · remove: 소스 이름")
    s.add_argument("arg2", nargs="?", help="add: 소스 이름(예: 구글)")
    s.add_argument("--url-file", help="ics: 비공개 ICS 주소가 첫 줄에 적힌 파일(예: ~/.config/second-brain/google.ics.url)")
    s.add_argument("--path", help="ics: 로컬 .ics 파일")
    s.add_argument("--calendars", help="eventkit: 읽을 캘린더 이름(쉼표). 비우면 전부")
    s.add_argument("--days", type=int, default=7, help="test일 때 며칠치")

    s = add("reminders", "맥 미리알림(읽기 전용): on [--lists A,B] · off · list · test", cmd_reminders)
    s.add_argument("action", choices=("on", "off", "list", "test"))
    s.add_argument("--lists", help="읽을 미리알림 목록 이름(쉼표). 비우면 전부")
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return EXIT_INPUT
    try:
        return args.func(args)
    except BrainError as e:
        log(f"오류: {e}")
        return e.code


if __name__ == "__main__":
    sys.exit(main())
