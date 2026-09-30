"""세컨드브레인 메일 어댑터 (v0.15, 2차 3단계 「메일」). 표준 라이브러리만, 읽기 전용.

IMAP(SSL)로 받은편지함 헤더만 가져와 세 묶음으로 나눈다. 본문은 읽지 않는다.
- reply   답장 필요: 사람이 보낸 안 읽은/깃발 메일
- waiting 기다리는 답: 내가 보낸 메일(보낸편지함) 중 아직 답이 오지 않은 것
- info    알아두면 됨: 뉴스레터·알림·자동 발송

설정(~/.config/second-brain/config.json):
  "mail": {"host": "imap.gmail.com", "port": 993, "user": "me@gmail.com",
           "password_file": "~/.config/second-brain/mail.pass",   # 앱 비밀번호 한 줄, chmod 600
           "folder": "INBOX", "sent_folder": "[Gmail]/Sent Mail", "days": 7, "max": 120}
비밀번호는 볼트·설정 파일에 직접 넣지 않고 별도 파일에서 읽는다. 결과는 ~/.cache/second-brain/mail.json에 10분 캐시.
"""
from __future__ import annotations

import email
import email.utils
import hashlib
import imaplib
import json
import os
import re
import ssl
import time
from datetime import datetime, timedelta, timezone
from email.header import Header, decode_header, make_header
from pathlib import Path

CACHE_SEC = 10 * 60
HEADER_FIELDS = "FROM TO SUBJECT DATE MESSAGE-ID IN-REPLY-TO REFERENCES LIST-ID LIST-UNSUBSCRIBE PRECEDENCE AUTO-SUBMITTED X-AUTO-RESPONSE-SUPPRESS"
AUTO_FROM_RE = re.compile(r"no-?reply|noreply|do-?not-?reply|newsletter|notification|notify|mailer|daemon|alert|info@|news@|support@|billing@|marketing|promo|bounce|updates?@", re.I)
INFO_SUBJECT_RE = re.compile(r"광고|\[광고\]|뉴스레터|newsletter|weekly|digest|receipt|영수증|명세서|결제 완료|배송|delivery|shipped|verify|인증번호|verification code|password|비밀번호 재설정|unsubscribe", re.I)
BOOKING_RE = re.compile(r"예약|reservation|booking|itinerary|e-?ticket|탑승|boarding|체크인|check-?in|confirmation|확정", re.I)


def cache_path():
    d = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")) / "second-brain"
    d.mkdir(parents=True, exist_ok=True)
    return d / "mail.json"


def _fix_8bit(s):
    """message_from_bytes가 인코딩 안 된 8비트 헤더를 surrogateescape로 넘겨준 경우 UTF-8로 복원."""
    if any(0xDC80 <= ord(ch) <= 0xDCFF for ch in s):
        try:
            return s.encode("ascii", "surrogateescape").decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            return s.encode("utf-8", "replace").decode("utf-8", "replace")
    return s


def _dec(s):
    if not s:
        return ""
    if isinstance(s, Header):  # compat32: 인코딩 안 된 8비트 헤더는 unknown-8bit Header 객체로 옴 → 바이트를 UTF-8로
        parts = []
        for b, cs in decode_header(s):
            if isinstance(b, bytes):
                parts.append(b.decode("utf-8" if not cs or cs == "unknown-8bit" else cs, "replace"))
            else:
                parts.append(b)
        return "".join(parts).strip()
    s = _fix_8bit(str(s))
    try:
        return _fix_8bit(str(make_header(decode_header(s)))).strip()
    except Exception:  # noqa: BLE001 - 깨진 인코딩은 원문
        return s.strip()


def _parse_date(s):
    try:
        d = email.utils.parsedate_to_datetime(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d
    except Exception:  # noqa: BLE001
        return None


def parse_header_block(raw: bytes, flags: str = "", folder: str = "INBOX"):
    """FETCH로 받은 헤더 블록 → 메시지 dict. 본문 없음."""
    msg = email.message_from_bytes(raw)
    name, addr = email.utils.parseaddr(_dec(msg.get("From")))
    d = _parse_date(msg.get("Date") or "")
    mid = (msg.get("Message-ID") or "").strip()
    refs = " ".join([msg.get("In-Reply-To") or "", msg.get("References") or ""]).split()
    return {
        "id": hashlib.sha1((mid or (addr + (msg.get("Subject") or "") + (msg.get("Date") or ""))).encode("utf-8")).hexdigest()[:12],
        "message_id": mid,
        "from": addr.lower(), "from_name": name or addr,
        "to": [a.lower() for _, a in email.utils.getaddresses([_dec(msg.get("To") or "")]) if a],
        "subject": _dec(msg.get("Subject")) or "(제목 없음)",
        "date": d.isoformat(timespec="minutes") if d else "",
        "_dt": d,
        "unread": "\\Seen" not in flags,
        "flagged": "\\Flagged" in flags,
        "in_reply_to": refs,
        "list": bool(msg.get("List-Id") or msg.get("List-Unsubscribe")),
        "auto": bool(msg.get("Auto-Submitted") and msg.get("Auto-Submitted").lower() != "no") or (msg.get("Precedence") or "").lower() in ("bulk", "list", "junk"),
        "folder": folder,
    }


def _flags_from_meta(meta: bytes):
    m = re.search(rb"FLAGS \(([^)]*)\)", meta or b"")
    return m.group(1).decode("ascii", "ignore") if m else ""


def parse_fetch_response(data, folder="INBOX"):
    """imaplib fetch 응답(리스트) → 메시지 dict 목록. 항목은 (meta, header_bytes) 튜플과 b')' 조각이 섞여 있다."""
    out = []
    for item in data or []:
        if not isinstance(item, tuple) or len(item) < 2:
            continue
        meta, raw = item[0], item[1]
        if not isinstance(raw, (bytes, bytearray)):
            continue
        try:
            out.append(parse_header_block(bytes(raw), _flags_from_meta(meta), folder))
        except Exception:  # noqa: BLE001 - 한 통이 깨져도 나머지는 살린다
            continue
    return out


def _read_password(cfg):
    pf = cfg.get("password_file")
    if pf:
        p = Path(os.path.expanduser(pf))
        if not p.is_file():
            raise FileNotFoundError(f"비밀번호 파일 없음: {p}")
        return p.read_text(encoding="utf-8").strip().splitlines()[0].strip()
    if os.environ.get("SECOND_BRAIN_MAIL_PASSWORD"):
        return os.environ["SECOND_BRAIN_MAIL_PASSWORD"]
    raise ValueError("mail.password_file(앱 비밀번호 파일)이 필요해요")


def fetch_mail(cfg: dict, now=None):
    """IMAP에서 최근 N일 헤더를 읽어 온다(읽기 전용). 반환 {inbox: [...], sent: [...]}."""
    now = now or datetime.now(timezone.utc)
    days = int(cfg.get("days") or 7)
    since = (now - timedelta(days=days)).strftime("%d-%b-%Y")
    maxn = int(cfg.get("max") or 120)
    pw = _read_password(cfg)
    ctx = ssl.create_default_context()
    M = imaplib.IMAP4_SSL(cfg.get("host") or "imap.gmail.com", int(cfg.get("port") or 993), ssl_context=ctx, timeout=25)
    try:
        M.login(cfg["user"], pw)
        out = {"inbox": [], "sent": []}
        for key, folder in (("inbox", cfg.get("folder") or "INBOX"), ("sent", cfg.get("sent_folder") or "")):
            if not folder:
                continue
            typ, _ = M.select(f'"{folder}"', readonly=True)
            if typ != "OK":
                continue
            typ, ids = M.search(None, "SINCE", since)
            if typ != "OK":
                continue
            nums = ids[0].split()[-maxn:]
            if not nums:
                continue
            typ, data = M.fetch(b",".join(nums), f"(FLAGS BODY.PEEK[HEADER.FIELDS ({HEADER_FIELDS})])")
            if typ == "OK":
                out[key] = parse_fetch_response(data, folder)
        return out
    finally:
        try:
            M.logout()
        except Exception:  # noqa: BLE001
            pass


def triage(inbox, sent, my_addrs, now=None):
    """세 묶음으로. reply=사람이 보낸 안 읽은/깃발, waiting=내가 보냈는데 답 없음, info=뉴스레터·자동."""
    now = now or datetime.now(timezone.utc)
    my = {a.lower() for a in my_addrs if a}
    replied_to = set()
    for m in inbox:
        for r in m.get("in_reply_to") or []:
            replied_to.add(r)
    reply, info, other = [], [], []
    for m in inbox:
        if m["from"] in my:
            continue
        is_info = m["list"] or m["auto"] or AUTO_FROM_RE.search(m["from"]) or INFO_SUBJECT_RE.search(m["subject"])
        m = dict(m)
        m["booking"] = bool(BOOKING_RE.search(m["subject"]))
        m["age_hours"] = round((now - m["_dt"]).total_seconds() / 3600, 1) if m.get("_dt") else None
        if is_info:
            info.append(m)
        elif m["unread"] or m["flagged"]:
            reply.append(m)
        else:
            other.append(m)
    waiting = []
    for s in sent:
        if s["from"] and s["from"] not in my:
            continue
        if s["message_id"] and s["message_id"] in replied_to:
            continue
        s = dict(s)
        s["age_hours"] = round((now - s["_dt"]).total_seconds() / 3600, 1) if s.get("_dt") else None
        s["to_name"] = ", ".join(s.get("to") or [])[:60]
        waiting.append(s)
    key = lambda m: (not m.get("flagged"), -(m.get("age_hours") or 0) * -1)  # 깃발 먼저, 최근 순
    reply.sort(key=lambda m: (not m["flagged"], m.get("date") or ""), reverse=False)
    reply.sort(key=lambda m: m.get("date") or "", reverse=True)
    reply.sort(key=lambda m: not m["flagged"])
    waiting.sort(key=lambda m: m.get("date") or "")
    info.sort(key=lambda m: m.get("date") or "", reverse=True)
    def strip(m):
        return {k: v for k, v in m.items() if not k.startswith("_") and k not in ("in_reply_to", "to")}
    return {"reply": [strip(m) for m in reply], "waiting": [strip(m) for m in waiting], "info": [strip(m) for m in info[:30]],
            "read_recent": len(other), "counts": {"reply": len(reply), "waiting": len(waiting), "info": len(info)},
            "bookings": [strip(m) for m in inbox if BOOKING_RE.search(m["subject"]) and m["from"] not in my][:5]}


def collect_mail(cfg_root: dict, now=None, force=False):
    """설정을 읽어 메일 상태를 만든다. 미설정이면 configured=False. 실패는 status에 담고 캐시를 폴백으로."""
    mcfg = cfg_root.get("mail") if isinstance(cfg_root, dict) else None
    now = now or datetime.now(timezone.utc)
    if not isinstance(mcfg, dict) or not mcfg.get("user"):
        return {"configured": False, "status": "unconfigured", "counts": {"reply": 0, "waiting": 0, "info": 0},
                "hint": "메일을 붙이려면 config.json에 mail {host, user, password_file(앱 비밀번호 파일)} 을 적고 `brain.py mail test`. 읽기 전용, 헤더만 읽어요."}
    cp = cache_path()
    if not force and cp.is_file() and now.timestamp() - cp.stat().st_mtime < CACHE_SEC:
        try:
            d = json.loads(cp.read_text(encoding="utf-8"))
            d["cached"] = True
            return d
        except ValueError:
            pass
    try:
        raw = fetch_mail(mcfg, now)
        res = triage(raw["inbox"], raw["sent"], [mcfg["user"]] + list(mcfg.get("aliases") or []), now)
        res.update({"configured": True, "status": "ok", "fetched_at": now.isoformat(timespec="minutes"), "user": mcfg["user"],
                    "web": mcfg.get("web") or ("https://mail.google.com/mail/u/0/" if "gmail" in (mcfg.get("host") or "") else "")})
        cp.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
        return res
    except Exception as e:  # noqa: BLE001 - 네트워크·인증 실패는 상태로
        err = f"{type(e).__name__}: {e}"
        if cp.is_file():
            try:
                d = json.loads(cp.read_text(encoding="utf-8"))
                d.update({"status": "stale", "error": err, "cached": True})
                return d
            except ValueError:
                pass
        return {"configured": True, "status": "fail", "error": err, "counts": {"reply": 0, "waiting": 0, "info": 0}, "user": mcfg.get("user")}


def mail_sentence(m):
    """보고 문장 한 개(없으면 None)."""
    if not m or not m.get("configured") or m.get("status") not in ("ok", "stale"):
        return None
    c = m.get("counts") or {}
    if not c.get("reply") and not c.get("waiting"):
        return "답장할 메일은 없어요."
    parts = []
    if c.get("reply"):
        top = m["reply"][0]
        parts.append(f"답장할 메일 {c['reply']}통" + (f", 먼저 {top['from_name']}의 「{top['subject'][:24]}」" if top else ""))
    if c.get("waiting"):
        parts.append(f"기다리는 답 {c['waiting']}통")
    return ", ".join(parts) + "."


def mail_kakao(m, limit=70):
    if not m or not m.get("configured") or m.get("status") not in ("ok", "stale"):
        return ""
    c = m.get("counts") or {}
    if not c.get("reply") and not c.get("waiting"):
        return ""
    return (f"메일 답장 {c.get('reply', 0)}" + (f"·대기 {c['waiting']}" if c.get("waiting") else ""))[:limit]


def mail_human(m):
    if not m.get("configured"):
        return "메일 미연결. " + m.get("hint", "")
    if m.get("status") == "fail":
        return f"메일 읽기 실패: {m.get('error')}"
    lines = [f"메일 ({m.get('user')}, {m.get('status')}{', 캐시' if m.get('cached') else ''})"]
    for k, label in (("reply", "답장 필요"), ("waiting", "기다리는 답"), ("info", "알아두면 됨")):
        items = m.get(k) or []
        lines.append(f"[{label} {len(items)}]")
        for x in items[:8]:
            who = x.get("to_name") if k == "waiting" else x.get("from_name")
            lines.append(f"  {'★ ' if x.get('flagged') else ''}{(x.get('date') or '')[5:16]}  {who[:18]:<18}  {x['subject'][:50]}")
    return "\n".join(lines)
