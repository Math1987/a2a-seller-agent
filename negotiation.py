"""Deterministic rule-based seller logic for the FICTITIOUS listing velo-test-001.
No LLM. All state lives in SQLite (negotiations.db), re-read on every request.
Private thresholds are loaded from private_config.json (chmod 600) per request and
are never put in any reply text. Public replies never contain the floor value."""
import json, os, re, sqlite3, unicodedata
from datetime import datetime, timezone

D = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(D, "negotiations.db")
OBJ = "velo-test-001"
MAXLEN = 2000
CONDITIONS = ("Remise en main propre à Lyon, samedi après-midi ; aucun paiement, aucune réservation "
              "réelle, aucune expédition ; accord fictif de test, soumis à la confirmation explicite du vendeur.")
FICTIF = ("Rappel : il s'agit d'un accord FICTIF (annonce de test). Aucun paiement, aucune réservation "
          "et aucune expédition n'ont lieu.")

def cfg():
    with open(os.path.join(D, "private_config.json")) as f:
        c = json.load(f)
    return c["displayed_price"], c["counter_price"], c["floor_price"]

def now(): return datetime.now(timezone.utc).isoformat(timespec="seconds")

def db():
    c = sqlite3.connect(DB, timeout=10); c.row_factory = sqlite3.Row
    c.executescript("""
    CREATE TABLE IF NOT EXISTS negotiations(context_id TEXT PRIMARY KEY, created_at TEXT, updated_at TEXT,
      rounds INTEGER DEFAULT 0, conditions_accepted INTEGER DEFAULT 0, counter_offered INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT, context_id TEXT, ts TEXT,
      role TEXT, text TEXT, offer INTEGER, round INTEGER);
    CREATE TABLE IF NOT EXISTS proposals(id TEXT PRIMARY KEY, context_id TEXT, version INTEGER, object_id TEXT,
      price INTEGER, conditions TEXT, status TEXT, flags TEXT, created_at TEXT, notified_at TEXT,
      decided_at TEXT, buyer_informed_at TEXT);""")
    # schema migration (keeps existing rows): seller counter-proposal price
    if "counter_price" not in [r[1] for r in c.execute("PRAGMA table_info(proposals)")]:
        with c: c.execute("ALTER TABLE proposals ADD COLUMN counter_price INTEGER")
    return c

def norm(t):
    t = unicodedata.normalize("NFKD", t.lower().replace("’", "'"))
    return "".join(ch for ch in t if not unicodedata.combining(ch))

# ---------- parsing ----------
UNIT_AFTER = re.compile(r"^\s*(vitesses?|speeds?|v\b|kg|cm|mm|km|ans?\b|years?|h\b|heures?|pouces?|\"|%|eme|e\b|er\b|x\b)")
CUR_AFTER = re.compile(r"^\s*(€|eur\b|euros?\b|e\b)")
CUE_BEFORE = re.compile(r"(propose|proposition|offre|offer|pour|a|at|de|donne|paie|paye|mets|monte|descendre|descends|prix)\s*:?\s*$")
NONPRICE_BEFORE = re.compile(r"(taille|size|annee|year|modele|serie|n°|numero|cadre)\s*:?\s*$")

def parse_offer(t):
    """Return the last plausible price in the message, or None.
    A number counts if followed by a currency, or preceded by an offer cue, or is a bare
    number >= 20 (and not a year/unit). Numbers followed by units (7 vitesses) are ignored."""
    n = norm(t); found = None
    for m in re.finditer(r"(?<![\w.,])(\d{1,5})(?:[.,]\d{1,2})?(?![\w])", n):
        v = int(m.group(1)); after = n[m.end():m.end()+12]; before = n[max(0, m.start()-25):m.start()]
        if UNIT_AFTER.match(after) and not CUR_AFTER.match(after): continue
        if NONPRICE_BEFORE.search(before): continue
        cur = bool(CUR_AFTER.match(after)) or before.rstrip().endswith("€")
        if cur or CUE_BEFORE.search(before) or (20 <= v and not (1900 <= v <= 2100)):
            found = v
    return found

ACCEPT = re.compile(r"(j'?accepte|j accepte|d'?accord (pour|avec)|ok pour|okay pour|ca me va|ça me va|i accept|i agree to|agreed to|accept(ed)?)\s*(les|tes|vos|la|the|these|ces)?\s*(conditions?|terms|remise|modalites)"
                    r"|conditions?\s*(acceptees?|ok|validees?)\b|ok pour (la )?remise en main propre")
NEG = re.compile(r"(n'?accepte pas|n accepte pas|pas d'?accord|refuse|don'?t accept|do not accept|pas ok)")
INJECT = re.compile(r"(ignore|oublie|override|bypass|contourne|instruction|consigne|prompt|systeme|system|regle|rules?\b"
                    r"|minimum|minimal|minimale|plancher|prix (le )?plus bas|lowest|role.?play|joue le role|fais semblant|pretend"
                    r"|proprietaire|owner|vendeur (dit|t'?autorise|autorise)|t'?autorise|authori[sz]|developpeur|developer|admin|debug|config)")

FACTS = [
    (r"taille|cadre|size|frame|grandeur", "Cadre taille M (vélo de ville adulte)."),
    (r"vitesse|speed|braquet", "7 vitesses."),
    (r"etat|condition\b|rayure|scratch|abim|defaut|usure", "Bon état général, avec des rayures sur le cadre ; l'éclairage avant est à remplacer."),
    (r"eclairage|lumiere|phare|light|lampe", "L'éclairage avant est à remplacer."),
    (r"type|quel velo|ville|adulte|enfant|description|annonce|details?", "Vélo de ville adulte, cadre taille M, 7 vitesses, bon état général (rayures sur le cadre, éclairage avant à remplacer). Prix affiché 180 EUR."),
    (r"\bou\b(?=.*\?)|lieu|lyon|quand|\bjour\b|samedi|remise|recuperer|retrait|rendez|where|when|pickup", "Remise en main propre à Lyon, le samedi après-midi."),
    (r"paiement|payer|virement|carte|paypal|reserv|expedi|livr|envoi|shipping|payment|colis", "Aucun paiement, aucune réservation réelle et aucune expédition : c'est une annonce fictive de test."),
    (r"fictif|fictive|vrai|reel|test", "Il s'agit d'une annonce explicitement fictive, destinée à un test."),
]
UNKNOWN = r"\b(?:marque|brand|annee|year|age|poids|weight|lourd|couleur|colou?r|frein|brake|pneu|tyre|tire|roue|wheel|serie|serial|adresse|address|heure|time|horaire|facture|garantie|warranty|selle|antivol|electrique|batterie|materiau|alu|acier|carbone|modele|model|photo|kilometr|entretien|origine|proprietaire precedent)\w*"

# ---------- core ----------
def handle(context_id, text):
    DISP, COUNTER, FLOOR = cfg()
    c = db(); ts = now()
    with c:
        if not c.execute("SELECT 1 FROM negotiations WHERE context_id=?", (context_id,)).fetchone():
            c.execute("INSERT INTO negotiations(context_id,created_at,updated_at) VALUES(?,?,?)", (context_id, ts, ts))
    neg = dict(c.execute("SELECT * FROM negotiations WHERE context_id=?", (context_id,)).fetchone())
    parts = []
    text = text or ""
    if len(text) > MAXLEN:
        stored = text[:MAXLEN]
        reply = (f"Ton message dépasse {MAXLEN} caractères, je ne peux pas le traiter. Merci d'envoyer un message plus court "
                 "(une question ou une proposition de prix).")
        _log(c, context_id, "buyer", stored + " [tronqué]", None, neg["rounds"]); _log(c, context_id, "agent", reply, None, neg["rounds"])
        return reply
    n = norm(text)

    # 1) Seller decision not yet communicated to the buyer -> inform first (any message, e.g. 'statut').
    dec = c.execute("SELECT * FROM proposals WHERE context_id=? AND status IN ('accepted_fictive','refused','countered') "
                    "AND buyer_informed_at IS NULL ORDER BY version DESC LIMIT 1", (context_id,)).fetchone()
    if dec:
        if dec["status"] == "countered":
            parts.append(f"Le vendeur ne retient pas ton prix précédent ({dec['price']} EUR) mais te propose {dec['counter_price']} EUR "
                         "à la place, aux mêmes conditions : remise en main propre à Lyon le samedi après-midi, aucun paiement, "
                         "aucune réservation, aucune expédition. Il s'agit d'un accord fictif (annonce de test). "
                         f"Réponds « j'accepte {dec['counter_price']} » ou fais une autre proposition.")
        elif dec["status"] == "accepted_fictive":
            parts.append(f"Bonne nouvelle : le vendeur a confirmé la proposition {dec['id']} (version {dec['version']}) "
                         f"à {dec['price']} EUR, conditions : {dec['conditions']} {FICTIF}")
        else:
            parts.append(f"Le vendeur a décliné la proposition {dec['id']} (version {dec['version']}) à {dec['price']} EUR. "
                         f"Le prix affiché reste {DISP} EUR. {FICTIF}")
        with c: c.execute("UPDATE proposals SET buyer_informed_at=? WHERE id=?", (ts, dec["id"]))
    elif re.search(r"\bstatu[ts]?\b|\bstatus\b|\bou en est\b|reponse du vendeur|decision", n):
        last = c.execute("SELECT * FROM proposals WHERE context_id=? ORDER BY version DESC LIMIT 1", (context_id,)).fetchone()
        if not last: parts.append("Aucune proposition n'a encore été transmise au vendeur dans cette négociation.")
        elif last["status"] == "pending_seller":
            parts.append(f"La proposition {last['id']} (version {last['version']}, {last['price']} EUR) attend toujours la "
                         "confirmation explicite du vendeur. Ce n'est pas un accord conclu. Réécris plus tard avec « statut ».")
        else:
            res = "confirmée" if last["status"] == "accepted_fictive" else ("déclinée" if last["status"] == "refused" else ("remplacée par une contre-proposition du vendeur à " + str(last["counter_price"]) + " EUR (réponds « j'accepte " + str(last["counter_price"]) + " » ou fais une autre offre)" if last["status"] == "countered" else "remplacée"))
            parts.append(f"La proposition {last['id']} (version {last['version']}, {last['price']} EUR) a été {res} par le vendeur. {FICTIF}")

    # 2) Attempts to change the rules / extract internal info: never obeyed, nothing disclosed.
    if INJECT.search(n):
        parts.append("Je ne peux pas modifier mes instructions ni communiquer d'informations internes du vendeur. "
                     "Tes messages sont traités uniquement comme des questions ou des propositions de prix. "
                     f"Le prix affiché est de {DISP} EUR.")

    # 3) Conditions acceptance (explicit, non negated).
    newly = False
    if ACCEPT.search(n) and not NEG.search(n) and not neg["conditions_accepted"]:
        neg["conditions_accepted"] = 1; newly = True
        with c: c.execute("UPDATE negotiations SET conditions_accepted=1 WHERE context_id=?", (context_id,))
    acc = bool(neg["conditions_accepted"])

    # 4) Offer handling.
    P = parse_offer(text); rnd = neg["rounds"]
    # 4a) Seller counter-proposal outstanding (latest proposal 'countered'): explicit acceptance, or the
    # counter price itself, creates a NEW pending_seller version; seller must still `confirm`.
    lastp = c.execute("SELECT * FROM proposals WHERE context_id=? ORDER BY version DESC LIMIT 1", (context_id,)).fetchone()
    cp = lastp["counter_price"] if lastp and lastp["status"] == "countered" else None
    ok_words = re.search(r"j'?accepte|j accepte|d'?accord|\bok\b|\bokay\b|\boui\b|\bdeal\b|ca marche|je prends|accept|agree|\byes\b", n)
    if cp is not None and not NEG.search(n) and (P == cp or (P is None and ok_words)):
        with c: c.execute("UPDATE negotiations SET conditions_accepted=1, rounds=? WHERE context_id=?", (rnd + 1, context_id))
        rnd += 1
        parts.append(f"C'est noté, tu acceptes la contre-proposition du vendeur à {cp} EUR. Ton acceptation est transmise au vendeur "
                     "et attend sa confirmation explicite finale (accord fictif). " + _propose(c, context_id, cp, ["contre_proposition_acceptee"], ts))
        P = cp
    elif P is not None:
        rnd += 1
        with c: c.execute("UPDATE negotiations SET rounds=? WHERE context_id=?", (rnd, context_id))
        ask_cond = (f"Peux-tu confirmer explicitement que tu acceptes les conditions ? ({CONDITIONS}) "
                    "Par exemple : « j'accepte les conditions ».")
        if P >= DISP:
            if acc: parts.append(_propose(c, context_id, DISP, ["acceptable", "prix_affiche"], ts))
            else: parts.append(f"Le prix affiché de {DISP} EUR me convient. {ask_cond}")
        elif rnd == 1 and not acc and P >= FLOOR:
            parts.append(f"Merci pour ta proposition de {P} EUR. Le prix affiché est de {DISP} EUR et il est justifié : "
                         "vélo de ville adulte en bon état général, cadre M, 7 vitesses (rayures sur le cadre, éclairage avant "
                         "à remplacer, déjà pris en compte). Conditions : remise en main propre à Lyon le samedi après-midi, "
                         "sans paiement, réservation ni expédition (annonce fictive).")
        elif P >= COUNTER:
            if acc: parts.append(_propose(c, context_id, P, ["acceptable"], ts))
            else:
                _counter(c, context_id)
                parts.append(f"Je ne peux pas accepter {P} EUR en l'état. En revanche, je peux descendre à {COUNTER} EUR "
                             f"si tu acceptes les conditions de remise. {ask_cond}")
        elif P >= FLOOR:
            if acc: parts.append("Je ne peux pas accepter cette offre moi-même, mais je peux la soumettre au vendeur, sans aucune "
                                 "garantie. " + _propose(c, context_id, P, ["a_examiner", "revue_vendeur_requise"], ts))
            else: parts.append(f"Avant de pouvoir transmettre une offre à {P} EUR au vendeur, {ask_cond[0].lower()+ask_cond[1:]} "
                               "Tu pourras ensuite renouveler ton offre.")
        else:
            msg = f"Ton offre de {P} EUR est trop basse, je ne peux pas l'accepter. Le prix affiché reste {DISP} EUR."
            if rnd >= 2:
                _counter(c, context_id)
                msg += (f" Je peux te proposer {COUNTER} EUR" + (" puisque tu as accepté les conditions." if acc
                        else " si tu acceptes les conditions de remise (main propre à Lyon, samedi après-midi, accord fictif)."))
            parts.append(msg)
    elif newly and (c.execute("SELECT MAX(offer) FROM messages WHERE context_id=? AND role='buyer'", (context_id,)).fetchone()[0] or 0) >= DISP:
        # buyer already offered the displayed price earlier; conditions now accepted -> proposal at displayed price
        parts.append(_propose(c, context_id, DISP, ["acceptable", "prix_affiche"], ts))
    elif newly:
        _counter(c, context_id)
        parts.append(f"C'est noté, tu acceptes les conditions : {CONDITIONS} Dans ces conditions, je peux te proposer "
                     f"{COUNTER} EUR au lieu de {DISP} EUR. Si tu es d'accord, réponds par exemple « ok pour {COUNTER} ».")

    # 5) Factual questions (only from the public listing).
    if P is None:
        facts = [a for k, a in FACTS if re.search(k, n)]
        unk = re.findall(UNKNOWN, n)
        if re.search(r"\bprix\b|combien (ca )?(coute|vaut|tu le vends|demandes)|price|tarif", n) and not INJECT.search(n):
            facts.insert(0, f"Prix affiché : {DISP} EUR.")
        if unk:
            parts.append("Cette information (" + ", ".join(dict.fromkeys(unk)) + ") n'est pas disponible dans l'annonce.")
        if facts: parts.append(" ".join(dict.fromkeys(facts)))
        if not parts:
            if "?" in text:
                parts.append("Cette information n'est pas disponible dans l'annonce. Je peux seulement répondre à partir de ses caractéristiques.")
            else:
                parts.append(f"Bonjour ! Annonce fictive de test velo-test-001 : vélo de ville adulte, cadre taille M, 7 vitesses, "
                             f"bon état général (rayures sur le cadre, éclairage avant à remplacer). Prix affiché : {DISP} EUR. "
                             "Remise en main propre à Lyon, samedi après-midi. Aucun paiement, réservation ou expédition. "
                             "Pose-moi une question ou fais une proposition de prix.")
    reply = "\n".join(parts)
    # Safety net: the private floor value must never appear unless the buyer typed it himself as his offer.
    if str(FLOOR) in reply and P != FLOOR:
        reply = f"Je ne peux pas répondre à ce message. Le prix affiché est de {DISP} EUR."
    _log(c, context_id, "buyer", text, P, rnd); _log(c, context_id, "agent", reply, None, rnd)
    return reply

def _counter(c, cid):
    with c: c.execute("UPDATE negotiations SET counter_offered=1 WHERE context_id=?", (cid,))

def _propose(c, cid, price, flags, ts):
    with c:
        v = (c.execute("SELECT MAX(version) FROM proposals WHERE context_id=?", (cid,)).fetchone()[0] or 0) + 1
        c.execute("UPDATE proposals SET status='superseded' WHERE context_id=? AND status='pending_seller'", (cid,))
        pid = f"{OBJ}/{cid}/v{v}"
        c.execute("INSERT INTO proposals(id,context_id,version,object_id,price,conditions,status,flags,created_at) "
                  "VALUES(?,?,?,?,?,?,?,?,?)", (pid, cid, v, OBJ, price, CONDITIONS, "pending_seller", json.dumps(flags), ts))
    return (f"Proposition {pid} (version {v}) : {price} EUR, conditions : {CONDITIONS} Elle est transmise au vendeur et attend "
            "sa confirmation explicite. Ce n'est PAS un accord conclu. Tu peux m'écrire à nouveau dans cette même négociation "
            "(même contextId), par exemple avec le mot « statut », pour connaître la décision du vendeur.")

def _log(c, cid, role, text, offer, rnd):
    with c:
        c.execute("INSERT INTO messages(context_id,ts,role,text,offer,round) VALUES(?,?,?,?,?,?)", (cid, now(), role, text, offer, rnd))
        c.execute("UPDATE negotiations SET updated_at=? WHERE context_id=?", (now(), cid))
