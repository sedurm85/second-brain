"""세컨드브레인 애플 메모 어댑터 (v0.29). 표준 라이브러리만 사용, 읽기 전용.

macOS 메모 앱(Notes.app)을 JXA(osascript -l JavaScript)로 읽는다. reminders.py와 같은 패턴:
osascript 서브프로세스 호출, 실패 시 안내 메시지만 남기고 빈 목록, 테스트는 환경변수로 가짜 명령을
주입한다.

쓰기는 하지 않는다 — 노트 수정·삭제는 메모 앱에서 직접 한다. brain.py import --apple-notes가
fetch_notes()/html_to_markdown()을 써서 볼트에 마크다운 노트를 만든다.

메모 스키마: {id, title, folder, created, modified, body_html, plaintext}
  created/modified는 ISO 8601 문자열(가능하면), 실패해도 빈 문자열로 채운다.

환경변수:
  SECOND_BRAIN_APPLE_NOTES_CMD - osascript 호출을 대체할 명령(테스트용, 고정 JSON을 출력하는 가짜 스크립트)
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import shlex
import subprocess
from html.parser import HTMLParser

TIMEOUT_SEC = 60
MAX_NOTES = 500

PERMISSION_HINT = "시스템 설정 → 개인정보 보호 및 보안 → 메모(또는 자동화)에서 터미널/파이썬 허용"

LAST_ERROR = None  # 마지막 조회 실패 사유(성공하면 None). 호출자가 CLI에 보여준다.

# 메모 앱을 JXA로 읽는다. 계정→폴더→노트 순서로 순회하고, 폴더 이름 필터가 있으면 그 폴더만
# 읽는다. 항목 하나 실패해도 전체가 죽지 않게 개별 try/catch, 전체 상한 MAX.
NOTES_JXA = r"""
function run(argv) {
  var wanted = JSON.parse(argv[0] || "[]");
  var MAX = parseInt(argv[1] || "500", 10) || 500;
  var app = Application("Notes");
  var accounts;
  try {
    accounts = app.accounts();
  } catch (e) {
    return JSON.stringify({error: "permission", message: String(e)});
  }
  var out = [];
  for (var a = 0; a < accounts.length && out.length < MAX; a++) {
    var folders;
    try { folders = accounts[a].folders(); } catch (e) { continue; }
    for (var f = 0; f < folders.length && out.length < MAX; f++) {
      var folder = folders[f];
      var fname;
      try { fname = folder.name(); } catch (e) { continue; }
      if (wanted.length && wanted.indexOf(fname) < 0) continue;
      var notes;
      try { notes = folder.notes(); } catch (e) { continue; }
      for (var n = 0; n < notes.length && out.length < MAX; n++) {
        var note = notes[n];
        try {
          var created = "", modified = "";
          try { created = note.creationDate().toISOString(); } catch (e2) {}
          try { modified = note.modificationDate().toISOString(); } catch (e3) {}
          out.push({
            id: String(note.id()),
            title: note.name() || "",
            folder: fname,
            created: created,
            modified: modified,
            body_html: note.body() || "",
            plaintext: note.plaintext() || ""
          });
        } catch (e4) { continue; }
      }
    }
  }
  return JSON.stringify({notes: out});
}
"""


def fetch_notes(folders=None, limit=MAX_NOTES, timeout=TIMEOUT_SEC):
    """메모 앱 노트를 읽는다. 실패하면 []에 LAST_ERROR를 남긴다. 캐시 없음(가져오기는 1회성 호출)."""
    global LAST_ERROR
    LAST_ERROR = None
    wanted_json = json.dumps(list(folders or []), ensure_ascii=False)
    override = os.environ.get("SECOND_BRAIN_APPLE_NOTES_CMD")
    if override:
        args = shlex.split(override) + [wanted_json, str(limit)]
    else:
        args = ["osascript", "-l", "JavaScript", "-e", NOTES_JXA, wanted_json, str(limit)]
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        LAST_ERROR = f"메모 조회 시간 초과({timeout}초)"
        return []
    except OSError as e:
        LAST_ERROR = f"메모 조회 실행 실패: {e}"
        return []
    if r.returncode != 0:
        text = (r.stderr or r.stdout or "").strip()
        if "-1743" in text:
            LAST_ERROR = f"메모 접근 권한이 없어요(-1743). {PERMISSION_HINT}"
        else:
            LAST_ERROR = text[:300] or "osascript 실패"
        return []
    try:
        data = json.loads(r.stdout.strip() or "{}")
    except ValueError:
        LAST_ERROR = "메모 응답을 해석할 수 없어요"
        return []
    if not isinstance(data, dict):
        data = {}
    if data.get("error"):
        text = json.dumps(data, ensure_ascii=False)
        if data.get("error") == "permission" or "-1743" in text:
            LAST_ERROR = f"메모 접근 권한이 없어요. {PERMISSION_HINT}"
        else:
            LAST_ERROR = str(data.get("message") or data.get("error"))[:300]
        return []
    items = []
    for it in (data.get("notes") or [])[:limit]:
        if not isinstance(it, dict):
            continue
        items.append({
            "id": str(it.get("id") or ""),
            "title": str(it.get("title") or "").strip(),
            "folder": str(it.get("folder") or ""),
            "created": str(it.get("created") or ""),
            "modified": str(it.get("modified") or ""),
            "body_html": str(it.get("body_html") or ""),
            "plaintext": str(it.get("plaintext") or ""),
        })
    return items


# ---------------------------------------------------------------------------
# HTML(메모 본문) → 마크다운
# ---------------------------------------------------------------------------

_BLOCK_TAGS = ("div", "p")
_HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")


class _NotesHTMLParser(HTMLParser):
    """메모 앱이 내보내는 단순 HTML(제목/볼드/이탤릭/리스트/링크/이미지)만 다루는 관대한 파서."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.list_stack = []  # [["ul"|"ol", 카운터], ...]
        self.link_href = None
        self.link_text = []
        self.skip_depth = 0  # <style>/<script> 안 텍스트는 버린다
        self.attachments = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("style", "script"):
            self.skip_depth += 1
        elif tag in _HEADING_TAGS:
            self.out.append("\n" + "#" * int(tag[1]) + " ")
        elif tag in ("b", "strong"):
            self._emit("**")
        elif tag in ("i", "em"):
            self._emit("*")
        elif tag == "ul":
            self.list_stack.append(["ul", 0])
            self.out.append("\n")
        elif tag == "ol":
            self.list_stack.append(["ol", 0])
            self.out.append("\n")
        elif tag == "li":
            self.out.append("\n")
            if self.list_stack and self.list_stack[-1][0] == "ol":
                self.list_stack[-1][1] += 1
                self.out.append(f"{self.list_stack[-1][1]}. ")
            else:
                self.out.append("- ")
        elif tag == "br":
            self.out.append("\n")
        elif tag in _BLOCK_TAGS:
            self.out.append("\n")
        elif tag == "a":
            self.link_href = attrs.get("href")
            self.link_text = []
        elif tag in ("img", "object"):
            self.attachments += 1

    def handle_endtag(self, tag):
        if tag in ("style", "script"):
            self.skip_depth = max(0, self.skip_depth - 1)
        elif tag in _HEADING_TAGS:
            self.out.append("\n")
        elif tag in ("b", "strong"):
            self._emit("**")
        elif tag in ("i", "em"):
            self._emit("*")
        elif tag in ("ul", "ol"):
            if self.list_stack:
                self.list_stack.pop()
            self.out.append("\n")
        elif tag == "li":
            pass  # 다음 <li>의 선행 개행 또는 </ul>/</ol>의 개행에 맡긴다(중복 공백 방지)
        elif tag in _BLOCK_TAGS:
            self.out.append("\n")
        elif tag == "a":
            text = "".join(self.link_text).strip()
            href = (self.link_href or "").strip()
            if href and text:
                self.out.append(f"[{text}]({href})")
            elif href:
                self.out.append(href)
            else:
                self.out.append(text)
            self.link_href = None
            self.link_text = []

    def handle_data(self, data):
        if self.skip_depth:
            return
        if self.link_href is not None:
            self.link_text.append(data)
        else:
            self.out.append(data)

    def _emit(self, marker):
        if self.link_href is not None:
            self.link_text.append(marker)
        else:
            self.out.append(marker)


def html_to_markdown(body_html):
    """메모 본문 HTML → 마크다운 텍스트. 깨진 HTML도 최대한 관대하게 처리한다."""
    if not body_html or not str(body_html).strip():
        return ""
    parser = _NotesHTMLParser()
    try:
        parser.feed(str(body_html))
        parser.close()
    except Exception:
        pass
    text = "".join(parser.out)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    if parser.attachments:
        suffix = f"(첨부 {parser.attachments}개)"
        text = f"{text}\n\n{suffix}" if text else suffix
    return text


def _main(argv=None):
    """스모크용 최소 CLI: `python3 apple_notes.py --limit N`으로 제목만 출력한다."""
    ap = argparse.ArgumentParser(description="애플 메모 조회 스모크 테스트")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--folder", action="append", dest="folders")
    args = ap.parse_args(argv)
    notes = fetch_notes(folders=args.folders, limit=args.limit)
    if LAST_ERROR:
        print(f"오류: {LAST_ERROR}")
        return 1
    print(f"{len(notes)}개 메모")
    for n in notes:
        print(f"- [{n['folder']}] {n['title']} (수정 {n['modified'][:10]})")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_main())
