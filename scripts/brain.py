#!/usr/bin/env python3
"""세컨드브레인 코어 CLI — 마크다운 볼트 관리 (Python 3.9+ 표준 라이브러리만 사용).

데이터는 stdout, 로그는 stderr. 종료 코드: 0 성공, 2 입력 오류, 3 볼트 없음.
"""
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

VERSION = "0.1.0"

EXIT_OK = 0
EXIT_INPUT = 2
EXIT_NO_VAULT = 3

NOTE_TYPES = ("note", "idea", "source", "meeting", "event")
ALL_TYPES = NOTE_TYPES + ("decision", "project", "person")
DECISION_STATUSES = ("open", "decided", "superseded")
SKIP_FILES = {"BRAIN.md", "inbox.md"}
SKIP_DIRS = {".git", ".obsidian", ".trash", "node_modules"}

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


def load_notes(vault):
    notes = []
    for p in iter_note_files(vault):
        try:
            notes.append(Note(vault, p))
        except OSError as e:
            log(f"경고: 읽기 실패 {p}: {e}")
    return notes


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
        return f"# {title}\n\n## 준비\n\n## 메모\n"
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


FIELD_WEIGHTS = {"title": 3.0, "tags": 2.0, "body": 1.0}
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


def search(vault, query, ntype=None, project=None, tag=None, since=None, limit=10, today=None):
    today = today or date.today()
    qtoks = tokenize(query)
    if not qtoks:
        raise BrainError("검색어에서 토큰을 찾지 못했습니다.")
    if since:
        parse_date(since, "--since")
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
        fields = {"title": n.title, "tags": " ".join(n.tags), "body": n.body}
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
    res = search(vault, q, limit=limit, today=today)
    for r in res:
        r["snippets"] = r.get("snippet", [])  # 대시보드 계약 키. CLI 호환 위해 snippet도 유지
    return res


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
    build_index(vault, today)
    return vault


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
    return home_dir() / ".config" / "second-brain" / "widgets.json"


def load_widgets_config():
    """widgets.json 로드. 없거나 깨졌으면 빈 목록(깨진 경우 error 포함)."""
    p = widgets_config_path()
    empty = {"allow_commands": False, "widgets": [], "path": str(p), "error": None}
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
    return {"allow_commands": data.get("allow_commands") is True,
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
    if size > max_bytes and lines:
        lines = lines[1:]  # 잘린 첫 줄 버림
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


def dash_tasks(vault, today=None, widgets=None, agenda=None):
    """/api/tasks. inbox 할 일 + 파생 할 일을 4묶음으로."""
    today = today or date.today()
    own = parse_tasks(vault, today) if vault else []
    b = bucket_tasks(own, today)
    for d in derived_tasks(vault, today, widgets, agenda):
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
        raise BrainError("action은 add · check · move · remove 중 하나")
    p.write_text("\n".join(lines), encoding="utf-8")
    git_commit(vault, f"brain: task {action}")
    return {"ok": True}


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


def kakao_brief(t):
    """카톡용 요약(≤200자)."""
    parts = [f"[{t['date']} {t['weekday'][0]}] {t['greeting']}"]
    ag_bit = agenda_mod.agenda_kakao(t.get("agenda") or {})
    if ag_bit:
        parts.append(ag_bit)
    if t["revisit"]:
        parts.append("결정 " + ", ".join(f"{d['title']}({_dleft(d)})" for d in t["revisit"][:3]))
    bad = [w for w in t["top_widgets"] if w["status"] in ("fail", "stale")]
    if bad:
        parts.append("자동화 " + ", ".join(f"{w['title']} {w['status']}" for w in bad[:3]))
    elif sum(t["widgets_summary"].values()):
        parts.append(f"자동화 정상 {t['widgets_summary']['ok']}")
    if t["inbox"]:
        parts.append(f"할 일 {len(t['inbox'])}: " + ", ".join(t["inbox"][:3]))
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
    t["agenda"] = {k: ag.get(k) for k in ("today", "next", "current", "conflicts", "sources", "total")}
    t["agenda"]["upcoming"] = (ag.get("upcoming") or [])[:6]
    t["agenda"]["upcoming_count"] = len(ag.get("upcoming") or [])
    t["agenda"]["sentence"] = agenda_mod.agenda_sentence(ag)
    tb = dash_tasks(vault, today, widgets, ag)
    t["tasks"] = {"counts": tb["counts"], "today": tb["today"][:8], "waiting": tb["waiting"][:5]}
    # 표시용 inbox: 오늘 묶음(직접 적은 것 우선). 예전 계약(문자열 목록) 유지
    t["inbox"] = [x["text"] for x in tb["today"] if x.get("kind") == "task"] or t["inbox"]
    t["kakao"] = kakao_brief(t)
    return t


def today_human(t):
    """사람용 브리핑(6줄 이내) + 카톡용 블록."""
    out = [f"{t['greeting']}! {t['date']} {t['weekday']}"]
    ag = t.get("agenda") or {}
    if ag.get("sentence"):
        out.append(ag["sentence"])
    bad_src = [s_ for s_ in (ag.get("sources") or []) if s_.get("status") != "ok"]
    if bad_src and not ag.get("today"):
        out.append("일정 연결 안 됨: " + "; ".join(f"{s_['name']} {s_['status']}" for s_ in bad_src[:2]))
    if t["revisit"]:
        out.append(f"되돌아볼 결정 {len(t['revisit'])}: " +
                   ", ".join(f"{d['title']}({_dleft(d)})" for d in t["revisit"][:3]))
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
    return "\n".join(out[:6]) + "\n\n카톡용(200자)\n" + t["kakao"]


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
            if route == "/api/task":
                res = task_action(vault, body, self.server.today or date.today())
                return self._json(200, res)
            if route == "/api/ask":
                widgets = collect_widgets(self.server.widget_cache)
                t = dash_today(vault, self.server.today, widgets=widgets, agenda=self.server.agenda_cache.get())
                return self._json(200, ask_assistant(str(body.get("text") or ""), t))
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
            if route == "/api/office":
                return self._json(200, dash_office(collect_widgets(self.server.widget_cache)))
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
                return self._json(200, dash_search(vault, q, _int_param(qs, "limit", 20, hi=200), today))
            if route == "/api/note":
                return self._json(200, dash_note(vault, (qs.get("path") or [""])[0]))
            if route == "/api/timeline":
                return self._json(200, dash_timeline(vault, _int_param(qs, "days", 30, hi=3650), today))
            if route == "/api/decisions":
                return self._json(200, dash_decisions(vault, today))
            if route == "/api/projects":
                return self._json(200, dash_projects(vault))
            if route == "/api/widgets":
                return self._json(200, collect_widgets(self.server.widget_cache))
            if route == "/api/today":
                widgets = collect_widgets(self.server.widget_cache)
                return self._json(200, dash_today(vault, self.server.today, widgets=widgets,
                                                  agenda=self.server.agenda_cache.get()))
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


def cmd_serve(args):
    if args.demo:
        v = build_demo_vault()
        log(f"데모 볼트(가공 샘플 데이터): {v}")
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
    emit(args, {"vault": str(v), "created": created},
         f"볼트 준비 완료: {v}\n생성/갱신: " + ", ".join(created))
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


def cmd_search(args):
    v = require_vault()
    res = search(v, args.query, ntype=args.type, project=args.project, tag=args.tag,
                 since=args.since, limit=args.limit)
    if args.json:
        emit(args, {"query": args.query, "results": res}, None)
        return EXIT_OK
    if not res:
        print(f"'{args.query}' 검색 결과 없음 (기록 없음)")
        return EXIT_OK
    lines = []
    for i, r in enumerate(res, 1):
        lines.append(f"{i}. [{r['type']}] {r['title']}  ({r['score']})  {r['path']}")
        lines += [f"     {s}" for s in r["snippet"]]
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


def _event_note_payload(n):
    items = _checklist(n.body)
    memo = n.body.split("## 메모", 1)[1].strip() if "## 메모" in n.body else ""
    return {"path": n.rel, "title": n.title, "checklist": items,
            "done": sum(1 for x in items if x["done"]), "total": len(items),
            "memo": memo[:1200], "location": n.meta.get("location") or ""}


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


def attach_event_notes(vault, ag):
    """agenda(dict)의 today/upcoming 각 일정에 note 필드 부착(없으면 None)."""
    if not vault or not vault_exists(Path(vault)):
        return ag
    exact, ranged = event_notes_index(Path(vault))
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
    if ag.get("next"):
        k = ag["next"].get("key")
        ag["next"]["note"] = _event_note_payload(exact[k]) if k in exact else None
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
    path = create_note(vault, "event", title, created=day, extra=extra)
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
    raise BrainError("action은 memo · todo · check 중 하나")


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


def dash_office(widgets, ps_lines=None, cron_lines=None, now=None):
    """/api/office 응답: teams, widgets(요약), running, jobs, events, assistant_name."""
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
    }


ASK_TIMEOUT_SEC = 120


def ask_assistant(question, today):
    """코어 화면의 자유 질문. 로컬 데이터(오늘 브리핑 JSON)를 붙여 헤드리스 Claude에 묻는다.
    명령은 config `ask_cmd`(기본 `claude -p`), 테스트·오프라인은 환경변수 SECOND_BRAIN_ASK_CMD로 대체."""
    q = " ".join(question.split())
    if not q:
        raise BrainError("질문이 비었어요")
    if len(q) > 1000:
        raise BrainError("질문은 1000자까지")
    cfg = load_config()
    cmd = os.environ.get("SECOND_BRAIN_ASK_CMD") or cfg.get("ask_cmd") or "claude -p --output-format text"
    name = str(cfg.get("assistant_name") or "브레인")
    ctx = {k: today.get(k) for k in ("date", "weekday", "agenda", "revisit", "inbox", "widgets_summary", "top_widgets", "this_week") if k in today}
    prompt = (f"너는 사용자의 개인 비서 「{name}」이다. 아래 JSON은 오늘 상태(일정·할 일·되돌아볼 결정·자동화)다. "
              f"이 데이터와 상식으로 질문에 한국어 해요체로 2~3문장, 200자 안에서 답한다. 모르면 모른다고 말한다. 목록·마크다운 없이 말로.\n\n"
              f"[오늘 상태]\n{json.dumps(ctx, ensure_ascii=False)}\n\n[질문]\n{q}")
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
    ans = " ".join(r.stdout.split())
    return {"answer": ans[:600], "via": argv[0]}


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
    res = event_note_action(v, {"action": args.action, "key": args.key, "text": text, "end": args.end, "location": args.location})
    note = res["note"]
    emit(args, res, f"{'새 일정 노트 생성 후 ' if res['created'] else ''}{'메모 추가' if args.action == 'memo' else '준비 항목 추가'}: {note['path']}"
                    + (f" (준비 {note['done']}/{note['total']})" if note["total"] else ""))
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


def cmd_brief(args):
    """아침 브리핑: today와 같은 내용. --kakao면 200자 카톡 발송(헬퍼 있을 때)."""
    try:
        v = vault_path()
        if not vault_exists(v):
            v = None
    except BrainError:
        v = None
    t = dash_today(v)
    if args.kakao:
        helper = kakao_helper_path()
        if not helper:
            log("카톡 헬퍼가 없어요(~/.local/k-skill-cron/notify_kakao.py 또는 config kakao_cmd).")
            emit(args, dict(t, sent=False), today_human(t))
            return EXIT_INPUT
        r = subprocess.run([sys.executable, str(helper), t["kakao"]], capture_output=True, text=True, timeout=60)
        t["sent"] = r.returncode == 0
        if r.returncode != 0:
            log(f"카톡 발송 실패: {(r.stderr or r.stdout).strip()[:200]}")
        emit(args, t, today_human(t) + ("\n\n카톡 발송 완료" if t["sent"] else "\n\n카톡 발송 실패"))
        return EXIT_OK if t["sent"] else EXIT_INPUT
    emit(args, t, today_human(t))
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

    s = add("decide", "결정 대체 처리(옛 결정을 superseded로)", cmd_decide)
    s.add_argument("--supersede", required=True, metavar="OLD_PATH", help="대체될 옛 결정")
    s.add_argument("new_path", metavar="NEW_PATH", help="새 결정")

    s = add("link", "두 노트를 서로 연결(프론트매터 links)", cmd_link)
    s.add_argument("a", help="노트 A")
    s.add_argument("b", help="노트 B")

    s = add("import", "마크다운 폴더·Obsidian 볼트·Claude Code 메모리 가져오기", cmd_import)
    s.add_argument("path", help="가져올 파일/폴더(홈 아래)")
    s.add_argument("--dry-run", action="store_true", help="쓰지 않고 결과만 미리보기")

    s = add("relink", "끊어진 [[링크]] 복구(`_`→`-`·메모리 타입 접두어 제거로 찾기)", cmd_relink)
    s.add_argument("--dry-run", action="store_true", help="쓰지 않고 변경 대상만 출력")

    s = add("config", "설정 조회/변경", cmd_config)
    s.add_argument("action", choices=("get", "set", "init-widgets"),
                   help="get · set · init-widgets(예시 widgets.json 생성, 기존 파일 보존)")
    s.add_argument("key", nargs="?", help="vault | git_autocommit | index_head | assistant_name | ask_cmd | kakao_cmd")
    s.add_argument("value", nargs="?", help="set할 값")

    s = add("serve", "로컬 대시보드 서버(127.0.0.1 전용)", cmd_serve)
    s.add_argument("--port", type=int, default=7777, help="포트(기본 7777, 0이면 임의)")
    s.add_argument("--open", action="store_true", help="브라우저 자동 열기")
    s.add_argument("--demo", action="store_true", help="가공 샘플 데모 볼트로 서빙")

    add("today", "오늘 브리핑: 일정·되돌아볼 결정·자동화 상태·inbox·이번 주 신규(+카톡용 200자)", cmd_today)
    add("widgets", "위젯(~/.config/second-brain/widgets.json) 상태 조회", cmd_widgets)

    s = add("agenda", "일정: 오늘·다가오는 N일(설정된 캘린더 소스에서)", cmd_agenda)
    s.add_argument("--days", type=int, default=7, help="며칠치(기본 7)")

    s = add("brief", "아침 브리핑(today와 같음). --kakao면 카톡으로 200자 발송", cmd_brief)
    s.add_argument("--kakao", action="store_true", help="카톡 나에게 보내기(헬퍼 필요)")

    s = add("task", "할 일: add <내용> [--due D|--tomorrow|--someday|--waiting 누구] · list · done <줄> · move <줄> <today|tomorrow|week|someday|clear|날짜> · remove <줄>", cmd_task)
    s.add_argument("action", choices=("add", "list", "done", "undo", "move", "remove"))
    s.add_argument("arg1", nargs="?", help="add: 내용 · done/undo/move/remove: 줄 번호(list의 #)")
    s.add_argument("arg2", nargs="?", help="move: 목적지")
    s.add_argument("--due", help="마감 YYYY-MM-DD")
    s.add_argument("--tomorrow", action="store_true", help="마감 내일")
    s.add_argument("--someday", action="store_true", help="언젠가")
    s.add_argument("--waiting", help="기다리는 상대")
    s.add_argument("--project", help="프로젝트")

    s = add("event", "일정 노트: memo <키> <내용> · todo <키> <항목> · show <키>  (키: 'YYYY-MM-DD|제목')", cmd_event)
    s.add_argument("action", choices=("memo", "todo", "show"))
    s.add_argument("key", help="일정 키 'YYYY-MM-DD|제목' (agenda --json의 key)")
    s.add_argument("text", nargs="?", help="메모 내용 또는 준비 항목")
    s.add_argument("--body-file", help="내용 파일('-'면 stdin)")
    s.add_argument("--end", help="여러 날 일정이면 종료일 YYYY-MM-DD(같은 이름의 날짜들에 함께 붙음)")
    s.add_argument("--location", help="장소 메모")

    s = add("calendar", "일정 소스 관리: list · add <ics|eventkit> <이름> · remove <이름> · test", cmd_calendar)
    s.add_argument("action", choices=("list", "add", "remove", "test"))
    s.add_argument("arg1", nargs="?", help="add: 종류(ics|eventkit) · remove: 소스 이름")
    s.add_argument("arg2", nargs="?", help="add: 소스 이름(예: 구글)")
    s.add_argument("--url-file", help="ics: 비공개 ICS 주소가 첫 줄에 적힌 파일(예: ~/.config/second-brain/google.ics.url)")
    s.add_argument("--path", help="ics: 로컬 .ics 파일")
    s.add_argument("--calendars", help="eventkit: 읽을 캘린더 이름(쉼표). 비우면 전부")
    s.add_argument("--days", type=int, default=7, help="test일 때 며칠치")
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
