#!/usr/bin/env python3
"""PDF → Markdown 변환 (RAG 인그레스트 전처리)

docling(표 구조 인식, 머리글·바닥글 자동 제거)으로 변환하고 페이지마다 `<!-- p.N -->` 표시를 넣는다.
rag.py ingest가 이 표시로 쪽수를 기록하므로 인용 시 원본 PDF 쪽수를 그대로 쓸 수 있다.

LLM으로 다시 쓰지 않는다 — 결정적 변환이라 같은 PDF는 항상 같은 결과이고, 문장이 바뀌지 않는다.
변환 후 원본 PDF 텍스트(pypdf)와 페이지별로 단어를 대조해, 본문이 빠진 페이지를 보고한다.

사용:
  python tools/pdf2md.py data/kb_src/안내서.pdf                 # → data/kb_src/안내서.md (+ .report.json)
  python tools/pdf2md.py 안내서.pdf --pages 99-101 -o /tmp/t.md  # 일부 페이지만(시험용)
  python tools/rag.py ingest data/kb_src/안내서.md --collection isms

의존: docling(표·레이아웃 모델, 첫 실행 시 다운로드), pypdf(대조용).
"""
from __future__ import annotations
import argparse
import datetime
import html
import importlib.metadata
import json
import logging
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

PAGE_MARK = "<!-- p.{} -->"
WARN_MARK = "<!-- warning:"                 # 쪽 표시 다음 줄. rag.py chunk_md가 읽는다
PUA = re.compile(r"[-]")          # PDF 글머리 아이콘 등 사설 영역 문자(깨진 글자로 보임)
WORD = re.compile(r"[가-힣A-Za-z0-9]{2,}")


def clean(md: str) -> str:
    md = PUA.sub("", md)
    md = re.sub(r"^\s*<!-- image -->\s*$", "", md, flags=re.M)
    # docling은 <, >, &를 &lt; &gt; &amp;로 내보낸다 → 원문 그대로 인용할 수 있게 되돌림
    # (예: "부칙 <제2026-9호, 2026. 7. 1.>"). 쪽 표시 <!-- p.N -->는 이 함수 밖에서 붙이므로 영향 없음.
    md = html.unescape(md)
    md = re.sub(r"^(\s*[-*]) +", r"\1 ", md, flags=re.M)   # 아이콘 제거 후 남은 "-  " 공백
    md = re.sub(r"[ \t]+$", "", md, flags=re.M)
    return re.sub(r"\n{3,}", "\n\n", md).strip()


class _TableDropCatcher(logging.Handler):
    """docling TableFormer가 표 셀을 버릴 때 남기는 경고를 잡는다. docling 주석 그대로 '실제 내용 손실'
    (버린 셀의 텍스트는 어느 표 셀에도 없다)이지만 쪽 번호가 없어, 변환 루프에서 쪽을 찾아 붙인다."""

    def __init__(self):
        super().__init__(logging.WARNING)
        self.msgs: list[str] = []

    def emit(self, record):
        msg = record.getMessage()
        if "dropped from the table" in msg:
            self.msgs.append(msg)


def convert(pdf: Path, pages: tuple[int, int] | None, batch: int = 20
            ) -> tuple[list[tuple[int, str]], dict[int, list[str]]]:
    """batch쪽씩 나눠 변환하며 진행 상황 출력(모델은 한 번만 로드). 쪽 번호는 원본 PDF 기준.
    반환: ([(쪽, md)], {쪽: [표 셀 누락 경고]}). 경고가 난 묶음만 한 쪽씩 다시 변환해 쪽을 특정한다."""
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from pypdf import PdfReader
    opts = PdfPipelineOptions(do_ocr=False, do_table_structure=True)   # 텍스트 PDF: OCR 불필요(오인식 방지)
    opts.table_structure_options.do_cell_matching = True
    conv = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})
    catch = _TableDropCatcher()
    logging.getLogger("MatchingPostProcessor").addHandler(catch)   # docling_ibm_models의 로거 이름
    first, last = pages or (1, len(PdfReader(str(pdf)).pages))
    out, warns, t0 = [], {}, time.time()
    for s in range(first, last + 1, batch):
        e = min(s + batch - 1, last)
        catch.msgs.clear()
        doc = conv.convert(str(pdf), page_range=(s, e)).document
        out += [(p, clean(doc.export_to_markdown(page_no=p, compact_tables=True)))
                for p in sorted(doc.pages.keys())]
        if catch.msgs:                          # 이 묶음 어딘가에서 셀 누락 → 한 쪽씩 다시 돌려 쪽 특정
            for p in range(s, e + 1):
                catch.msgs.clear()
                conv.convert(str(pdf), page_range=(p, p))
                if catch.msgs:
                    warns[p] = list(catch.msgs)
                    print(f"   ⚠️ p.{p}: 표 셀 누락 — {catch.msgs[0]}", flush=True)
        done, total = e - first + 1, last - first + 1
        el = time.time() - t0
        print(f"   p.{s}-{e} 완료 ({done}/{total}쪽 · {el:.0f}초 · 남은 시간 약 "
              f"{el / done * (total - done):.0f}초)", flush=True)
    return out, warns


def fidelity(pdf: Path, md_pages: list[tuple[int, str]]) -> dict:
    """원본 PDF 텍스트(pypdf) 단어가 md에 있는지 페이지별 대조.
    머리글·바닥글·옆면 탭처럼 여러 페이지에 반복되는 단어와 쪽번호(숫자 1~3자리)는 제외."""
    from pypdf import PdfReader
    reader = PdfReader(str(pdf))
    ref = {i + 1: WORD.findall(pg.extract_text() or "") for i, pg in enumerate(reader.pages)}
    df = Counter(w for ws in ref.values() for w in set(ws))
    # 반복 단어(머리글 등) = 전체의 25% 초과 '그리고' 3쪽 초과에 나오는 단어. 쪽수 하한이 없으면
    # 짧은 문서에서 모든 단어가 반복 단어로 빠져 대조가 0단어(=검사 안 함)가 된다.
    boiler = {w for w, n in df.items() if n > max(0.25 * len(ref), 3)}
    report = {"pages": len(md_pages), "boilerplate_words": len(boiler), "issues": []}
    total = missing = 0
    for p, md in md_pages:
        got = set(WORD.findall(md))
        words = [w for w in ref.get(p, []) if w not in boiler and not re.fullmatch(r"\d{1,3}", w)]
        miss = [w for w in words if w not in got]
        total += len(words); missing += len(miss)
        if miss:
            report["issues"].append({"page": p, "missing": len(miss), "of": len(words),
                                     "words": sorted(set(miss))[:30]})
    report["words_checked"] = total
    report["words_missing"] = missing
    if total == 0:
        print("   ⚠️ 대조할 본문 단어가 없습니다(이미지 PDF·텍스트 추출 실패 가능) — 원본을 직접 확인하세요.")
    return report


def main():
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="PDF → Markdown (docling, 페이지 표시 포함)")
    ap.add_argument("pdf")
    ap.add_argument("-o", "--out", help="출력 md (기본: PDF와 같은 위치, 확장자 .md)")
    ap.add_argument("--pages", help="페이지 범위 예: 99-101 (시험용)")
    a = ap.parse_args()

    pdf = Path(a.pdf)
    if not pdf.exists():
        raise SystemExit(f"❌ 없음: {pdf}")
    out = Path(a.out) if a.out else pdf.with_suffix(".md")
    pages = tuple(int(x) for x in a.pages.split("-")) if a.pages else None
    if pages and len(pages) == 1:
        pages = (pages[0], pages[0])

    t0 = time.time()
    print(f"🔄 변환: {pdf.name} {'p.%d-%d' % pages if pages else '(전체)'} — docling(표 구조 인식, OCR 끔)", flush=True)
    md_pages, warns = convert(pdf, pages)
    ver = importlib.metadata.version("docling")
    head = (f"<!-- source: {pdf.name} | converter: docling {ver} (tools/pdf2md.py) | "
            f"converted: {datetime.date.today()} -->")
    def warn_line(p: int) -> str:              # rag.py chunk_md가 읽어 이 쪽 청크에 경고를 붙인다
        n = sum(int(m.split()[0]) for m in warns[p] if m.split()[0].isdigit())
        return f"{WARN_MARK} 표 변환 중 셀 {n}개가 버려짐(docling) — 이 쪽 표는 원본 PDF p.{p}와 대조 필요 -->\n\n"
    body = "\n\n".join(f"{PAGE_MARK.format(p)}\n{warn_line(p) if p in warns else ''}{md}"
                       for p, md in md_pages)
    out.write_text(f"{head}\n\n{body}\n", encoding="utf-8")
    print(f"✅ {out} — {len(md_pages)}쪽 · {time.time() - t0:.0f}초", flush=True)

    rep = fidelity(pdf, md_pages)
    rep["table_warnings"] = {str(p): m for p, m in sorted(warns.items())}
    rep_path = out.with_suffix(".report.json")
    rep_path.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    rate = rep["words_missing"] / max(rep["words_checked"], 1)
    print(f"🔍 원문 대조: 본문 단어 {rep['words_checked']}개 중 누락 {rep['words_missing']}개({rate:.2%}) · "
          f"누락 있는 페이지 {len(rep['issues'])}쪽 → {rep_path.name}")
    for it in sorted(rep["issues"], key=lambda x: -x["missing"])[:10]:
        print(f"   p.{it['page']}: {it['missing']}/{it['of']} {it['words'][:8]}")
    if warns:
        print(f"⚠️ 표 셀 누락 {len(warns)}쪽: {sorted(warns)} — md에 표시했고 검색·show 결과에 경고로 나온다"
              f"(rag.py show --page N --raw로 원본 대조).")


if __name__ == "__main__":
    main()
