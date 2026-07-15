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
"""
from __future__ import annotations
import argparse
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "kb_src"
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


def main():
    ap = argparse.ArgumentParser(description="URL→다운로드→RAG 인그레스트")
    ap.add_argument("urls", nargs="+")
    ap.add_argument("--collection", required=True)
    ap.add_argument("--name", help="저장 파일명(단일 URL일 때)")
    a = ap.parse_args()

    files = []
    for i, url in enumerate(a.urls):
        name = derive_name(url, a.name if len(a.urls) == 1 else None)
        dest = SRC / name
        download(url, dest)
        files.append(str(dest))

    print(f"\n📥 인그레스트 → 컬렉션 '{a.collection}'")
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "rag.py"),
                        "ingest", *files, "--collection", a.collection])
    if r.returncode != 0:
        raise SystemExit("❌ 인그레스트 실패")
    print(f"\n다음: python tools/rag.py search \"<질의>\" --collection {a.collection} -k 5")


if __name__ == "__main__":
    main()
