#!/usr/bin/env python3
"""legalize-kr 연동 헬퍼 — 컴플라이언스 AI

로컬 vendor/legalize-kr(법령 Markdown + Git 개정이력)에서
법령 조회 / 특정 조문 추출 / 개정이력 / 개정 diff 를 제공한다.
법제처 MCP가 '실시간 원문 조회'라면, 이 헬퍼는 'Git 기반 개정이력·diff'(P3) 담당.

사용:
  python tools/legalize.py list
  python tools/legalize.py show 개인정보보호법 --jo 21
  python tools/legalize.py show 개인정보보호법 --as-of 2026-07-16   # 그날 시행 중이던 현행본
  python tools/legalize.py history 개인정보보호법
  python tools/legalize.py diff 개인정보보호법 --at 2023-03-14   # 해당 개정 커밋의 변경분
  python tools/legalize.py sync                                  # 원격 최신본으로 즉시 동기화

주의: 기본 show는 최신 '공포'본(HEAD)을 보여준다. 시행일이 미래면 '⚠️ 시행예정본' 경고가 뜨며,
      오늘 현행을 원하면 --as-of <오늘> 을 쓴다. (공포 != 시행)

동기화: 매 실행 시 원격(origin) 최신 여부를 확인하고 뒤처져 있으면 자동으로 맞춘다
      (확인은 하루 1회로 제한, --no-sync 로 생략 가능). legalize-kr은 이력 백필로
      force-push되는 경우가 있어 pull 대신 fetch + reset --hard 를 쓴다.
"""
from __future__ import annotations
import argparse
import datetime
import re
import subprocess
import sys
import time
from pathlib import Path

TODAY = datetime.date.today().isoformat()

REPO = Path(__file__).resolve().parent.parent / "vendor" / "legalize-kr"
FETCH_TTL_HOURS = 24  # 원격 확인 최소 간격

# 대상 법률: 친숙한 별칭 → legalize-kr 디렉토리명(공백 제거)
LAWS = {
    "전자금융거래법": "전자금융거래법",
    "정보통신망법": "정보통신망이용촉진및정보보호등에관한법률",
    "신용정보법": "신용정보의이용및보호에관한법률",
    "개인정보보호법": "개인정보보호법",
    "위치정보법": "위치정보의보호및이용등에관한법률",
}
KINDS = ["법률", "시행령", "시행규칙"]  # 시행세칙/고시는 행정규칙 → MCP 담당


def resolve(name: str) -> str:
    """별칭 또는 정식명을 디렉토리명으로 변환."""
    if name in LAWS:
        return LAWS[name]
    if (REPO / "kr" / name).is_dir():
        return name
    # 부분 매칭
    for alias, d in LAWS.items():
        if name in alias or name in d:
            return d
    raise SystemExit(f"❌ 대상 법률 아님: {name}\n   가능: {', '.join(LAWS)}")


def law_path(name: str, kind: str) -> Path:
    p = REPO / "kr" / resolve(name) / f"{kind}.md"
    if not p.exists():
        avail = [f.stem for f in (REPO / "kr" / resolve(name)).glob("*.md")]
        raise SystemExit(f"❌ 없음: {name}/{kind}.md (보유: {', '.join(avail)})")
    return p


def git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(REPO), *args],
        capture_output=True, text=True, check=True,
    ).stdout


def _git_try(*args: str, timeout: int | None = None) -> subprocess.CompletedProcess:
    """실패해도 예외를 던지지 않는 git 실행 (동기화 전용)."""
    return subprocess.run(
        ["git", "-C", str(REPO), *args],
        capture_output=True, text=True, timeout=timeout,
    )


def _remote_ref() -> str:
    r = _git_try("symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else "origin/main"


def sync(force: bool = False) -> None:
    """원격 최신본으로 자동 동기화. 오프라인이면 경고 후 로컬본으로 진행.

    force-push(이력 재작성)에도 대응해야 하므로 fetch + reset --hard 를 쓰되,
    vendor 저장소에 로컬 수정이 있으면 건드리지 않는다.
    상태 메시지는 stdout 파싱을 깨지 않도록 stderr로 낸다.
    """
    fetch_head = REPO / ".git" / "FETCH_HEAD"
    if not force and fetch_head.exists():
        if time.time() - fetch_head.stat().st_mtime < FETCH_TTL_HOURS * 3600:
            return  # 최근에 확인함
    try:
        r = _git_try("fetch", "origin", timeout=60)
    except subprocess.TimeoutExpired:
        print("⚠️ legalize-kr fetch 시간 초과 — 로컬본으로 진행", file=sys.stderr)
        return
    if r.returncode != 0:
        print("⚠️ legalize-kr fetch 실패(오프라인?) — 로컬본으로 진행", file=sys.stderr)
        return
    remote = _remote_ref()
    local = _git_try("rev-parse", "HEAD").stdout.strip()
    target = _git_try("rev-parse", remote).stdout.strip()
    if not target or local == target:
        return
    if _git_try("status", "--porcelain").stdout.strip():
        print("⚠️ vendor/legalize-kr에 로컬 수정이 있어 자동 동기화 건너뜀 "
              "(수정을 치우고 `legalize.py sync` 실행)", file=sys.stderr)
        return
    old = _git_try("log", "-1", "--format=%h %cs").stdout.strip()
    r = _git_try("reset", "--hard", remote)
    if r.returncode == 0:
        new = _git_try("log", "-1", "--format=%h %cs").stdout.strip()
        print(f"🔄 legalize-kr 최신본 동기화: {old} → {new}", file=sys.stderr)
    else:
        print(f"⚠️ legalize-kr 동기화 실패: {r.stderr.strip()}", file=sys.stderr)


def frontmatter(text: str) -> dict:
    m = re.match(r"^---\n(.*?)\n---", text, re.S)
    fm = {}
    if m:
        for line in m.group(1).splitlines():
            if ":" in line and not line.startswith((" ", "-")):
                k, _, v = line.partition(":")
                fm[k.strip()] = v.strip().strip("'\"")
    return fm


def norm_jo(jo: str) -> str:
    """조문 번호 정규화: '21'→'제21조', '37의2'→'제37조의2', '제37조의2'→그대로."""
    jo = jo.strip()
    if not jo.startswith("제"):
        jo = "제" + jo
    if "조" not in jo:  # '제21' / '제37의2' 처럼 '조' 누락 시 삽입
        m = re.match(r"제(\d+)(의\d+)?$", jo)
        if m:
            jo = f"제{m.group(1)}조{m.group(2) or ''}"
    return jo


def extract_article(text: str, jo: str) -> str | None:
    """'제21조' 형태 조문 하나를 헤딩~다음 조문 직전까지 추출."""
    jo = norm_jo(jo)
    # 헤딩 라인 매칭 (##### 제21조 ...)
    pat = re.compile(rf"^#+\s*{re.escape(jo)}\s*[\(（ ]", re.M)
    m = pat.search(text)
    if not m:
        return None
    start = m.start()
    # 다음 '제N조' 헤딩 또는 장/절 헤딩까지
    nxt = re.compile(r"^#+\s*(제\d+조|제\d+장|제\d+절|부칙)", re.M)
    m2 = nxt.search(text, m.end())
    return text[start: m2.start() if m2 else len(text)].strip()


def extract_all_articles(text: str) -> dict[str, str]:
    """조문 dict {제N조: 본문} 추출 (신설/삭제/개정 비교용)."""
    heads = list(re.finditer(r"^#+\s*(제\d+조(?:의\d+)?)\s*[\(（ ]", text, re.M))
    out = {}
    for i, h in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        out[h.group(1)] = text[h.start():end].strip()
    return out


def _commit_at(rel: str, at: str | None) -> tuple[str, str]:
    """(commit, 공포일자) 반환. at=None이면 최신 개정."""
    if at:
        hs = git("log", "--format=%H|%ci", f"--since={at} 00:00",
                 f"--until={at} 23:59", "--", rel).split()
        if not hs:
            raise SystemExit(f"❌ {at}자 개정 커밋 없음. history로 날짜 확인.")
        h, date = hs[0].split("|")[0], at
    else:
        line = git("log", "--format=%H|%ci", "-1", "--", rel).strip()
        h, date = line.split("|")[0], line.split("|")[1][:10]
    return h, date


def cmd_analyze(a):
    """개정 1건을 조문별 신설/삭제/개정으로 구조화 (P3 LLM 입력)."""
    p = law_path(a.name, a.kind)
    rel = p.relative_to(REPO).as_posix()
    commit, date = _commit_at(rel, a.at)
    new = git("show", f"{commit}:{rel}")
    try:
        old = git("show", f"{commit}~1:{rel}")
    except subprocess.CalledProcessError:
        old = ""  # 최초 제정
    A_old, A_new = extract_all_articles(old), extract_all_articles(new)
    added = [k for k in A_new if k not in A_old]
    removed = [k for k in A_old if k not in A_new]
    changed = [k for k in A_new if k in A_old and A_new[k] != A_old[k]]

    print(f"=== 개정 분석: {a.name} ({a.kind}) — {date} 개정 (커밋 {commit[:8]}) ===")
    print(f"신설 {len(added)} · 삭제 {len(removed)} · 개정 {len(changed)}\n")
    if added:
        print(f"■ 신설 조문: {', '.join(added)}")
    if removed:
        print(f"■ 삭제 조문: {', '.join(removed)}")
    if changed:
        print(f"■ 개정 조문: {', '.join(changed)}")
    if a.detail and changed:
        print("\n--- 개정 조문 변경 상세 (조항 단위) ---")
        for k in changed[: a.detail]:
            old_paras = set(re.findall(r"\*\*[①-⑳]\*\*.*", A_old[k]))
            new_paras = re.findall(r"\*\*[①-⑳]\*\*.*", A_new[k])
            print(f"\n[{k}]")
            for para in new_paras:
                if para not in old_paras:
                    print(f"  + {para[:120]}")
    print("\n※ 위 구조를 LLM이 '변경 요약 + 영향 통제항목 + 대응 체크리스트'로 가공(P3).")


def cmd_list(_):
    print("=== 대상 법률 (legalize-kr 보유) ===")
    for alias, d in LAWS.items():
        kinds = [f.stem for f in sorted((REPO / "kr" / d).glob("*.md"))]
        fm = frontmatter((REPO / "kr" / d / "법률.md").read_text(encoding="utf-8"))
        print(f"  {alias:10s} → {d}")
        print(f"     구분: {', '.join(kinds)} | 시행일: {fm.get('시행일자','?')} | 상태: {fm.get('상태','?')}")


def _versions(rel: str) -> list[dict]:
    """법령 파일의 모든 공포본을 (commit, 공포일, 시행일)로. 최신 공포순."""
    out = []
    for line in git("log", "--format=%H", "--", rel).split():
        try:
            fm = frontmatter(git("show", f"{line}:{rel}"))
        except subprocess.CalledProcessError:
            continue
        out.append({"commit": line, "공포": fm.get("공포일자", ""), "시행": fm.get("시행일자", "")})
    return out


def _inforce_commit(rel: str, as_of: str) -> dict | None:
    """as_of 날짜에 실제 시행 중이던 버전(시행일 <= as_of 중 최신 시행일)."""
    cands = [v for v in _versions(rel) if v["시행"] and v["시행"] <= as_of]
    return max(cands, key=lambda v: v["시행"]) if cands else None


def cmd_show(a):
    p = law_path(a.name, a.kind)
    rel = p.relative_to(REPO).as_posix()
    banner = ""
    if a.as_of:  # 그 날짜에 시행 중이던 버전 선택
        v = _inforce_commit(rel, a.as_of)
        if not v:
            raise SystemExit(f"❌ {a.as_of} 기준 시행본을 찾지 못함.")
        text = git("show", f"{v['commit']}:{rel}")
        banner = f"  📅 as-of {a.as_of} 시행본 (공포 {v['공포']})\n"
    else:
        text = p.read_text(encoding="utf-8")
    fm = frontmatter(text)
    효 = fm.get("시행일자", "?")
    # ⚠️ 최신 공포본이 아직 시행 전(시행일 > 오늘)이면 경고
    if not a.as_of and 효 != "?" and 효 > TODAY:
        inforce = _inforce_commit(rel, TODAY)
        eff = inforce["시행"] if inforce else "미상"
        banner = (f"  ⚠️ 시행예정본: 시행일 {효} > 오늘 {TODAY} (아직 현행 아님)\n"
                  f"     오늘 현행: 시행 {eff}본 — 현행 조회는 `--as-of {TODAY}` 사용\n")
    print(f"# {fm.get('제목', a.name)} ({a.kind})  시행 {효} / 공포 {fm.get('공포일자','?')}")
    if banner:
        print(banner, end="")
    print(f"  출처: {fm.get('출처','')}\n")
    if a.jo:
        art = extract_article(text, a.jo)
        print(art if art else f"⚠️ {norm_jo(a.jo)}를 찾지 못함"
              + (" (해당 시점 미시행/미신설일 수 있음)" if a.as_of else ""))
    else:
        body = re.sub(r"^---\n.*?\n---\n", "", text, flags=re.S)
        print(body[: a.limit] + ("\n…(생략)" if len(body) > a.limit else ""))


def cmd_sync(_):
    sync(force=True)
    head = _git_try("log", "-1", "--format=%h %cs %s").stdout.strip()
    print(f"현재 legalize-kr HEAD: {head}")


def cmd_history(a):
    p = law_path(a.name, a.kind)
    rel = p.relative_to(REPO).as_posix()
    log = git("log", "--format=%h | %ci | %s", "--", str(rel))
    print(f"=== 개정이력: {a.name} ({a.kind}) ===")
    print("커밋   | 공포일자                  | 내용")
    print(log if log.strip() else "(이력 없음)")


def cmd_diff(a):
    p = law_path(a.name, a.kind)
    rel = p.relative_to(REPO).as_posix()
    if a.at:  # 특정 개정일 커밋의 변경분
        h = git("log", "--format=%H", f"--since={a.at} 00:00", f"--until={a.at} 23:59",
                "--", rel).split()
        if not h:
            raise SystemExit(f"❌ {a.at}자 개정 커밋 없음. history로 날짜 확인.")
        commit = h[0]
        print(f"=== {a.name} ({a.kind}) — {a.at} 개정 diff (커밋 {commit[:8]}) ===\n")
        print(git("show", "--format=%ci %s", commit, "--", rel))
    else:  # 최근 두 개정 비교
        hashes = git("log", "--format=%H", "-2", "--", rel).split()
        if len(hashes) < 2:
            raise SystemExit("❌ 비교할 개정이 2개 미만.")
        print(f"=== {a.name} ({a.kind}) — 직전 개정 대비 최신 diff ===\n")
        print(git("diff", hashes[1], hashes[0], "--", rel))


def main():
    if not REPO.exists():
        raise SystemExit(f"❌ vendor/legalize-kr 없음. 먼저 clone 필요:\n"
                         f"   git clone https://github.com/legalize-kr/legalize-kr {REPO}")
    ap = argparse.ArgumentParser(description="legalize-kr 연동 헬퍼")
    ap.add_argument("--no-sync", action="store_true",
                    help="원격 최신 여부 확인 생략 (오프라인/속도 우선 시)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="대상 법률 목록").set_defaults(func=cmd_list)

    sub.add_parser("sync", help="원격 최신본으로 즉시 동기화").set_defaults(func=cmd_sync)

    s = sub.add_parser("show", help="법령/조문 조회")
    s.add_argument("name"); s.add_argument("--kind", default="법률", choices=KINDS)
    s.add_argument("--jo", help="조문 번호 (예: 21, 제21조)")
    s.add_argument("--as-of", dest="as_of", metavar="YYYY-MM-DD",
                   help="그 날짜에 시행 중이던 버전 조회 (기본: 최신 공포본, 시행 전이면 경고)")
    s.add_argument("--limit", type=int, default=3000)
    s.set_defaults(func=cmd_show)

    s = sub.add_parser("history", help="개정이력")
    s.add_argument("name"); s.add_argument("--kind", default="법률", choices=KINDS)
    s.set_defaults(func=cmd_history)

    s = sub.add_parser("diff", help="개정 diff")
    s.add_argument("name"); s.add_argument("--kind", default="법률", choices=KINDS)
    s.add_argument("--at", help="특정 개정일 YYYY-MM-DD")
    s.set_defaults(func=cmd_diff)

    s = sub.add_parser("analyze", help="개정 조문별 신설/삭제/개정 구조화(P3)")
    s.add_argument("name"); s.add_argument("--kind", default="법률", choices=KINDS)
    s.add_argument("--at", help="특정 개정일 YYYY-MM-DD (기본: 최신 개정)")
    s.add_argument("--detail", type=int, default=0, help="개정 조문 상세 N건 표시")
    s.set_defaults(func=cmd_analyze)

    a = ap.parse_args()
    if a.cmd != "sync" and not a.no_sync:
        sync()
    a.func(a)


if __name__ == "__main__":
    main()
