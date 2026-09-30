#!/usr/bin/env python3
"""세컨드브레인 대시보드 성능 벤치마크 (Python 3.9+ 표준 라이브러리만 사용).

임시 HOME 아래 합성 볼트(노트 N개 + 위젯 10개)를 만들고, 대시보드가 실제로 부르는
주요 조회 함수들을 각각 1회 warm-up 뒤 5회 반복 실행해 중앙값(ms)을 표로 찍는다.
--out에 같은 결과를 JSON으로도 남긴다.

사용법:
    python3 scripts/bench.py --notes 1000 --out bench.json
    python3 scripts/bench.py --notes 300 --out bench-300.json

주의: 이 스크립트는 실제 ~/brain, ~/.config, ~/.cache를 건드리지 않는다 — 실행 내내
HOME을 tempfile.TemporaryDirectory()로 바꿔 쓰고 끝나면 통째로 지운다.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import brain  # noqa: E402


TAGS_POOL = ["ai", "인프라", "보안", "devops", "쿠버네티스", "aws", "비용", "최적화",
             "자동화", "네트워크", "데이터베이스", "모니터링", "장애대응", "리뷰", "회고"]
PROJECT_TITLES = [f"프로젝트 {i:02d} 인프라 이전" if i % 4 == 0 else
                  f"프로젝트 {i:02d} 비용 최적화" if i % 4 == 1 else
                  f"프로젝트 {i:02d} 보안 강화" if i % 4 == 2 else
                  f"프로젝트 {i:02d} 관측성 구축" for i in range(40)]
PEOPLE_NAMES = [f"팀원{i:02d}" for i in range(20)]

# "인프라 비용" 관련 검색이 실제로 히트를 만들도록 의도적으로 섞어 넣는 문장들.
INFRA_COST_SENTENCES = [
    "이번 달 인프라 비용이 예상보다 12% 늘어서 원인을 뜯어봤다.",
    "쿠버네티스 클러스터 오토스케일링 설정을 손봐서 인프라 비용을 줄였다.",
    "클라우드 인프라 비용 대시보드를 매주 확인하는 루틴을 만들었다.",
    "인프라 비용 절감을 위해 유휴 인스턴스를 정리했다.",
    "로그 저장 정책을 바꿔서 관측성 파이프라인의 인프라 비용을 낮췄다.",
]
GENERIC_SENTENCES = [
    "장애 대응 회고에서 알람 임계값을 다시 잡기로 했다.",
    "보안 점검에서 오래된 토큰을 여러 개 발견해서 회수했다.",
    "새 모니터링 대시보드에 주요 지표 5개를 올렸다.",
    "네트워크 지연이 특정 리전에서만 튀는 걸 확인했다.",
    "데이터베이스 인덱스를 정리해서 조회 속도를 개선했다.",
    "자동화 스크립트가 새벽에 두 번 실패해서 재시도 로직을 추가했다.",
    "리뷰 과정에서 놓친 엣지 케이스를 하나 더 찾았다.",
    "온보딩 문서를 다시 정리해서 새 팀원이 보기 쉽게 했다.",
]
GENERIC_TYPES = ["note", "idea", "source", "meeting"]


def _rand_tags(rng, k=None):
    k = k or rng.randint(2, 4)
    return sorted(rng.sample(TAGS_POOL, k))


def _dump(meta, body):
    return brain.dump_frontmatter(meta, body)


def _write(path, meta, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_dump(meta, body), encoding="utf-8")


def _paragraph(rng, infra_bias=False):
    pool = INFRA_COST_SENTENCES if infra_bias else GENERIC_SENTENCES
    n = rng.randint(3, 6)
    return " ".join(rng.choice(pool) for _ in range(n))


def build_vault(vault: Path, n_notes: int, today: date, seed: int = 20260930):
    """합성 볼트 생성. 반환: 생성된 (type -> stem 목록) 요약 dict."""
    rng = random.Random(seed)
    vault.mkdir(parents=True, exist_ok=True)
    (vault / "BRAIN.md").write_text("# BRAIN — 벤치용 볼트\n", encoding="utf-8")

    stems = {"person": [], "project": [], "decision": [], "event": [], "journal": [], "generic": []}

    # 1) 사람 20
    for i, name in enumerate(PEOPLE_NAMES):
        created = (today - timedelta(days=rng.randint(30, 300))).isoformat()
        stem = f"person-{i:03d}"
        meta = {"title": name, "type": "person", "created": created, "tags": _rand_tags(rng, 2)}
        if rng.random() < 0.6:
            meta["email"] = f"{stem}@example.com"
        body = f"# {name}\n\n## 맥락\n\n{name}와의 최근 협업 맥락 메모.\n"
        _write(vault / "people" / f"{stem}.md", meta, body)
        stems["person"].append(stem)

    # 2) 프로젝트 40
    for i, title in enumerate(PROJECT_TITLES):
        created = (today - timedelta(days=rng.randint(60, 300))).isoformat()
        stem = f"project-{i:03d}"
        meta = {"title": title, "type": "project", "created": created, "tags": _rand_tags(rng)}
        body = f"# {title}\n\n## 목표\n\n{_paragraph(rng, infra_bias=(i % 3 == 0))}\n\n## 메모\n"
        _write(vault / "projects" / f"{stem}.md", meta, body)
        stems["project"].append(stem)

    # 3) 결정 30 (open/decided/superseded 섞기, open 일부는 되돌아볼 날짜가 지남)
    for i in range(30):
        created = (today - timedelta(days=rng.randint(1, 180))).isoformat()
        status = ["open", "decided", "superseded"][i % 3]
        stem = f"decision-{i:03d}"
        title = f"결정 {i:03d}: " + rng.choice(["로그 파이프라인 교체", "쿠버네티스 버전 업그레이드",
                                                "비용 알람 임계값 조정", "보안 정책 강화", "관측성 도구 통일"])
        meta = {"title": title, "type": "decision", "created": created, "tags": _rand_tags(rng),
                "status": status, "project": rng.choice(PROJECT_TITLES)}
        if status == "open":
            revisit_delta = rng.randint(-20, 30)
            meta["revisit"] = (today + timedelta(days=revisit_delta)).isoformat()
        target = rng.choice(stems["project"] + stems["person"]) if (stems["project"] or stems["person"]) else None
        link = f"\n\n관련: [[{target}]]\n" if target else ""
        body = (f"# {title}\n\n## 상황\n\n{_paragraph(rng, infra_bias=True)}\n\n"
                f"## 고려한 선택지\n\n- A\n- B\n\n## 결정\n\nB로 간다.\n\n"
                f"## 이유\n\n{_paragraph(rng)}\n\n## 되돌아볼 날짜\n{link}")
        _write(vault / "decisions" / f"{stem}.md", meta, body)
        stems["decision"].append(stem)

    # 4) 이벤트 50
    for i in range(50):
        created = (today - timedelta(days=rng.randint(-14, 200))).isoformat()
        y = created[:4]
        stem = f"{created[:10]}-event-{i:03d}"
        title = rng.choice(["팀 주간회의", "고객사 미팅", "온보딩 세션", "장애 회고", "1:1 미팅"])
        meta = {"title": title, "type": "event", "created": created, "tags": _rand_tags(rng, 2)}
        body = f"# {title}\n\n## 준비\n\n## 동선\n\n## 메모\n\n{_paragraph(rng)}\n"
        _write(vault / "events" / y / f"{stem}.md", meta, body)
        stems["event"].append(stem)

    # 5) 일지 60 (그중 9개는 주간 회고 -weekly, 최근 14일 안에 최소 1개)
    weekly_offsets = {3, 10, 17, 24, 31, 38, 45, 52, 59}
    journal_days = [today - timedelta(days=k) for k in range(60)]
    for k, day in enumerate(journal_days):
        created = day.isoformat()
        y = created[:4]
        if k in weekly_offsets:
            stem = f"{created}-weekly"
            title = f"{created} 주간 회고"
            week_lines = [rng.choice(GENERIC_SENTENCES + INFRA_COST_SENTENCES) for _ in range(3)]
            patterns = [rng.choice(GENERIC_SENTENCES) for _ in range(2)]
            questions = [f"질문: {rng.choice(GENERIC_SENTENCES)}" for _ in range(3)]
            next_week = [f"다음 주: {rng.choice(GENERIC_SENTENCES)}" for _ in range(2)]
            meta = {"title": title, "type": "journal", "created": created, "tags": ["회고"],
                    "journal_kind": "weekly", "journal_date": created,
                    "summary": "주간 회고: " + week_lines[0][:60]}
            body = (f"# {title}\n\n## 이번 주\n" + "".join(f"- {l}\n" for l in week_lines)
                    + "\n## 눈에 띄는 것\n" + "".join(f"- {l}\n" for l in patterns)
                    + "\n## 되돌아볼 질문\n" + "".join(f"- [ ] {q}\n" for q in questions)
                    + "\n## 다음 주\n" + "".join(f"- [ ] {q}\n" for q in next_week))
        else:
            stem = created
            title = f"{created} 일지"
            lines = [rng.choice(GENERIC_SENTENCES + INFRA_COST_SENTENCES) for _ in range(4)]
            meta = {"title": title, "type": "journal", "created": created, "tags": ["일지"],
                    "journal_date": created, "summary": lines[0][:60]}
            body = f"# {title}\n\n## 오늘\n" + "".join(f"- {l}\n" for l in lines) + "\n## 잘한 것\n\n## 내일 첫 일\n"
        _write(vault / "journal" / y / f"{stem}.md", meta, body)
        stems["journal"].append(stem)

    # 6) 나머지 일반 노트 (note/idea/source/meeting 혼합) — 태그·위키링크·요약 포함
    fixed = len(stems["person"]) + len(stems["project"]) + len(stems["decision"]) + len(stems["event"]) + len(stems["journal"])
    n_generic = max(0, n_notes - fixed)
    link_pool = stems["project"] + stems["person"]
    for i in range(n_generic):
        created = (today - timedelta(days=rng.randint(0, 240))).isoformat()
        y, m = created[:4], created[5:7]
        ntype = GENERIC_TYPES[i % len(GENERIC_TYPES)]
        infra_bias = (i % 7 == 0)  # ~15%는 "인프라 비용" 검색이 히트하도록
        title = (f"인프라 비용 점검 노트 {i:04d}" if infra_bias else f"작업 노트 {i:04d}")
        stem = f"note-{i:05d}"
        tags = _rand_tags(rng)
        project = rng.choice(PROJECT_TITLES) if rng.random() < 0.5 else None
        summary = _paragraph(rng, infra_bias)[:120]
        body_paras = [_paragraph(rng, infra_bias) for _ in range(rng.randint(2, 4))]
        n_links = rng.randint(0, 3)
        link_targets = [t for t in (link_pool + stems["generic"]) if t != stem]
        links = rng.sample(link_targets, min(n_links, len(link_targets))) if link_targets else []
        link_block = ("\n\n관련: " + " ".join(f"[[{t}]]" for t in links) + "\n") if links else "\n"
        meta = {"title": title, "type": ntype, "created": created, "tags": tags, "summary": summary}
        if project:
            meta["project"] = project
        body = f"# {title}\n\n" + "\n\n".join(body_paras) + link_block
        _write(vault / "notes" / y / m / f"{stem}.md", meta, body)
        stems["generic"].append(stem)

    return stems


def build_widgets(home: Path, today: date, n_widgets: int = 10, lines_per_day: int = 250, seed: int = 7):
    """widgets.json + 로그 위젯 n_widgets개(각 최근 7일치 날짜 줄) 생성.

    로그를 하루 lines_per_day줄 정도(수십~수백KB)로 채워, widget_history()가 실제로
    파일을 읽고 정규식으로 줄을 스캔하는 비용이 벤치에 드러나게 한다."""
    rng = random.Random(seed)
    logs_dir = home / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    days7 = [today - timedelta(days=k) for k in range(6, -1, -1)]
    widgets = []
    teams = ["운영팀", "생활팀", "콘텐츠팀", "커리어팀"]
    for i in range(n_widgets):
        log_path = logs_dir / f"widget-{i:02d}.log"
        lines = []
        for day in days7:
            fail_today = (i + day.toordinal()) % 5 == 0
            for j in range(lines_per_day):
                if fail_today and j == lines_per_day - 1:
                    lines.append(f"{day.isoformat()} 0{i % 10}:00:{j % 60:02d} Traceback (most recent call last):")
                    lines.append(f"{day.isoformat()} 0{i % 10}:00:{j % 60:02d} HTTPError 500: Internal Server Error")
                else:
                    lines.append(f"{day.isoformat()} 0{i % 10}:00:{j % 60:02d} 작업 {i}-{j} 완료 len: {3 + j % 7}")
        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        widgets.append({
            "id": f"widget-{i:02d}", "title": f"자동화 작업 {i:02d} (매일)", "kind": "log",
            "source": str(log_path), "team": teams[i % len(teams)],
            "status": {"ok_pattern": "len:", "fail_pattern": "Traceback|Error", "stale_minutes": 1560},
            "lines": 3,
        })
    cfgdir = home / ".config" / "second-brain"
    cfgdir.mkdir(parents=True, exist_ok=True)
    (cfgdir / "widgets.json").write_text(
        json.dumps({"allow_commands": False, "allow_run": False, "widgets": widgets}, ensure_ascii=False, indent=2),
        encoding="utf-8")


def setup_env(home: Path, vault: Path):
    os.environ["HOME"] = str(home)
    os.environ["XDG_CACHE_HOME"] = str(home / ".cache")
    os.environ["SECOND_BRAIN_JOBS_DIR"] = str(home / "nojobs")
    os.environ["SECOND_BRAIN_OFFLINE"] = "1"
    os.environ["SECOND_BRAIN_NO_LAUNCHCTL"] = "1"
    os.environ.pop("SECOND_BRAIN_VAULT", None)
    os.environ.pop("SECOND_BRAIN_NO_NOTE_CACHE", None)
    cfgdir = home / ".config" / "second-brain"
    cfgdir.mkdir(parents=True, exist_ok=True)
    (cfgdir / "config.json").write_text(
        json.dumps({"vault": str(vault), "calendar": {"sources": []}}, ensure_ascii=False), encoding="utf-8")


def median_ms(fn, warmup=1, runs=5):
    for _ in range(warmup):
        fn()
    samples = []
    for _ in range(runs):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000.0)
    samples.sort()
    mid = len(samples) // 2
    if len(samples) % 2:
        return samples[mid]
    return (samples[mid - 1] + samples[mid]) / 2.0


def run_bench(vault: Path, n_notes: int, today: date):
    now = datetime.combine(today, datetime.min.time()).replace(hour=9, minute=30).astimezone()
    brain.invalidate_notes_cache()
    widgets = brain.collect_widgets()
    empty_agenda = {"today": [], "upcoming": [], "next": None, "current": [], "conflicts": [], "total": 0}

    cases = []

    def add(name, fn):
        cases.append((name, fn))

    add("load_notes(cold)", lambda: (brain.invalidate_notes_cache(), brain.load_notes(vault))[-1])
    add("load_notes(warm)", lambda: brain.load_notes(vault))
    add("dash_today", lambda: brain.dash_today(vault, today=today, now=now, widgets=widgets, agenda=empty_agenda))
    add("dash_search(자유어)", lambda: brain.dash_search(vault, "인프라 비용", limit=20, today=today))
    add("dash_search(연산자)", lambda: brain.dash_search(vault, "type:decision since:90d", today=today))
    add("dash_summary", lambda: brain.dash_summary(vault, today=today))
    add("dash_report", lambda: brain.dash_report(vault, 7, today, widgets=widgets))
    add("dash_journals", lambda: brain.dash_journals(vault))
    add("dash_people", lambda: brain.dash_people(vault, agenda=empty_agenda, today=today))
    add("dash_timeline(30d)", lambda: brain.dash_timeline(vault, days=30, today=today))
    if hasattr(brain, "dash_graph"):
        add("dash_graph", lambda: brain.dash_graph(vault))
    add("dash_office", lambda: brain.dash_office(widgets, ps_lines=[], cron_lines=[], now=now))
    add("office_kpis", lambda: brain.office_kpis(widgets, today=today))
    add("office_schedule", lambda: brain.office_schedule(widgets, cron_lines=[], today=today, now=now))
    add("lint_vault", lambda: brain.lint_vault(vault, today=today))
    add("build_index", lambda: brain.build_index(vault, today=today))

    results = {}
    for name, fn in cases:
        ms = median_ms(fn)
        results[name] = round(ms, 2)
    return results, len(brain.load_notes(vault))


def print_table(title, results, notes_count):
    print(f"\n== {title} (notes={notes_count}) ==")
    width = max(len(k) for k in results) + 2
    for name, ms in results.items():
        print(f"{name:<{width}} {ms:>9.2f} ms")


def main():
    ap = argparse.ArgumentParser(description="세컨드브레인 대시보드 성능 벤치마크")
    ap.add_argument("--notes", type=int, default=1000, help="합성 볼트에 만들 노트 총수 (기본 1000)")
    ap.add_argument("--out", type=str, default=None, help="결과 JSON을 쓸 경로")
    ap.add_argument("--seed", type=int, default=20260930, help="합성 데이터 시드")
    args = ap.parse_args()

    today = date.today()
    with tempfile.TemporaryDirectory(prefix="second-brain-bench-") as tmp:
        home = Path(tmp)
        vault = home / "brain"
        setup_env(home, vault)
        build_vault(vault, args.notes, today, seed=args.seed)
        build_widgets(home, today)
        brain.invalidate_notes_cache()

        results, notes_count = run_bench(vault, args.notes, today)
        print_table(f"--notes {args.notes}", results, notes_count)

        if args.out:
            out_path = Path(args.out)
            out_path.write_text(json.dumps({
                "notes_requested": args.notes, "notes_actual": notes_count,
                "today": today.isoformat(), "results_ms": results,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"\nJSON 저장: {out_path}")


if __name__ == "__main__":
    main()
