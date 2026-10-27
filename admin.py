"""Seller-side CLI (box-local only). Usage: .venv/bin/python admin.py
  list | show <contextId> | pending | notify-next | confirm <proposalId> | reject <proposalId> | counter <proposalId> <price> | delete <contextId> | reset-tests"""
import json, sys
from negotiation import db, now

def rows(c, q, a=()): return [dict(r) for r in c.execute(q, a)]

def main(a):
    c = db()
    if not a: print(__doc__); return 1
    cmd = a[0]
    if cmd == "list":
        for n in rows(c, "SELECT * FROM negotiations ORDER BY created_at"):
            print(json.dumps(n, ensure_ascii=False))
            for p in rows(c, "SELECT * FROM proposals WHERE context_id=? ORDER BY version", (n["context_id"],)):
                print("   ", json.dumps(p, ensure_ascii=False))
    elif cmd == "show" and len(a) == 2:
        print(json.dumps({"negotiation": rows(c, "SELECT * FROM negotiations WHERE context_id=?", (a[1],)),
                          "messages": rows(c, "SELECT ts,role,round,offer,text FROM messages WHERE context_id=? ORDER BY id", (a[1],)),
                          "proposals": rows(c, "SELECT * FROM proposals WHERE context_id=? ORDER BY version", (a[1],))},
                         ensure_ascii=False, indent=1))
    elif cmd == "pending":
        print(json.dumps(rows(c, "SELECT * FROM proposals WHERE status='pending_seller' ORDER BY created_at"), ensure_ascii=False, indent=1))
    elif cmd == "notify-next":
        out = []
        with c:
            for p in rows(c, "SELECT * FROM proposals WHERE notified_at IS NULL AND status='pending_seller' ORDER BY created_at"):
                lm = c.execute("SELECT text FROM messages WHERE context_id=? AND role='buyer' ORDER BY id DESC LIMIT 1", (p["context_id"],)).fetchone()
                out.append({"id": p["id"], "version": p["version"], "object": p["object_id"], "price_eur": p["price"],
                            "conditions": p["conditions"], "flags": json.loads(p["flags"]), "negotiation_id": p["context_id"],
                            "last_buyer_message": lm[0] if lm else None, "created_at": p["created_at"]})
                c.execute("UPDATE proposals SET notified_at=? WHERE id=?", (now(), p["id"]))
        print(json.dumps(out, ensure_ascii=False, indent=1) if out else "[]")
    elif cmd in ("confirm", "reject") and len(a) == 2:
        p = c.execute("SELECT * FROM proposals WHERE id=?", (a[1],)).fetchone()
        if not p: print("ERREUR: proposition inconnue"); return 2
        latest = c.execute("SELECT MAX(version) FROM proposals WHERE context_id=?", (p["context_id"],)).fetchone()[0]
        if p["status"] != "pending_seller" or p["version"] != latest:
            print(f"ERREUR: proposition non modifiable (statut {p['status']}, version {p['version']}, dernière v{latest})"); return 3
        st = "accepted_fictive" if cmd == "confirm" else "refused"
        with c: c.execute("UPDATE proposals SET status=?, decided_at=? WHERE id=? AND status='pending_seller'", (st, now(), a[1]))
        print(f"OK: {a[1]} -> {st}. L'acheteur sera informé à son prochain message dans la négociation.")
    elif cmd == "counter" and len(a) == 3:
        p = c.execute("SELECT * FROM proposals WHERE id=?", (a[1],)).fetchone()
        if not p: print("ERREUR: proposition inconnue"); return 2
        try: price = int(a[2])
        except ValueError: print("ERREUR: prix invalide"); return 4
        latest = c.execute("SELECT MAX(version) FROM proposals WHERE context_id=?", (p["context_id"],)).fetchone()[0]
        if p["status"] != "pending_seller" or p["version"] != latest:
            print(f"ERREUR: proposition non modifiable (statut {p['status']}, version {p['version']}, dernière v{latest})"); return 3
        with c: c.execute("UPDATE proposals SET status='countered', counter_price=?, decided_at=? WHERE id=? AND status='pending_seller'", (price, now(), a[1]))
        print(f"OK: {a[1]} -> countered à {price} EUR. L'acheteur sera informé à son prochain message dans la négociation.")
    elif cmd == "delete" and len(a) == 2:
        with c:
            k = [c.execute(f"DELETE FROM {t} WHERE context_id=?", (a[1],)).rowcount for t in ("messages", "proposals", "negotiations")]
        print(f"OK: supprimé messages={k[0]} propositions={k[1]} négociations={k[2]} pour {a[1]}")
    elif cmd == "reset-tests":
        with c:
            for t in ("messages", "proposals", "negotiations"): c.execute(f"DELETE FROM {t}")
        print("OK: toutes les négociations et propositions ont été supprimées.")
    else:
        print(__doc__); return 1
    return 0

if __name__ == "__main__": sys.exit(main(sys.argv[1:]))
