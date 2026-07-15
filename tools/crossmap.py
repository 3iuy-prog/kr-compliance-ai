#!/usr/bin/env python3
"""크로스매핑 조회 헬퍼 (P2) — ISMS-P ↔ 안전성확보조치(고시) ↔ 법령

data/crossmap.json 을 조회해 통제↔규제 매핑을 표로 반환.
"한 통제를 구현하면 어떤 법·기준을 동시 충족하나"(P2 킬러 기능) 지원.

사용:
  python tools/crossmap.py list
  python tools/crossmap.py lookup 2.6.1
  python tools/crossmap.py search 접근통제
  python tools/crossmap.py by-law 개인정보보호법 --jo 29   # 역매핑: 이 조항에 걸리는 통제
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "crossmap.json"
ISMS = Path(__file__).resolve().parent.parent / "data" / "isms_p.json"


def load() -> dict:
    return json.loads(DB.read_text(encoding="utf-8"))


def load_isms() -> dict:
    return json.loads(ISMS.read_text(encoding="utf-8")) if ISMS.exists() else {}


def _ref(r: dict) -> str:
    v = "✅" if r.get("verified") else "⚠️"
    return f"[{r['layer']}] {r['name']} {r['article']}({r['title']}) {v}"


def _row(c: dict) -> str:
    isms = c["isms"]
    notice = " / ".join(_ref(n) for n in c.get("notice", [])) or "-"
    law = " / ".join(_ref(l) for l in c.get("law", [])) or "-"
    return (f"■ ISMS-P {isms['id']} {isms['name']} ({isms['domain']})\n"
            f"   고시: {notice}\n"
            f"   법령: {law}\n"
            f"   비고: {c.get('note','')}")


def cmd_list(_):
    db = load()
    m = db["_meta"]
    print(f"=== {m['title']} ===  ({m['isms_version']})")
    print(f"검증표기: ✅ 원문대조 완료 / ⚠️ 미검증(MCP 확인 필요)\n")
    for c in db["controls"]:
        print(_row(c)); print()


def cmd_lookup(a):
    db = load()
    hits = [c for c in db["controls"] if c["isms"]["id"] == a.id]
    if not hits:
        raise SystemExit(f"❌ ISMS-P {a.id} 매핑 없음. list로 확인.")
    for c in hits:
        print(_row(c))


def cmd_search(a):
    db = load()
    q = a.query
    hits = [c for c in db["controls"]
            if q in json.dumps(c, ensure_ascii=False)]
    if not hits:
        raise SystemExit(f"❌ '{q}' 매칭 없음.")
    print(f"=== '{q}' 검색: {len(hits)}건 ===\n")
    for c in hits:
        print(_row(c)); print()


def cmd_by_law(a):
    """역매핑: 특정 법령 조항에 매핑된 통제 나열."""
    db = load()
    jo = a.jo if not a.jo or a.jo.startswith("제") else f"제{a.jo}조"
    hits = [c for c in db["controls"]
            if any(l["name"] == a.name and (not jo or l["article"] == jo)
                   for l in c.get("law", []))]
    if not hits:
        raise SystemExit(f"❌ {a.name} {jo or ''}에 매핑된 통제 없음.")
    print(f"=== {a.name} {jo or '(전체)'} ← 관련 통제 {len(hits)}건 ===\n")
    for c in hits:
        print(_row(c)); print()


def cmd_validate(_):
    """crossmap의 ISMS-P 코드가 data/isms_p.json에 실재하는지 정합성 검사."""
    db, isms = load(), load_isms()
    if not isms:
        raise SystemExit("❌ data/isms_p.json 없음.")
    known = {c["id"]: c["name"] for c in isms.get("controls", [])}
    ok, bad = 0, []
    for c in db["controls"]:
        cid, cname = c["isms"]["id"], c["isms"]["name"]
        if cid not in known:
            bad.append(f"  ❌ {cid} — isms_p.json에 없는 코드")
        elif known[cid] != cname:
            bad.append(f"  ⚠️ {cid} 명칭 불일치: crossmap='{cname}' vs isms_p='{known[cid]}'")
        else:
            ok += 1
    print(f"=== 정합성 검사: crossmap ↔ isms_p.json ===")
    print(f"일치 {ok} / 전체 {len(db['controls'])}")
    print("\n".join(bad) if bad else "✅ 모든 ISMS-P 코드·명칭 일치")


def cmd_isms(a):
    """ISMS-P 항목/카테고리 조회 (L3 정형화 데이터)."""
    isms = load_isms()
    if not isms:
        raise SystemExit("❌ data/isms_p.json 없음.")
    if a.id:
        hits = [c for c in isms["controls"] if c["id"] == a.id or c["id"].startswith(a.id + ".")]
        cats = {c["id"]: c["name"] for c in isms["categories"]}
        for c in hits:
            v = "✅" if c.get("verified") else "⚠️"
            print(f"  ISMS-P {c['id']} {c['name']}  [{cats.get(c['category'],'')}] {v}")
        if not hits:
            raise SystemExit(f"❌ {a.id} 없음.")
    else:
        m = isms["_meta"]
        print(f"=== {m['title']} ===\n{m['structure']}\n")
        for cat in isms["categories"]:
            n = sum(1 for c in isms["controls"] if c["category"] == cat["id"])
            print(f"  {cat['id']} {cat['name']}  (시드 {n}개)")


def main():
    if not DB.exists():
        raise SystemExit(f"❌ {DB} 없음.")
    ap = argparse.ArgumentParser(description="크로스매핑 조회 (P2)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="전체 매핑").set_defaults(func=cmd_list)

    s = sub.add_parser("lookup", help="ISMS-P 코드로 조회")
    s.add_argument("id"); s.set_defaults(func=cmd_lookup)

    s = sub.add_parser("search", help="키워드 검색")
    s.add_argument("query"); s.set_defaults(func=cmd_search)

    s = sub.add_parser("by-law", help="법령 조항 역매핑")
    s.add_argument("name"); s.add_argument("--jo", help="조문 (예: 29, 제29조)")
    s.set_defaults(func=cmd_by_law)

    s = sub.add_parser("isms", help="ISMS-P 항목 조회(L3)")
    s.add_argument("id", nargs="?", help="코드/카테고리 (예: 2.5, 2.6.1). 생략 시 전체 구조")
    s.set_defaults(func=cmd_isms)

    sub.add_parser("validate", help="crossmap↔isms_p 정합성 검사").set_defaults(func=cmd_validate)

    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
