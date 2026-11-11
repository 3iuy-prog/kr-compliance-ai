#!/usr/bin/env python3
"""문서에서 관계(그래프의 간선)를 추출해 data/relations/에 저장하고 조회한다.

RAG 검색은 상위 몇 개만 돌려주므로 "이 조문에 걸린 항목 전부" 같은 역방향·전수 질의를 빠짐없이 못 한다.
그래서 원문에 관계가 **명시적으로 적힌** 문서는 인그레스트할 때(rag.py ingest가 자동 호출) 관계를 뽑아 둔다.

추출기(문서 종류를 내용으로 판별):
  isms_related_laws  ISMS-P 인증기준 안내서 — 항목 머리말 표의 "관련 법규" 칸 → (항목 → 법령/고시 조문)

추출한 관계는 원문과 자동 대조해 status를 단다(근거로 쓰기 전 확인 수준):
  verified       현행 원문에 그 조문이 있고 조문 제목이 같다
  title_changed  조문은 있으나 제목이 다르다(안내서 발간 후 개정 가능성 — 원문 확인 필요)
  not_found      현행 원문에 그 조문이 없다(삭제·이동 가능성)
  no_source      대조할 원문이 로컬에 없다(대상 5법·안전성확보조치 기준 외)
  no_article     조문 없이 법령·지침 이름만 적혀 있다
  법령 원문 = legalize-kr의 오늘 기준 시행본, 고시 원문 = data/kb_src의 안전성 확보조치 기준 안내서에 실린 고시 전문.

사용:
  python tools/relations.py extract data/kb_src/ISMS-P_인증기준_안내서_2023.11.23.md   # 보통은 ingest가 자동 호출
  python tools/relations.py by-law 개인정보보호법 --jo 29       # 역방향: 이 조문에 걸린 항목 전부
  python tools/relations.py by-item 2.9.4                       # 정방향
  python tools/relations.py list
"""
from __future__ import annotations
import argparse
import datetime
import json
import re
import sys
from collections import Counter
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
REL_DIR = ROOT / "data" / "relations"
KB_SRC = ROOT / "data" / "kb_src"
sys.path.insert(0, str(TOOLS))

ART = re.compile(r"제\s?(\d+)조(?:의\s?(\d+))?\s*\(([^)]*)\)")
HEAD_TITLE = re.compile(r"^#+\s*(제\d+조(?:의\d+)?)\s*\(([^)]*)\)", re.M)          # legalize-kr 조문 헤딩
NOTICE_TITLE = re.compile(r"^(?:- |\d+\. )?(제\d+조(?:의\d+)?)\(([^)]*)\)", re.M)   # 안내서 속 고시 전문(docling이 붙인 목록 번호 무시)

# 이름(공백 제거) → (종류, 표준 이름). 대조 가능한 것만. 나머지는 other.
NAMES = {
    "개인정보보호법": ("law", "개인정보보호법"),
    "정보통신망법": ("law", "정보통신망법"),
    "정보통신망이용촉진및정보보호등에관한법률": ("law", "정보통신망법"),
    "신용정보법": ("law", "신용정보법"),
    "신용정보의이용및보호에관한법률": ("law", "신용정보법"),
    "위치정보법": ("law", "위치정보법"),
    "위치정보의보호및이용등에관한법률": ("law", "위치정보법"),
    "전자금융거래법": ("law", "전자금융거래법"),
    "개인정보의안전성확보조치기준": ("notice", "개인정보의 안전성 확보조치 기준"),
    "안전성확보조치기준": ("notice", "개인정보의 안전성 확보조치 기준"),   # crossmap.json 표기
}


def _norm(s: str) -> str:
    return re.sub(r"[\s·ㆍ・]", "", s)


def canon(name: str) -> tuple[str, str, str]:
    """(종류 law|notice|other, 표준 이름, 법령 종류 법률|시행령)"""
    key, kind = _norm(name), "법률"
    if key.endswith("시행령"):
        key, kind = key[:-3], "시행령"
    typ, std = NAMES.get(key, ("other", name.strip()))
    return typ, std, kind


# ---------- 원문 조문 제목(대조 기준) ----------
_cache: dict = {}


def legalize_state() -> str:
    """대조 전에 legalize-kr를 원격 최신본으로 동기화(legalize.py와 같은 규칙: 24시간 지났으면 fetch,
    오프라인이면 경고 후 로컬본)하고, 대조에 쓴 커밋을 돌려준다 — 결과 파일에 기록."""
    if "legalize_head" not in _cache:
        import legalize
        legalize.sync()
        head = legalize._git_try("log", "-1", "--format=%h %cs").stdout.strip()
        fetched = legalize.REPO / ".git" / "FETCH_HEAD"
        when = (datetime.datetime.fromtimestamp(fetched.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                if fetched.exists() else "기록 없음")
        _cache["legalize_head"] = f"legalize-kr {head} (마지막 원격 확인 {when})"
    return _cache["legalize_head"]


def law_titles(std: str, kind: str) -> tuple[dict[str, str], str] | None:
    """legalize-kr 오늘 기준 시행본의 {조: 제목}, 기준 설명."""
    k = ("law", std, kind)
    if k not in _cache:
        legalize_state()
        try:
            import legalize
            p = legalize.law_path(std, kind)
            rel = p.relative_to(legalize.REPO).as_posix()
            v = legalize._inforce_commit(rel, legalize.TODAY)
            text = legalize.git("show", f"{v['commit']}:{rel}") if v else p.read_text(encoding="utf-8")
            basis = f"legalize-kr {std} {kind} {legalize.TODAY} 시행본" + (f"(시행 {v['시행']})" if v else "")
            _cache[k] = ({m.group(1): m.group(2).strip() for m in HEAD_TITLE.finditer(text)}, basis)
        except (SystemExit, Exception):
            _cache[k] = None
    return _cache[k]


def notice_titles() -> tuple[dict[str, str], str] | None:
    """data/kb_src의 안전성 확보조치 기준 안내서(가장 최신 파일명)에 실린 고시 전문의 {조: 제목}."""
    if "notice" not in _cache:
        srcs = sorted(KB_SRC.glob("*안전성확보조치*안내서*.md"))
        _cache["notice"] = None
        for src in reversed(srcs):
            t = src.read_text(encoding="utf-8")
            m = re.search(r"고시 (제\d{4}-\d+호)", t)
            s, e = (m.start() if m else -1), t.find("## 부칙")
            if s < 0 or e < s:
                continue
            titles = {}
            for a in NOTICE_TITLE.finditer(t[s:e]):
                titles.setdefault(a.group(1), a.group(2).strip())
            _cache["notice"] = (titles, f"{src.name} 고시 전문({m.group(1)})")
            break
    return _cache["notice"]


def verify(typ: str, std: str, kind: str, jo: str | None, title: str | None) -> dict:
    if not jo:
        return {"status": "no_article"}
    src = law_titles(std, kind) if typ == "law" else notice_titles() if typ == "notice" else None
    if not src:
        return {"status": "no_source"}
    titles, basis = src
    if jo not in titles:
        return {"status": "not_found", "basis": basis}
    cur = titles[jo]
    return {"status": "verified" if _norm(cur) == _norm(title or "") else "title_changed",
            "current_title": cur, "basis": basis}


# ---------- 추출기: ISMS-P "관련 법규" ----------
def _parse_related(cell: str) -> list[tuple[str, str | None, str | None]]:
    """'개인정보 보호법 제29조(안전조치의무), 제31조(…)  정보통신망법 제45조의3(…)' → [(이름, 조, 제목)].
    법령 사이는 공백 두 칸 이상(변환 시 지운 글머리 아이콘 자리)."""
    out = []
    for e in (x.strip(" ,") for x in re.split(r"\s{2,}", cell)):
        if "?" in e:                          # 옆 칸 문장(…있는가?)이 섞여 들어온 경우
            e = e.split("?")[-1].strip(" ,")
        if not e:
            continue
        arts = list(ART.finditer(e))
        name = (e[:arts[0].start()] if arts else e).strip(" ,")
        if not name and out:                  # 이름 없이 조문만 이어지면 앞 법령
            name = out[-1][0]
        if not arts:
            out.append((name, None, None))
        for m in arts:
            jo = f"제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "")
            out.append((name, jo, re.sub(r"\s+", " ", m.group(3)).strip()))
    return out


def _is_isms(text: str) -> bool:
    import rag
    return any(rag.ITEM_RE.match(l) for l in text.splitlines()) and "| 관련 법규 |" in text


def extract_isms(md: Path) -> list[dict]:
    import rag
    rels, item = [], None
    for page, text in rag.extract_text(md):
        for line in text.splitlines():
            m = rag.ITEM_RE.match(line)
            if m:
                item = {"type": "isms", "id": m.group(1), "name": m.group(2).strip(), "page": page}
            elif line.startswith("| 관련 법규 |") and item:
                cell = line.split("|")[2]
                for name, jo, title in _parse_related(cell):
                    typ, std, kind = canon(name)
                    to = {"type": typ, "name": std, "kind": kind if typ == "law" else None,
                          "article": jo, "title_in_source": title}
                    rels.append({"from": item, "to": to, **verify(typ, std, kind, jo, title)})
    return rels


EXTRACTORS = [("isms_related_laws", _is_isms, extract_isms)]


def extract_auto(md: Path, quiet: bool = False) -> Path | None:
    """맞는 추출기가 있으면 관계를 뽑아 data/relations/<문서>.json에 저장. rag.py ingest가 호출."""
    text = md.read_text(encoding="utf-8", errors="ignore")
    for name, detect, fn in EXTRACTORS:
        if not detect(text):
            continue
        rels = fn(md)
        REL_DIR.mkdir(parents=True, exist_ok=True)
        out = REL_DIR / f"{md.stem}.json"
        st = Counter(r["status"] for r in rels)
        meta = {"source": md.name, "extractor": name, "extracted": datetime.date.today().isoformat(),
                "basis": sorted({r["basis"] for r in rels if r.get("basis")}),
                "legalize": _cache.get("legalize_head", "사용 안 함"), "status_count": dict(st),
                "note": "status는 원문 조문 제목 자동 대조 결과. verified 외는 근거로 쓰기 전 원문 확인."}
        out.write_text(json.dumps({"_meta": meta, "relations": rels}, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        if not quiet:
            items = len({r["from"]["id"] for r in rels})
            print(f"🔗 관계 추출({name}): 항목 {items}개 → 간선 {len(rels)}개 · "
                  + " · ".join(f"{k} {v}" for k, v in st.most_common()) + f" → {out.relative_to(ROOT)}")
        return out
    return None


# ---------- 조회 ----------
def load_all() -> list[dict]:
    out = []
    for f in sorted(REL_DIR.glob("*.json")) if REL_DIR.exists() else []:
        d = json.loads(f.read_text(encoding="utf-8"))
        out += [{**r, "_doc": d["_meta"]["source"]} for r in d["relations"]]
    return out


MARK = {"verified": "✅", "title_changed": "⚠️ 제목 다름", "not_found": "❌ 현행 원문에 없음",
        "no_source": "· 원문 미보유", "no_article": "· 조문 없음"}


def by_law(name: str, jo: str | None = None) -> list[dict]:
    typ, std, kind = canon(name)
    jo = None if not jo else (jo if jo.startswith("제") else f"제{jo}조")
    return [r for r in load_all()
            if (r["to"]["name"] == std or _norm(r["to"]["name"]) == _norm(name))
            and (not jo or r["to"]["article"] == jo)]


def fmt(r: dict) -> str:
    to, fr = r["to"], r["from"]
    t = f"{to['name']} {to['article'] or ''}({to['title_in_source'] or ''})".replace("()", "")
    extra = f" → 현행: {r['current_title']}" if r["status"] == "title_changed" else ""
    return f"ISMS-P {fr['id']} {fr['name']} (p.{fr['page']})  ↔  {t}  {MARK.get(r['status'], r['status'])}{extra}"


def cmd_extract(a):
    if not extract_auto(Path(a.md)):
        raise SystemExit("❌ 이 문서에 맞는 관계 추출기가 없음(현재: ISMS-P 안내서 '관련 법규')")


def cmd_by_law(a):
    hits = by_law(a.name, a.jo)
    print(f"=== {a.name} {a.jo or ''} ← 원문에 '관련 법규'로 적힌 항목 {len(hits)}건 (자동 추출) ===")
    for r in sorted(hits, key=lambda r: [int(x) for x in r["from"]["id"].split(".")]):
        print("  " + fmt(r))


def cmd_by_item(a):
    hits = [r for r in load_all() if r["from"]["id"] == a.code]
    print(f"=== ISMS-P {a.code} → 관련 법규 {len(hits)}건 (자동 추출) ===")
    for r in hits:
        print("  " + fmt(r))


def cmd_list(_):
    for f in sorted(REL_DIR.glob("*.json")) if REL_DIR.exists() else []:
        m = json.loads(f.read_text(encoding="utf-8"))["_meta"]
        print(f"{f.name}: {m['extractor']} · {m['extracted']} · {m['status_count']}")


def main():
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="문서 관계 추출·조회")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("extract"); s.add_argument("md"); s.set_defaults(func=cmd_extract)
    s = sub.add_parser("by-law"); s.add_argument("name"); s.add_argument("--jo"); s.set_defaults(func=cmd_by_law)
    s = sub.add_parser("by-item"); s.add_argument("code"); s.set_defaults(func=cmd_by_item)
    sub.add_parser("list").set_defaults(func=cmd_list)
    a = ap.parse_args(); a.func(a)


if __name__ == "__main__":
    main()
