#!/usr/bin/env python3
"""변환 md의 한 쪽을 교정본으로 바꾼다 — 표 구조가 틀어진 쪽(kb-table-verify 스킬이 페이지 이미지로 대조)용.

교정은 이 프로젝트에서 LLM이 만든 텍스트가 지식베이스에 들어가는 유일한 지점이라, 반영 전에 검사한다:
  - 교정본의 모든 단어가 **원본 PDF 그 쪽 텍스트(pypdf)에 있어야** 한다(띄어쓰기 무시).
    원본에 없는 글자·숫자가 하나라도 있으면 거부 — 표 구조(행·열 배치, 병합 셀 값 반복)만 고칠 수 있다.
  - 원본 쪽 단어 중 교정본에 없는 것은 참고로 출력(머리글·쪽번호 등은 원래 md에도 없다).

교정본은 <문서>.fixes/p<쪽>.md에 출처와 함께 보관되고, pdf2md.py가 다시 변환할 때도 자동으로 다시 적용된다.

사용:
  python tools/kb_fix.py data/kb_src/안내서.md --page 96 --file 교정본.md --note "Claude 시각 대조: 묶음 셀 '저장 시' 위치 교정"
  python tools/kb_fix.py data/kb_src/안내서.md --page 96 --file 교정본.md --check   # 검사만
  → 이후 python tools/rag.py ingest data/kb_src/안내서.md --collection <이름> --append
"""
from __future__ import annotations
import argparse
import datetime
import re
import sys
from pathlib import Path

WORD = re.compile(r"[가-힣A-Za-z0-9]+")
CORR_MARK = "<!-- corrected:"
PAGE_SPLIT = re.compile(r"^(<!-- p\.(\d+) -->)$", re.M)


def _raw_page(pdf: Path, page: int) -> str:
    from pypdf import PdfReader
    reader = PdfReader(str(pdf))
    if not 1 <= page <= len(reader.pages):
        raise SystemExit(f"❌ 원본 PDF에 p.{page} 없음")
    return reader.pages[page - 1].extract_text() or ""


def validate(fix: str, raw: str) -> tuple[list[str], list[str]]:
    """(원본에 없는 단어, 원본에 있는데 교정본에 없는 단어). 띄어쓰기·줄바꿈 차이는 무시."""
    raw_flat = re.sub(r"\s+", "", raw)
    fix_body = re.sub(r"<!--.*?-->", "", fix, flags=re.S)
    invented = sorted({w for w in WORD.findall(fix_body) if w not in raw_flat})
    fix_flat = re.sub(r"\s+", "", fix_body)
    dropped = sorted({w for w in WORD.findall(raw) if len(w) > 1 and w not in fix_flat})
    return invented, dropped


def split_pages(md: str) -> tuple[str, list[list]]:
    """md → (머리말, [[쪽, 쪽표시줄, 본문], ...])"""
    parts = PAGE_SPLIT.split(md)
    head, pages = parts[0], []
    for i in range(1, len(parts), 3):
        pages.append([int(parts[i + 1]), parts[i], parts[i + 2]])
    return head, pages


def join_pages(head: str, pages: list[list]) -> str:
    return head + "".join(mark + body for _, mark, body in pages)


def page_body(fix: str, note: str) -> str:
    return f"\n{CORR_MARK} {note} -->\n\n{fix.strip()}\n\n"


def load_fix(path: Path) -> tuple[str, str]:
    """보관된 교정본 → (note, 본문)."""
    t = path.read_text(encoding="utf-8")
    m = re.match(r"<!-- kb_fix: (.*?) -->\n", t)
    return (m.group(1) if m else path.name), (t[m.end():] if m else t)


def apply_fixes(md: str, pdf: Path, fixes_dir: Path) -> tuple[str, list[int]]:
    """fixes_dir의 교정본을 md에 적용(다시 검사). pdf2md가 재변환 후 호출."""
    if not fixes_dir.is_dir():
        return md, []
    head, pages = split_pages(md)
    by_no = {p[0]: p for p in pages}
    applied = []
    for f in sorted(fixes_dir.glob("p*.md")):
        n = int(re.sub(r"\D", "", f.stem))
        note, fix = load_fix(f)
        invented, _ = validate(fix, _raw_page(pdf, n))
        if invented:
            print(f"   ❌ 교정본 {f.name} 적용 안 함 — 원본에 없는 단어 {invented[:10]}")
            continue
        if n in by_no:
            by_no[n][2] = page_body(fix, note)
            applied.append(n)
    return join_pages(head, pages), applied


def main():
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="변환 md의 한 쪽을 검증된 교정본으로 교체")
    ap.add_argument("md", help="pdf2md.py가 만든 md")
    ap.add_argument("--page", type=int, required=True)
    ap.add_argument("--file", required=True, help="교정본(그 쪽 전체 md, 쪽 표시 없이)")
    ap.add_argument("--note", default="Claude 시각 대조 교정", help="교정 사유(출처 표시에 남음)")
    ap.add_argument("--check", action="store_true", help="검사만 하고 반영하지 않음")
    a = ap.parse_args()

    md_path = Path(a.md)
    pdf = md_path.with_suffix(".pdf")
    if not pdf.exists():
        raise SystemExit(f"❌ 원본 PDF 없음: {pdf} (md와 같은 이름의 PDF가 필요)")
    fix = Path(a.file).read_text(encoding="utf-8")
    invented, dropped = validate(fix, _raw_page(pdf, a.page))
    if invented:
        raise SystemExit(f"❌ 거부: 원본 PDF p.{a.page}에 없는 단어 {invented[:20]} — 표 구조만 고치고 문구는 원문 그대로")
    print(f"✅ 검사 통과: 교정본의 모든 단어가 원본 p.{a.page}에 있음")
    if dropped:
        print(f"ℹ️ 원본에 있으나 교정본에 없는 단어(머리글·쪽번호면 정상): {dropped[:20]}")
    if a.check:
        return

    note = f"{a.note} ({datetime.date.today()}, tools/kb_fix.py)"
    fixes = md_path.parent / f"{md_path.stem}.fixes"
    fixes.mkdir(exist_ok=True)
    (fixes / f"p{a.page}.md").write_text(f"<!-- kb_fix: {note} -->\n{fix.strip()}\n", encoding="utf-8")
    head, pages = split_pages(md_path.read_text(encoding="utf-8"))
    hit = [p for p in pages if p[0] == a.page]
    if not hit:
        raise SystemExit(f"❌ md에 p.{a.page} 없음")
    hit[0][2] = page_body(fix, note)
    md_path.write_text(join_pages(head, pages), encoding="utf-8")
    rep_path = md_path.with_suffix(".report.json")     # pdf2md 보고서도 맞춰 둔다(경고 → 교정)
    if rep_path.exists():
        import json
        rep = json.loads(rep_path.read_text(encoding="utf-8"))
        rep.get("table_warnings", {}).pop(str(a.page), None)
        rep["corrected_pages"] = sorted(set(rep.get("corrected_pages", [])) | {a.page})
        rep_path.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"✅ {md_path.name} p.{a.page} 교체 · 교정본 보관: {fixes / f'p{a.page}.md'}\n"
          f"   다음: python tools/rag.py ingest {md_path} --collection <이름> --append")


if __name__ == "__main__":
    main()
