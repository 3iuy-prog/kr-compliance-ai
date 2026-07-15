#!/usr/bin/env python3
"""범용 RAG — 아무 PDF/텍스트나 지식베이스로 (L3 인그레스트)

오프라인·무(無)API 기본 백엔드: 문자 n-gram TF-IDF 코사인(한국어 토크나이저 불필요).
법령 아닌 문서(ISMS-P 안내서, 전자금융기반시설 점검항목, 금융보안원 가이드 등 L3)를
컬렉션 단위로 흡수해 legal-answer 스킬이 검색.

임베딩 백엔드는 나중에 교체 가능(embed() 함수만 바꾸면 됨).

사용:
  python tools/rag.py ingest <파일...> --collection isms      # PDF/txt/md 흡수
  python tools/rag.py search "접속기록 보관 기간" --collection isms -k 5
  python tools/rag.py list
  python tools/rag.py drop --collection isms

의존: pypdf(있으면 PDF), 없으면 txt/md만. 인덱스는 순수 stdlib(json).
"""
from __future__ import annotations
import argparse
import json
import math
import re
import zlib
from collections import Counter
from pathlib import Path

KB = Path(__file__).resolve().parent.parent / "data" / "kb"
HASH_BITS = 1 << 20  # 해시 feature 공간


# ---------- 텍스트 추출 ----------
def extract_text(path: Path) -> list[tuple[int, str]]:
    """(page, text) 목록. PDF는 페이지별, 그 외는 통째로 page=1."""
    suf = path.suffix.lower()
    if suf == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError:
            raise SystemExit("❌ PDF 처리에 pypdf 필요: python3 -m pip install --user pypdf")
        reader = PdfReader(str(path))
        return [(i + 1, (pg.extract_text() or "")) for i, pg in enumerate(reader.pages)]
    if suf in (".txt", ".md"):
        return [(1, path.read_text(encoding="utf-8", errors="ignore"))]
    raise SystemExit(f"❌ 지원 안 함: {suf} (pdf/txt/md만. HWP는 텍스트 변환 후 투입)")


# ---------- 청킹 ----------
def chunk(text: str, size: int = 800, overlap: int = 150) -> list[str]:
    text = re.sub(r"[ \t]+", " ", text)
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    out, buf = [], ""
    for p in paras:
        if len(buf) + len(p) + 1 <= size:
            buf = f"{buf}\n{p}".strip()
        else:
            if buf:
                out.append(buf)
            buf = (buf[-overlap:] + "\n" + p) if buf else p
            while len(buf) > size:            # 아주 긴 문단 분할
                out.append(buf[:size]); buf = buf[size - overlap:]
    if buf:
        out.append(buf)
    return out


# ---------- 벡터화(문자 n-gram TF-IDF, 해시) ----------
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w가-힣]", " ", s.lower())).strip()


def _feats(s: str) -> list[int]:
    s = _norm(s)
    grams = []
    for tok in s.split():
        t = f"^{tok}$"
        for n in (2, 3):                      # 문자 2·3-gram
            grams += [t[i:i + n] for i in range(len(t) - n + 1)]
    # zlib.crc32: 프로세스 간 결정적(내장 hash()는 프로세스별 랜덤 시드라 불가)
    return [zlib.crc32(g.encode("utf-8")) % HASH_BITS for g in grams]


def _tf(feats: list[int]) -> dict[int, float]:
    return dict(Counter(feats))


def embed(text: str, idf: dict[int, float]) -> dict[str, float]:
    """TF-IDF L2 정규화 희소벡터. (임베딩 교체 지점)"""
    vec = {f: c * idf.get(f, 0.0) for f, c in _tf(_feats(text)).items()}
    norm = math.sqrt(sum(w * w for w in vec.values())) or 1.0
    return {str(f): w / norm for f, w in vec.items() if w}


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    short, long = (a, b) if len(a) < len(b) else (b, a)
    return sum(w * long.get(f, 0.0) for f, w in short.items())


# ---------- 컬렉션 IO ----------
def col_dir(name: str) -> Path:
    return KB / name


def cmd_ingest(a):
    name = a.collection
    d = col_dir(name); d.mkdir(parents=True, exist_ok=True)
    chunks = []  # {id, source, page, text}
    for f in a.files:
        p = Path(f)
        if not p.exists():
            print(f"⚠️ 없음: {p}"); continue
        for page, text in extract_text(p):
            for c in chunk(text):
                if len(c) < 30:
                    continue
                chunks.append({"id": len(chunks), "source": p.name, "page": page, "text": c})
    if not chunks:
        raise SystemExit("❌ 추출된 청크 없음.")
    # IDF 계산
    N = len(chunks)
    df: Counter = Counter()
    per_feats = []
    for c in chunks:
        fs = set(_feats(c["text"])); per_feats.append(fs)
        df.update(fs)
    idf = {f: math.log((N + 1) / (df[f] + 1)) + 1 for f in df}
    # 청크 벡터
    vecs = [embed(c["text"], idf) for c in chunks]
    (d / "chunks.jsonl").write_text(
        "\n".join(json.dumps(c, ensure_ascii=False) for c in chunks) + "\n", encoding="utf-8")
    (d / "index.json").write_text(json.dumps(
        {"idf": {str(k): v for k, v in idf.items()}, "vecs": vecs,
         "meta": {"n": N, "sources": sorted({c['source'] for c in chunks})}},
        ensure_ascii=False), encoding="utf-8")
    print(f"✅ '{name}' 인그레스트: 청크 {N}개, 출처 {sorted({c['source'] for c in chunks})}")


def cmd_search(a):
    d = col_dir(a.collection)
    if not (d / "index.json").exists():
        raise SystemExit(f"❌ 컬렉션 '{a.collection}' 없음. list로 확인.")
    idx = json.loads((d / "index.json").read_text(encoding="utf-8"))
    chunks = [json.loads(l) for l in (d / "chunks.jsonl").read_text(encoding="utf-8").splitlines()]
    idf = {int(k): v for k, v in idx["idf"].items()}
    q = embed(a.query, idf)
    scored = sorted(((cosine(q, v), i) for i, v in enumerate(idx["vecs"])), reverse=True)
    print(f"=== '{a.query}' @ {a.collection} — 상위 {a.k} ===")
    for score, i in scored[:a.k]:
        if score <= 0:
            continue
        c = chunks[i]
        snippet = re.sub(r"\s+", " ", c["text"])[:240]
        print(f"\n[{score:.3f}] {c['source']} p.{c['page']} (chunk {c['id']})\n  {snippet}…")


def cmd_list(_):
    if not KB.exists() or not any(KB.iterdir()):
        print("(컬렉션 없음)"); return
    print("=== RAG 컬렉션 ===")
    for d in sorted(KB.iterdir()):
        idxp = d / "index.json"
        if idxp.exists():
            m = json.loads(idxp.read_text(encoding="utf-8"))["meta"]
            print(f"  {d.name}: 청크 {m['n']} · 출처 {m['sources']}")


def cmd_drop(a):
    import shutil
    d = col_dir(a.collection)
    if d.exists():
        shutil.rmtree(d); print(f"🗑️ '{a.collection}' 삭제")
    else:
        print(f"(없음: {a.collection})")


def main():
    ap = argparse.ArgumentParser(description="범용 RAG (오프라인 TF-IDF)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("ingest", help="파일 흡수")
    s.add_argument("files", nargs="+"); s.add_argument("--collection", required=True)
    s.set_defaults(func=cmd_ingest)

    s = sub.add_parser("search", help="검색")
    s.add_argument("query"); s.add_argument("--collection", required=True)
    s.add_argument("-k", type=int, default=5); s.set_defaults(func=cmd_search)

    sub.add_parser("list", help="컬렉션 목록").set_defaults(func=cmd_list)

    s = sub.add_parser("drop", help="컬렉션 삭제")
    s.add_argument("--collection", required=True); s.set_defaults(func=cmd_drop)

    a = ap.parse_args(); a.func(a)


if __name__ == "__main__":
    main()
