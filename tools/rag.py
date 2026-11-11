#!/usr/bin/env python3
"""범용 RAG — 아무 PDF/텍스트나 지식베이스로 (L3 인그레스트)

법령 아닌 문서(ISMS-P 안내서, 전자금융기반시설 점검항목, 금융보안원 가이드 등 L3)를
컬렉션 단위로 흡수해 legal-answer 스킬이 검색.

검색 백엔드 (하이브리드):
  - 키워드: 문자 n-gram TF-IDF 코사인 — 항상 사용. 순수 stdlib, 조문번호·법정용어 정확 매칭에 강함.
  - 의미:   로컬 임베딩 모델(기본 nlpai-lab/KURE-v1) 코사인 — sentence-transformers 설치 시.
            "로그 보관"↔"접속기록", "패스워드"↔"비밀번호" 같은 바꿔 말한 질의를 잡는다.
  - 결합:   RRF(Reciprocal Rank Fusion). 임베딩이 없으면 자동으로 TF-IDF만 사용.
  임베딩은 로컬 계산(별도 임베딩 API 불필요). 단 검색 결과를 읽는 Claude는 Anthropic API로 동작한다.
  모델은 RAG_EMBED_MODEL 환경변수로 교체.

사용:
  python tools/rag.py ingest <파일...> --collection isms      # PDF/txt/md 흡수(기존 컬렉션 덮어씀)
  python tools/rag.py ingest <파일...> --collection isms --append   # 기존에 추가(새 청크만 임베딩)
  python tools/rag.py search "접속기록 보관 기간" --collection isms -k 5 [--mode auto|hybrid|dense|tfidf]
  python tools/rag.py show --collection isms --chunk 245 [--context 1]   # 원문 전체(앞뒤 청크 포함)
  python tools/rag.py show --collection isms --page 146                  # 페이지 원문
  python tools/rag.py list
  python tools/rag.py drop --collection isms

의존: pypdf(PDF), sentence-transformers(선택·의미검색). 둘 다 없으면 txt/md + TF-IDF.
"""
from __future__ import annotations
import argparse
import json
import math
import os
import re
import sys
import time
import zlib
from collections import Counter
from pathlib import Path

KB = Path(__file__).resolve().parent.parent / "data" / "kb"
HASH_BITS = 1 << 20  # 해시 feature 공간
DEFAULT_MODEL = os.environ.get("RAG_EMBED_MODEL", "nlpai-lab/KURE-v1")
RRF_K = 60           # RRF 상수(표준값). 순위 r의 기여 = 1/(RRF_K + r)
POOL = 50            # 하이브리드 결합 전 각 백엔드에서 가져올 후보 수
TFIDF_WEIGHT = 0.25  # RRF에서 키워드 순위 가중치(의미 = 1.0). rag_eval.py 그리드 결과 — docs/05-rag.md


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


# ---------- 키워드 백엔드: 문자 n-gram TF-IDF(해시) ----------
def _norm(s: str) -> str:
    s = re.sub(r"(?<=\d)\.(?=\d)", "_", s.lower())   # "2.5.4" 같은 항목번호를 한 토큰으로 유지
    return re.sub(r"\s+", " ", re.sub(r"[^\w가-힣]", " ", s)).strip()


def _feats(s: str) -> list[int]:
    s = _norm(s)
    grams = []
    for tok in s.split():
        if re.fullmatch(r"\d+(?:_\d+)+", tok):
            # 항목번호(2.5.4 → 2_5_4)는 통째로만. n-gram으로 쪼개면 2.5.1·2.5.2와 조각이 겹쳐
            # 번호가 잔뜩 나열된 목차 페이지가 실제 항목 페이지보다 위로 올라온다.
            grams.append(f"#{tok}")
            continue
        t = f"^{tok}$"
        for n in (2, 3):                      # 문자 2·3-gram
            grams += [t[i:i + n] for i in range(len(t) - n + 1)]
    # zlib.crc32: 프로세스 간 결정적(내장 hash()는 프로세스별 랜덤 시드라 불가)
    return [zlib.crc32(g.encode("utf-8")) % HASH_BITS for g in grams]


def tfidf_vec(text: str, idf: dict[int, float]) -> dict[str, float]:
    """TF-IDF L2 정규화 희소벡터."""
    vec = {f: c * idf.get(f, 0.0) for f, c in Counter(_feats(text)).items()}
    norm = math.sqrt(sum(w * w for w in vec.values())) or 1.0
    return {str(f): w / norm for f, w in vec.items() if w}


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    short, long = (a, b) if len(a) < len(b) else (b, a)
    return sum(w * long.get(f, 0.0) for f, w in short.items())


def build_tfidf(texts: list[str]) -> tuple[dict[int, float], list[dict[str, float]]]:
    N = len(texts)
    df: Counter = Counter()
    for t in texts:
        df.update(set(_feats(t)))
    idf = {f: math.log((N + 1) / (df[f] + 1)) + 1 for f in df}
    return idf, [tfidf_vec(t, idf) for t in texts]


# ---------- 의미 백엔드: 로컬 임베딩(선택) ----------
_MODELS: dict = {}


def dense_available() -> bool:
    # import하지 않고 설치 여부만 확인(import는 torch 로드로 수 초 걸림 — TF-IDF 검색까지 느려짐)
    import importlib.util
    return importlib.util.find_spec("sentence_transformers") is not None


def dense_encode(texts: list[str], model_name: str, show_progress: bool = False):
    """L2 정규화된 float32 행렬(n, dim). 로컬 CPU 계산."""
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
    import numpy as np
    from sentence_transformers import SentenceTransformer
    try:
        from transformers.utils import logging as hf_logging
        hf_logging.disable_progress_bar()     # "Loading weights" 막대가 검색 출력을 어지럽히지 않게
    except ImportError:
        pass
    if model_name not in _MODELS:
        try:   # 캐시에 있으면 네트워크 접근 없이 로드(오프라인 원칙)
            m = SentenceTransformer(model_name, local_files_only=True)
        except OSError:
            print(f"⬇️ 임베딩 모델 최초 다운로드: {model_name} (약 2GB, 1회)", file=sys.stderr)
            m = SentenceTransformer(model_name)
        m.max_seq_length = 512                # 청크 800자 ≈ 400~600 토큰. 속도 위해 상한
        _MODELS[model_name] = m
    emb = _MODELS[model_name].encode(texts, batch_size=16, normalize_embeddings=True,
                                     show_progress_bar=show_progress, convert_to_numpy=True)
    return np.asarray(emb, dtype=np.float32)


# ---------- 컬렉션 IO ----------
def col_dir(name: str) -> Path:
    return KB / name


def write_collection(name: str, chunks: list[dict], model: str | None, dense=None) -> dict:
    """chunks: [{source, page, text}]. TF-IDF는 항상, model이 있으면 임베딩도 저장.
    dense(청크와 같은 순서의 행렬)를 주면 다시 계산하지 않고 그대로 저장한다(--append용)."""
    d = col_dir(name); d.mkdir(parents=True, exist_ok=True)
    for i, c in enumerate(chunks):
        c["id"] = i
    texts = [c["text"] for c in chunks]
    idf, vecs = build_tfidf(texts)
    dense_model = None
    if model:
        import numpy as np
        if dense is None:
            dense = dense_encode(texts, model, show_progress=len(texts) > 200)
        assert len(dense) == len(chunks), "임베딩 행 수와 청크 수 불일치"
        np.save(d / "dense.npy", dense)
        dense_model = model
    elif (d / "dense.npy").exists():
        (d / "dense.npy").unlink()            # 옛 임베딩이 새 청크와 어긋나지 않도록
    (d / "chunks.jsonl").write_text(
        "\n".join(json.dumps(c, ensure_ascii=False) for c in chunks) + "\n", encoding="utf-8")
    meta = {"n": len(chunks), "sources": sorted({c["source"] for c in chunks}),
            "dense_model": dense_model}
    (d / "index.json").write_text(json.dumps(
        {"idf": {str(k): v for k, v in idf.items()}, "vecs": vecs, "meta": meta},
        ensure_ascii=False), encoding="utf-8")
    return meta


def reindex_tfidf(name: str) -> int:
    """TF-IDF 인덱스만 재생성(청크·임베딩 유지). 토큰화 규칙이 바뀌었을 때 재임베딩 없이 반영."""
    d = col_dir(name)
    idx = json.loads((d / "index.json").read_text(encoding="utf-8"))
    chunks = [json.loads(l) for l in (d / "chunks.jsonl").read_text(encoding="utf-8").splitlines() if l]
    idf, vecs = build_tfidf([c["text"] for c in chunks])
    idx.update({"idf": {str(k): v for k, v in idf.items()}, "vecs": vecs})
    (d / "index.json").write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")
    return len(chunks)


def load_collection(name: str) -> dict:
    d = col_dir(name)
    if not (d / "index.json").exists():
        raise SystemExit(f"❌ 컬렉션 '{name}' 없음. list로 확인.")
    idx = json.loads((d / "index.json").read_text(encoding="utf-8"))
    chunks = [json.loads(l) for l in (d / "chunks.jsonl").read_text(encoding="utf-8").splitlines() if l]
    col = {"name": name, "chunks": chunks, "vecs": idx["vecs"], "meta": idx["meta"],
           "idf": {int(k): v for k, v in idx["idf"].items()}, "dense": None}
    if idx["meta"].get("dense_model") and (d / "dense.npy").exists():
        col["dense_path"] = d / "dense.npy"
    return col


def resolve_mode(col: dict, mode: str) -> str:
    has_dense = "dense_path" in col and dense_available()
    if mode == "auto":
        return "hybrid" if has_dense else "tfidf"
    if mode in ("hybrid", "dense") and not has_dense:
        why = ("sentence-transformers 미설치" if not dense_available()
               else "이 컬렉션에 임베딩 없음 — 재인그레스트 필요")
        raise SystemExit(f"❌ --mode {mode} 불가: {why}")
    return mode


def _rank_tfidf(col: dict, query: str) -> list[tuple[float, int]]:
    q = tfidf_vec(query, col["idf"])
    scored = [(cosine(q, v), i) for i, v in enumerate(col["vecs"])]
    return sorted((s for s in scored if s[0] > 0), reverse=True)


def _rank_dense(col: dict, query: str) -> list[tuple[float, int]]:
    import numpy as np
    if col["dense"] is None:
        col["dense"] = np.load(col["dense_path"])
    q = dense_encode([query], col["meta"]["dense_model"])[0]
    sims = col["dense"] @ q
    order = np.argsort(-sims)
    return [(float(sims[i]), int(i)) for i in order]


def search(col: dict, query: str, k: int = 5, mode: str = "auto",
           tfidf_weight: float = TFIDF_WEIGHT) -> list[dict]:
    """[{chunk, score, tfidf_rank, dense_rank}] 상위 k. mode는 resolve_mode 거친 값이어도 됨."""
    mode = resolve_mode(col, mode)
    tf = _rank_tfidf(col, query) if mode in ("tfidf", "hybrid") else []
    de = _rank_dense(col, query) if mode in ("dense", "hybrid") else []
    tf_rank = {i: r for r, (_, i) in enumerate(tf, 1)}
    de_rank = {i: r for r, (_, i) in enumerate(de, 1)}
    if mode == "tfidf":
        top = tf[:k]
    elif mode == "dense":
        top = de[:k]
    else:  # 가중 RRF: 각 백엔드 상위 POOL 순위만 합산
        fused: Counter = Counter()
        for ranking, w in ((tf[:POOL], tfidf_weight), (de[:POOL], 1.0)):
            for r, (_, i) in enumerate(ranking, 1):
                fused[i] += w / (RRF_K + r)
        top = sorted(((s, i) for i, s in fused.items()), reverse=True)[:k]
    return [{"chunk": col["chunks"][i], "score": s,
             "tfidf_rank": tf_rank.get(i), "dense_rank": de_rank.get(i)} for s, i in top]


# ---------- CLI ----------
def _elapsed(t0: float) -> str:
    s = time.time() - t0
    return f"{s:.1f}초" if s < 60 else f"{int(s // 60)}분 {int(s % 60)}초"


def _read_files(files: list[str]) -> list[dict]:
    chunks = []  # {source, page, text}
    for f in files:
        p = Path(f)
        if not p.exists():
            print(f"⚠️ 없음: {p}"); continue
        for page, text in extract_text(p):
            for c in chunk(text):
                if len(c) >= 30:
                    chunks.append({"source": p.name, "page": page, "text": c})
    return chunks


def cmd_ingest(a):
    t0 = time.time()
    new = _read_files(a.files)
    if not new:
        raise SystemExit("❌ 추출된 청크 없음.")
    exists = (col_dir(a.collection) / "index.json").exists()
    if a.append and exists:
        meta = _append(a, new)
    else:
        if exists:
            old = json.loads((col_dir(a.collection) / "index.json").read_text(encoding="utf-8"))["meta"]
            print(f"⚠️ 기존 컬렉션 '{a.collection}'(출처 {old['sources']})을 덮어씁니다. "
                  f"기존 문서를 유지하고 추가하려면 --append")
        model = None
        if not a.no_dense:
            if dense_available():
                model = a.model or DEFAULT_MODEL
                print(f"🧠 임베딩 생성: {model} (청크 {len(new)}개, 로컬 실행)")
            else:
                print("ℹ️ sentence-transformers 미설치 → TF-IDF만 인덱싱 "
                      "(의미검색: python3 -m pip install --user sentence-transformers)")
        meta = write_collection(a.collection, new, model)
    print(f"✅ '{a.collection}' 인그레스트: 청크 {meta['n']}개, 출처 {meta['sources']}, "
          f"임베딩 {meta['dense_model'] or '없음'} · {_elapsed(t0)}")


def _append(a, new: list[dict]) -> dict:
    """기존 청크·임베딩은 그대로 두고 새 파일의 청크만 임베딩해 덧붙인다.
    같은 파일명이 이미 있으면 그 파일의 청크만 교체(중복 방지)."""
    col = load_collection(a.collection)
    model = col["meta"].get("dense_model")
    new_sources = {c["source"] for c in new}
    keep = [i for i, c in enumerate(col["chunks"]) if c["source"] not in new_sources]
    replaced = sorted(new_sources & set(col["meta"]["sources"]))
    if replaced:
        print(f"♻️ 같은 파일명은 교체: {replaced}")
    kept = [{k: v for k, v in col["chunks"][i].items() if k != "id"} for i in keep]

    if not model:                             # 기존이 TF-IDF 전용이면 그대로 TF-IDF만
        if dense_available() and not a.no_dense:
            print(f"ℹ️ 기존 컬렉션에 임베딩이 없어 TF-IDF만 추가합니다. "
                  f"의미검색을 켜려면 전체 파일로 다시 ingest(--append 없이)")
        return write_collection(a.collection, kept + new, None)

    if a.model and a.model != model:
        raise SystemExit(f"❌ 모델 불일치: 기존 컬렉션은 {model}. 다른 모델로 추가하면 검색이 깨집니다 "
                         f"— 전체 파일로 다시 ingest(--append 없이)")
    if a.no_dense:
        raise SystemExit("❌ 기존 컬렉션에 임베딩이 있어 --no-dense로 추가할 수 없습니다.")
    if not dense_available():
        raise SystemExit(f"❌ 기존 컬렉션에 임베딩({model})이 있어 새 청크도 임베딩해야 합니다 "
                         f"— sentence-transformers 설치 필요")
    import numpy as np
    old = np.load(col["dense_path"])
    if len(old) != len(col["chunks"]):
        raise SystemExit("❌ 기존 임베딩과 청크 수가 어긋남 — 전체 파일로 다시 ingest 필요")
    print(f"🧠 새 청크만 임베딩: {model} ({len(new)}개, 기존 {len(kept)}개 유지)")
    emb = dense_encode([c["text"] for c in new], model, show_progress=len(new) > 200)
    dense = np.vstack([old[keep], emb]) if keep else emb
    return write_collection(a.collection, kept + new, model, dense=dense)


def cmd_search(a):
    # 질의 여러 개를 한 프로세스에서 처리 — 임베딩 모델 로드(호출당 수 초)를 한 번만 한다
    col = load_collection(a.collection)
    mode = resolve_mode(col, a.mode)
    for qi, query in enumerate(a.query):
        hits = search(col, query, a.k, mode)
        print(f"{'' if qi == 0 else chr(10)}=== '{query}' @ {a.collection} [{mode}] — 상위 {a.k} ===")
        for h in hits:
            c = h["chunk"]
            ranks = ""
            if mode == "hybrid":
                ranks = f" (키워드 {h['tfidf_rank'] or '-'}위 · 의미 {h['dense_rank'] or '-'}위)"
            text = re.sub(r"\s+", " ", c["text"]).strip()
            if a.brief:                       # 사람이 훑어볼 때만. 답변 근거로는 전문을 쓴다
                text = text[:240] + ("…" if len(text) > 240 else "")
            print(f"\n[{h['score']:.3f}] {c['source']} p.{c['page']} (chunk {c['id']}){ranks}\n  {text}")


def _merge_overlap(prev: str, nxt: str, max_overlap: int = 400) -> str:
    """연속 청크를 이어 붙인다. chunk()가 앞 청크 끝(overlap)을 다음 청크 앞에 복사하므로 겹친 부분은 한 번만."""
    for k in range(min(len(prev), len(nxt), max_overlap), 0, -1):
        if prev.endswith(nxt[:k]):
            return prev + nxt[k:]
    return prev + "\n" + nxt


def cmd_show(a):
    """청크 번호(앞뒤 context개 포함) 또는 페이지로 원문 전체를 출력. 원문 인용·검증용."""
    col = load_collection(a.collection)
    chunks = col["chunks"]
    if a.chunk is not None:
        if not 0 <= a.chunk < len(chunks):
            raise SystemExit(f"❌ chunk {a.chunk} 없음 (0~{len(chunks) - 1})")
        src = chunks[a.chunk]["source"]
        ids = [i for i in range(a.chunk - a.context, a.chunk + a.context + 1)
               if 0 <= i < len(chunks) and chunks[i]["source"] == src]
    else:
        srcs = sorted({c["source"] for c in chunks if str(c["page"]) == a.page})
        if not srcs:
            raise SystemExit(f"❌ p.{a.page} 청크 없음")
        if len(srcs) > 1 and not a.source:
            raise SystemExit(f"❌ p.{a.page}가 여러 출처에 있음 — --source로 지정: {srcs}")
        src = a.source or srcs[0]
        ids = [i for i, c in enumerate(chunks) if c["source"] == src and str(c["page"]) == a.page]
        if not ids:
            raise SystemExit(f"❌ {src} p.{a.page} 청크 없음")
    # 페이지가 바뀌는 곳은 이어 붙이지 않고 페이지별로 나눠 출력(인용 시 쪽수 표기용)
    groups: list[tuple[int, list[int]]] = []
    for i in ids:
        if groups and groups[-1][0] == chunks[i]["page"]:
            groups[-1][1].append(i)
        else:
            groups.append((chunks[i]["page"], [i]))
    print(f"=== {a.collection} · {src} ===")
    for page, gids in groups:
        text = chunks[gids[0]]["text"]
        for i in gids[1:]:
            text = _merge_overlap(text, chunks[i]["text"])
        mark = " ← 요청" if a.chunk is not None and a.chunk in gids else ""
        print(f"\n--- p.{page} (chunk {gids[0]}{'–' + str(gids[-1]) if len(gids) > 1 else ''}){mark} ---\n{text.strip()}")


def cmd_list(_):
    if not KB.exists() or not any(KB.iterdir()):
        print("(컬렉션 없음)"); return
    print("=== RAG 컬렉션 ===")
    for d in sorted(KB.iterdir()):
        idxp = d / "index.json"
        if idxp.exists():
            m = json.loads(idxp.read_text(encoding="utf-8"))["meta"]
            print(f"  {d.name}: 청크 {m['n']} · 임베딩 {m.get('dense_model') or '없음'} · 출처 {m['sources']}")


def cmd_reindex(a):
    if not (col_dir(a.collection) / "index.json").exists():
        raise SystemExit(f"❌ 컬렉션 '{a.collection}' 없음. list로 확인.")
    t0 = time.time()
    n = reindex_tfidf(a.collection)
    print(f"✅ '{a.collection}' TF-IDF 재생성: 청크 {n}개 (임베딩 유지) · {_elapsed(t0)}")


def cmd_drop(a):
    import shutil
    d = col_dir(a.collection)
    if d.exists():
        shutil.rmtree(d); print(f"🗑️ '{a.collection}' 삭제")
    else:
        print(f"(없음: {a.collection})")


def main():
    # Windows 콘솔(cp949)에서 이모지·특수문자 출력 시 UnicodeEncodeError 방지
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description="범용 RAG (TF-IDF + 로컬 임베딩 하이브리드)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("ingest", help="파일 흡수")
    s.add_argument("files", nargs="+"); s.add_argument("--collection", required=True)
    s.add_argument("--model", help=f"임베딩 모델 (기본 {DEFAULT_MODEL}; --append면 기존 컬렉션 모델)")
    s.add_argument("--no-dense", action="store_true", help="임베딩 생략, TF-IDF만")
    s.add_argument("--append", action="store_true",
                   help="기존 컬렉션에 추가(새 청크만 임베딩, 같은 파일명은 교체). 없으면 새로 생성")
    s.set_defaults(func=cmd_ingest)

    s = sub.add_parser("search", help="검색")
    s.add_argument("query", nargs="+", help="질의(여러 개면 모델을 한 번만 로드해 차례로 검색)")
    s.add_argument("--collection", required=True)
    s.add_argument("-k", type=int, default=5)
    s.add_argument("--brief", action="store_true", help="청크 앞 240자만(사람이 훑어볼 때). 기본은 전문")
    s.add_argument("--mode", choices=["auto", "hybrid", "dense", "tfidf"], default="auto",
                   help="auto=임베딩 있으면 hybrid, 없으면 tfidf")
    s.set_defaults(func=cmd_search)

    s = sub.add_parser("show", help="원문 보기(청크 번호+앞뒤 또는 페이지) — 인용·검증용")
    s.add_argument("--collection", required=True)
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--chunk", type=int, help="search 결과의 chunk 번호")
    g.add_argument("--page", help="페이지 번호(해당 페이지 청크 전부). 조문 단위 컬렉션은 \"제29조\" 등")
    s.add_argument("--context", type=int, default=1, help="--chunk 앞뒤로 함께 볼 청크 수(기본 1)")
    s.add_argument("--source", help="--page가 여러 출처에 있을 때 파일명")
    s.set_defaults(func=cmd_show)

    sub.add_parser("list", help="컬렉션 목록").set_defaults(func=cmd_list)

    s = sub.add_parser("reindex", help="TF-IDF만 재생성(임베딩 유지, 토큰화 변경 반영용)")
    s.add_argument("--collection", required=True); s.set_defaults(func=cmd_reindex)

    s = sub.add_parser("drop", help="컬렉션 삭제")
    s.add_argument("--collection", required=True); s.set_defaults(func=cmd_drop)

    a = ap.parse_args(); a.func(a)


if __name__ == "__main__":
    main()
