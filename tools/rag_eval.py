#!/usr/bin/env python3
"""RAG 검색 품질 평가 — data/rag_eval.json 질의로 백엔드별 적중률 비교.

코퍼스: legalize-kr 법률 4종을 조문 단위로 분할(조문 = 페이지)해 임시 컬렉션 `_eval`로 인그레스트.
지표: hit@1, hit@5, MRR(@10) — 질의 유형(paraphrase/exact)별.

사용:
  python tools/rag_eval.py                      # 인덱스 재생성 + 전 모드 평가
  python tools/rag_eval.py --reuse              # 기존 _eval 인덱스 재사용
  python tools/rag_eval.py --model BAAI/bge-m3  # 다른 임베딩 모델로 비교
  python tools/rag_eval.py -v                   # 질의별 정답 순위 출력
  python tools/rag_eval.py --spec data/rag_eval_isms.json   # 실제 ISMS-P 안내서 컬렉션(isms) 평가
  python tools/rag_eval.py --spec data/rag_eval_isms.json --collection isms_md   # 같은 질의로 다른 컬렉션 비교

전제: vendor/legalize-kr 클론(python tools/legalize.py sync).
"""
from __future__ import annotations
import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rag  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LAWS = ROOT / "vendor" / "legalize-kr" / "kr"
EVAL = ROOT / "data" / "rag_eval.json"
COLLECTION = "_eval"
HEAD = re.compile(r"^#{2,6}\s*(제\d+조(?:의\d+)?)\s*(.*)$")


def law_chunks(law: str, rel: str) -> list[dict]:
    """법률 md → 조문별 청크. page = '제N조'."""
    p = LAWS / rel
    if not p.exists():
        raise SystemExit(f"❌ {p} 없음 — python tools/legalize.py sync 먼저 실행")
    body = p.read_text(encoding="utf-8").split("\n---\n", 1)[-1]  # front matter 제거
    out, art, buf = [], None, []

    def flush():
        if art and buf:
            text = "\n".join(buf).strip()
            for c in rag.chunk(text):
                if len(c) >= 30:
                    out.append({"source": law, "page": art, "text": c})

    for line in body.splitlines():
        m = HEAD.match(line)
        if m:
            flush()
            art, buf = m.group(1), [f"{m.group(1)} {m.group(2)}".strip()]
        elif line.startswith("#"):
            continue                          # 장·절 제목은 조문 경계 아님
        elif art:
            buf.append(line)
    flush()
    return out


def evaluate(col: dict, queries: list[dict], mode: str, verbose: bool,
             tfidf_weight: float = rag.TFIDF_WEIGHT) -> dict:
    stats: dict[str, dict] = {}
    for q in queries:
        if "pages" in q:   # PDF 세트: 결과 청크의 페이지가 정답 범위 안이면 적중
            pages = set(q["pages"])
            ok = lambda c: c["page"] in pages
        else:              # 법률 세트: (법률, 조문) 일치
            gold = {tuple(g) for g in q["gold"]}
            ok = lambda c: (c["source"], c["page"]) in gold
        hits = rag.search(col, q["q"], k=10, mode=mode, tfidf_weight=tfidf_weight)
        rank = next((r for r, h in enumerate(hits, 1) if ok(h["chunk"])), None)
        s = stats.setdefault(q["type"], {"n": 0, "h1": 0, "h5": 0, "rr": 0.0})
        s["n"] += 1
        s["h1"] += rank == 1
        s["h5"] += bool(rank and rank <= 5)
        s["rr"] += 1 / rank if rank else 0.0
        if verbose:
            top = hits[0]["chunk"] if hits else None
            got = (f"p.{top['page']}" if "pages" in q else f"{top['source']} {top['page']}") if top else "-"
            print(f"  [{mode:6}] {'✅' if rank == 1 else ('🔸' if rank else '❌')} "
                  f"정답 {rank or '10위 밖':>6} | 1위 {got:24} | {q['q']}")
    return stats


def main():
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="RAG 검색 품질 평가")
    ap.add_argument("--model", default=rag.DEFAULT_MODEL)
    ap.add_argument("--reuse", action="store_true", help="기존 _eval 인덱스 재사용")
    ap.add_argument("--modes", default="tfidf,dense,hybrid")
    ap.add_argument("--weights", help="hybrid의 TF-IDF 가중치 후보(쉼표 구분), 예: 0,0.25,0.5,1")
    ap.add_argument("--collection", help="평가할 컬렉션(spec의 _meta.collection 대신). 예: isms_md")
    ap.add_argument("--spec", default=str(EVAL),
                    help="평가 세트. _meta.collection이 있으면 그 기존 컬렉션을 평가(재생성 안 함)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()

    spec = json.loads(Path(a.spec).read_text(encoding="utf-8"))
    queries = spec["queries"]
    collection = a.collection or spec["_meta"].get("collection", COLLECTION)
    # 실제 KB 컬렉션(예: isms)은 rag.py ingest로 만든 것을 그대로 평가. _eval(법률)만 여기서 생성
    if collection == COLLECTION and not a.reuse:
        chunks = [c for law, rel in spec["_meta"]["corpus"].items() for c in law_chunks(law, rel)]
        model = a.model if rag.dense_available() else None
        t = time.time()
        rag.write_collection(COLLECTION, chunks, model)
        print(f"인덱스: 청크 {len(chunks)}개 · 임베딩 {model or '없음'} · {time.time() - t:.1f}초")
    col = rag.load_collection(collection)
    modes = [m for m in a.modes.split(",")
             if m == "tfidf" or ("dense_path" in col and rag.dense_available())]

    runs = []  # (라벨, mode, tfidf_weight)
    for mode in modes:
        if mode == "hybrid" and a.weights:
            runs += [(f"hybrid@{w:g}", "hybrid", w) for w in map(float, a.weights.split(","))]
        else:
            runs.append((mode, mode, rag.TFIDF_WEIGHT))

    order = {"paraphrase": 0, "exact": 1, "id": 2}
    rows = []
    for label, mode, w in runs:
        t = time.time()
        st = evaluate(col, queries, mode, a.verbose, w)
        dt = (time.time() - t) / len(queries) * 1000
        for typ, s in sorted(st.items(), key=lambda x: order.get(x[0], 9)):
            rows.append((label, typ, s["n"], s["h1"] / s["n"], s["h5"] / s["n"], s["rr"] / s["n"], dt))

    print(f"\n컬렉션: {collection} · 모델: {col['meta'].get('dense_model') or '-'} · 질의 {len(queries)}개")
    print(f"{'mode':11} {'type':10} {'n':>3} {'hit@1':>6} {'hit@5':>6} {'MRR':>6} {'ms/q':>6}")
    for label, typ, n, h1, h5, mrr, dt in rows:
        print(f"{label:11} {typ:10} {n:>3} {h1:>6.0%} {h5:>6.0%} {mrr:>6.2f} {dt:>6.0f}")


if __name__ == "__main__":
    main()
