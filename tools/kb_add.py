#!/usr/bin/env python3
"""KB 추가 — URL(가이드/고시 PDF)을 받아 다운로드 후 RAG에 인그레스트.

discover→download→ingest→answer 루프의 'download+ingest' 단계.
URL 발견(웹검색)과 최종 답변은 에이전트(Claude)가 담당.

사용:
  python tools/kb_add.py <url> --collection efin [--name 파일명.pdf]
  python tools/kb_add.py <url1> <url2> --collection fsec

- 공식 출처만 권장(law.go.kr, kisa.or.kr, fsec.or.kr, pipc.go.kr, data.go.kr 등).
  비공식 도메인이면 경고만 하고 진행(판단은 사용자).
- 다운로드는 data/kb_src/ 에 저장(.gitignore). 인그레스트 후 tools/rag.py 로 검색.
- PDF는 tools/pdf2md.py로 **Markdown 변환 후** 인그레스트한다(표·구조 보존, 항목 단위 청킹).
  변환 뒤 원본과 단어 대조 결과를 보여 주고, 누락이 많으면 경고한다. docling이 없거나 --no-md면 PDF를 바로 넣는다.
- 기존 컬렉션에 **추가**한다(rag.py ingest --append). 기존 문서는 유지되고 새 청크만 임베딩,
  같은 문서(확장자 뺀 파일명이 같음)면 그 문서만 교체. 컬렉션이 없으면 새로 만든다.
"""
from __future__ import annotations
import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "kb_src"
WARN_MISSING = 0.01   # 원문 대조 누락률이 이보다 높으면 검수 권장 경고
OFFICIAL = ("law.go.kr", "kisa.or.kr", "fsec.or.kr", "pipc.go.kr",
            "privacy.go.kr", "data.go.kr", "isms-p.or.kr", "mois.go.kr")


def derive_name(url: str, override: str | None) -> str:
    if override:
        return override
    base = unquote(Path(urlparse(url).path).name) or "download.pdf"
    return base if "." in base else base + ".pdf"


def download(url: str, dest: Path) -> None:
    host = urlparse(url).hostname or ""
    if not any(host.endswith(d) for d in OFFICIAL):
        print(f"⚠️ 비공식 도메인: {host} — 출처 신뢰성 직접 확인 필요")
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"⬇️  {url}\n    → {dest}")
    r = subprocess.run(["curl", "-sL", "--max-time", "120", "-o", str(dest), url])
    if r.returncode != 0 or not dest.exists() or dest.stat().st_size == 0:
        raise SystemExit(f"❌ 다운로드 실패: {url}")
    head = dest.read_bytes()[:5]
    kind = "PDF" if head.startswith(b"%PDF") else ("텍스트" if dest.suffix in (".txt", ".md") else "?")
    print(f"    ✅ {dest.stat().st_size//1024} KB ({kind})")
    if kind == "?":
        print("    ⚠️ PDF 시그니처 아님 — HTML 오류페이지일 수 있음. 확인 요망.")


def to_markdown(pdf: Path) -> Path | None:
    """pdf2md.py로 변환. 성공하면 md 경로, 실패하면 None(호출 측이 PDF로 대체)."""
    print(f"\n📝 Markdown 변환: {pdf.name}", flush=True)
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "pdf2md.py"), str(pdf)])
    md, rep_path = pdf.with_suffix(".md"), pdf.with_suffix(".report.json")
    if r.returncode != 0 or not md.exists():
        print("    ⚠️ 변환 실패 — PDF를 바로 인그레스트합니다.")
        return None
    rep = json.loads(rep_path.read_text(encoding="utf-8"))
    rate = rep["words_missing"] / max(rep["words_checked"], 1)
    if rate > WARN_MISSING:
        pages = sorted(rep["issues"], key=lambda x: -x["missing"])[:10]
        print(f"    ⚠️ 원문 대조 누락 {rate:.1%} — 기준({WARN_MISSING:.0%}) 초과. 인그레스트는 진행하지만 "
              f"아래 페이지를 원본과 대조해 주세요(머리글·바닥글 단어뿐이면 무시해도 됨). 전체: {rep_path.name}")
        for i in pages[:5]:
            print(f"       p.{i['page']}: {i['words'][:8]}")
    return md


def main():
    # Windows 콘솔(cp949)에서 이모지·특수문자 출력 시 UnicodeEncodeError 방지
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="URL→다운로드→RAG 인그레스트")
    ap.add_argument("urls", nargs="+")
    ap.add_argument("--collection", required=True)
    ap.add_argument("--name", help="저장 파일명(단일 URL일 때)")
    ap.add_argument("--no-md", action="store_true", help="PDF를 Markdown으로 변환하지 않고 바로 인그레스트")
    a = ap.parse_args()

    files = []
    for i, url in enumerate(a.urls):
        name = derive_name(url, a.name if len(a.urls) == 1 else None)
        dest = SRC / name
        download(url, dest)
        files.append(dest)

    def is_pdf(f: Path) -> bool:
        with open(f, "rb") as fh:
            return fh.read(5).startswith(b"%PDF")
    if any(is_pdf(f) for f in files) and not a.no_md:
        if importlib.util.find_spec("docling") is None:
            print("\nℹ️ docling 미설치 → PDF를 바로 인그레스트합니다(표·구조 보존 변환: "
                  "python3 -m pip install --user docling)")
        else:
            files = [(to_markdown(f) or f) if is_pdf(f) else f for f in files]

    print(f"\n📥 인그레스트 → 컬렉션 '{a.collection}'", flush=True)  # 하위 프로세스 출력보다 먼저 보이게
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "rag.py"),
                        "ingest", *map(str, files), "--collection", a.collection, "--append"])
    if r.returncode != 0:
        raise SystemExit("❌ 인그레스트 실패")
    print(f"\n다음: python tools/rag.py search \"<질의>\" --collection {a.collection} -k 5")


if __name__ == "__main__":
    main()
