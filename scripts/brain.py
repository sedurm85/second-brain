#!/usr/bin/env python3
"""세컨드브레인 코어 CLI — 마크다운 볼트 관리 (Python 3.9+ 표준 라이브러리만 사용).

데이터는 stdout, 로그는 stderr. 종료 코드: 0 성공, 2 입력 오류, 3 볼트 없음.
"""
import argparse
import json
import math
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

VERSION = "0.1.0"

EXIT_OK = 0
EXIT_INPUT = 2
EXIT_NO_VAULT = 3

NOTE_TYPES = ("note", "idea", "source", "meeting")
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
    """원본 파일 → (type, meta, body). 원본은 읽기만 한다."""
    text = path.read_text(encoding="utf-8", errors="replace")
    meta, body = parse_frontmatter(text)
    mtime = date.fromtimestamp(path.stat().st_mtime).isoformat()
    if _is_memory_meta(meta):
        mtype = _memory_type(meta)
        ntype = MEMORY_TYPE_MAP.get(mtype, "note")
        md = meta.get("metadata") if isinstance(meta.get("metadata"), dict) else {}
        created = _date_from(meta.get("modified") or md.get("modified"), mtime)
        new_meta = {"title": _memory_title(meta["name"], mtype), "type": ntype, "created": created,
                    "tags": ["claude-memory"] + ([mtype] if mtype else [])}
        desc = str(meta.get("description") or "").strip()
        new_body = (f"> {desc}\n\n" if desc else "") + body.lstrip("\n")
        new_meta["imported_from"] = path.name
        return ntype, new_meta, new_body
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
    return ntype, new_meta, body


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
    seen = {(n.title, n.created) for n in load_notes(vault)}
    taken = existing_stems(vault)
    next_dec = next_decision_number(vault)
    result = {"source": str(src), "dry_run": dry_run, "imported": [], "skipped": [], "errors": []}
    for f in files:
        if f.name == "MEMORY.md" and f.parent.name == "memory":
            result["skipped"].append({"file": str(f), "reason": "메모리 인덱스 파일"})
            continue
        try:
            ntype, meta, body = convert_file(f)
        except (OSError, UnicodeError) as e:
            result["errors"].append({"file": str(f), "error": str(e)})
            continue
        key = (meta["title"], meta["created"])
        if key in seen:
            result["skipped"].append({"file": str(f), "reason": "중복(title+created)"})
            continue
        seen.add(key)
        if ntype == "decision":
            stem = unique_stem(f"{next_dec:03d}-{slugify(meta['title'])}", taken)
            dest = vault / "decisions" / f"{stem}.md"
            next_dec += 1
        else:
            dest = target_path(vault, ntype, meta["title"], meta["created"], taken)
        taken.add(dest.stem)
        if not dry_run:
            write_note(dest, meta, body)
        result["imported"].append({"file": str(f), "dest": dest.relative_to(vault).as_posix(),
                                   "type": ntype, "title": meta["title"]})
    if not dry_run and result["imported"]:
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
           f"오류 {len(r['errors'])}개"]
    if by_type:
        out.append("타입별: " + ", ".join(f"{k} {c}" for k, c in sorted(by_type.items())))
    out += [f"- {x['dest']} ← {Path(x['file']).name}" for x in r["imported"][:30]]
    if len(r["imported"]) > 30:
        out.append(f"- ... 외 {len(r['imported']) - 30}개")
    out += [f"! {Path(e['file']).name}: {e['error']}" for e in r["errors"]]
    print("\n".join(out))
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


def cmd_config(args):
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

    s = add("config", "설정 조회/변경", cmd_config)
    s.add_argument("action", choices=("get", "set"), help="get 또는 set")
    s.add_argument("key", nargs="?", help="vault | git_autocommit | index_head")
    s.add_argument("value", nargs="?", help="set할 값")
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
