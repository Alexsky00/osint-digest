#!/usr/bin/env python3
"""
OSINT Daily Digest — digest.py
100% gratuit : Tavily (recherche web) + Groq (LLM) + Nitter (X) + Gmail
"""

import os, json, smtplib, urllib.request, urllib.error, re, time, base64
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from pathlib import Path
from html.parser import HTMLParser

# ── SECRETS ───────────────────────────────────────────────────────────────────
TAVILY_API_KEY     = os.environ["TAVILY_API_KEY"]
GROQ_API_KEY       = os.environ["GROQ_API_KEY"]
GMAIL_USER         = os.environ["GMAIL_USER"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]

# ── INPUTS (auto = vides / manuel = workflow_dispatch) ────────────────────────
_recipients_input = os.environ.get("INPUT_RECIPIENTS", "").strip()
NOTIF_TYPE        = os.environ.get("INPUT_TYPE", "digest").strip()
ALERT_SUBJECT     = os.environ.get("INPUT_ALERT_SUBJECT", "⚡ ALERTE OSINT").strip()
ALERT_BODY_FR     = os.environ.get("INPUT_ALERT_BODY_FR", "").strip()
ALERT_BODY_ES     = os.environ.get("INPUT_ALERT_BODY_ES", "").strip()
MAIL_ENABLED      = os.environ.get("INPUT_MAIL_ENABLED", "true").lower().strip() == "true"
# ─────────────────────────────────────────────────────────────────────────────

BASE_DIR   = Path(__file__).parent
PALETTE    = ["#1b3a6b", "#b5182a", "#00267a", "#155f3e", "#3b1560"]
_logo_path = BASE_DIR / "docs" / "logo.png"
LOGO_B64   = base64.b64encode(_logo_path.read_bytes()).decode() if _logo_path.exists() else ""

NITTER_INSTANCES = [
    "https://nitter.net",
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
]

GROQ_MODEL = "llama-3.1-8b-instant"   # Modèle gratuit, rapide, 14 400 req/jour

SYSTEM_PROMPT = """Tu es un analyste OSINT senior spécialisé en veille stratégique.
Règles strictes :
1. Parmi toutes les informations fournies (web ET signaux X), sélectionne les PLUS NOTABLES du moment,
   sans distinguer la provenance — un tweet pertinent vaut autant qu'un article si le fait est vérifiable.
2. "Notable" = impact géopolitique, économique, social ou sectoriel significatif, ou rupture par rapport
   au statu quo. Écarte les anecdotes, météo, culture, sport sauf exception majeure.
3. Chaque point : 2 phrases max, factuel, zéro opinion ni spéculation.
4. Chaque point se termine par → [Source: NomMédia ou @compte, JJ/MM/AAAA]
5. Si plusieurs sources confirment un fait, cite la plus fiable (média > compte X).
6. DATES : utilise UNIQUEMENT les dates présentes dans les sources fournies. N'utilise jamais une date
   issue de ta mémoire ou de ton entraînement. Si une source ne mentionne pas de date, ne cite pas de date.
7. Si l'information est absente du contexte : "Non disponible dans les sources à cette heure."
8. Format : liste numérotée SANS texte d'introduction ni conclusion. Commence directement par "1.".
9. N'invente jamais de source ni de fait."""


# ── CONFIG ────────────────────────────────────────────────────────────────────

def load_config():
    with open(BASE_DIR / "sources.json",    encoding="utf-8") as f: sources     = json.load(f)
    with open(BASE_DIR / "sections.json",   encoding="utf-8") as f: sections    = json.load(f)
    with open(BASE_DIR / "x_accounts.json", encoding="utf-8") as f: x_accounts  = json.load(f)
    return sources, sections, x_accounts


def get_recipients():
    if _recipients_input:
        return [r.strip() for r in _recipients_input.split(",") if r.strip()]
    path = BASE_DIR / "docs" / "recipients.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        r = data.get("digest", [])
        if not r: raise ValueError("Aucun destinataire")
        return r
    except FileNotFoundError:
        raise SystemExit("❌ docs/recipients.json introuvable.")


def active_sources(sources, key):
    return [s for s in sources.get(key, []) if s.get("active", True)]

def active_sources_str(sources, key):
    return ", ".join(s["name"] for s in active_sources(sources, key)) or "Reuters, AP"

def active_handles(x_accounts, key):
    return [a["handle"] for a in x_accounts.get(key, []) if a.get("active", True)]


# ── TAVILY : recherche web ────────────────────────────────────────────────────

def tavily_search(query: str, domains: list[str] = None) -> str:
    """
    Appelle l'API Tavily et retourne un contexte textuel structuré.
    1 crédit par appel (basic search).
    """
    payload = {
        "query":               query,
        "search_depth":        "basic",
        "max_results":         8,        # plus de résultats = meilleure sélection
        "include_answer":      False,
        "include_raw_content": False,
        "days":                2,        # uniquement les 48 dernières heures
    }
    if domains:
        payload["include_domains"] = domains[:15]

    data = json.dumps(payload).encode("utf-8")
    req  = urllib.request.Request(
        "https://api.tavily.com/search",
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {TAVILY_API_KEY}",
            "Content-Type":  "application/json"
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            resp    = json.loads(r.read())
            results = resp.get("results", [])
            if not results:
                return "Aucun résultat disponible."
            lines = []
            for item in results:
                title   = item.get("title", "")
                url     = item.get("url", "")
                snippet = item.get("content", "")[:500]   # plus de contexte par article
                source  = url.split("/")[2] if url else "source inconnue"
                pub     = item.get("published_date", "")
                date_tag = f" [{pub[:10]}]" if pub else ""
                lines.append(f"[{source}{date_tag}] {title} — {snippet}")
            return "\n\n".join(lines)
    except Exception as e:
        return f"Erreur Tavily : {e}"


def build_tavily_query(section: dict, sources: dict, lang: str) -> tuple[str, list[str]]:
    """
    Construit la requête Tavily : large et ouverte pour capturer les faits les plus notables,
    sans sur-spécifier — Groq fera la sélection éditoriale.
    """
    src_list = active_sources(sources, section["sources_key"])
    domains  = [s["url"] for s in src_list]

    # Requêtes larges et récentes — on veut le bruit complet, Groq trie
    queries = {
        "international": "breaking international news today geopolitics conflict diplomacy",
        "espagne":       "noticias más importantes España hoy política economía sociedad",
        "france":        "actualités majeures France aujourd'hui politique économie société",
        "finances":      "financial markets major news today stocks bonds commodities forex central banks",
        "rail":          "railway rail transport major news today operators tenders new lines incidents",
    }
    query = queries.get(section["id"], f"major news {section.get('title_fr', section['id'])} today")
    return query, domains


# ── GROQ : LLM gratuit ────────────────────────────────────────────────────────

def groq_summarize(context: str, section: dict, lang: str, x_signals: str = "", _retry: int = 0) -> str:
    """
    Envoie le contexte Tavily + signaux X à Groq pour générer le résumé.
    Modèle gratuit llama-3.1-8b-instant : 14 400 req/jour.
    """
    n      = section["items"]
    title  = section[f"title_{lang}"]
    prompt_key = f"prompt_{lang}"

    # Prompt unifié : web + X sur un pied d'égalité, sélection éditoriale par Groq
    x_block = f"""\n--- SIGNAUX X/TWITTER ---\n{x_signals}""" if x_signals else ""

    today = datetime.now().strftime("%d/%m/%Y")
    user_msg = f"""Nous sommes le {today}. Voici les informations disponibles sur le thème "{title}" publiées dans les dernières 48h.
Elles proviennent de sources web ET de comptes X — traite-les sans distinction de provenance.

--- SOURCES WEB ---
{context}{x_block}

---
MISSION : Parmi toutes ces informations, sélectionne les {n} faits les plus notables et significatifs
publiés AUJOURD'HUI ou HIER ({today}). Privilégie les événements à fort impact, les ruptures, les décisions majeures.
Ignore les doublons (même fait, plusieurs sources = cite la plus fiable).
Commence directement par "1." sans introduction.
{section[prompt_key].replace('{items}', str(n)).replace('{sources}', 'les sources et signaux ci-dessus')}
"""

    payload = json.dumps({
        "model":       GROQ_MODEL,
        "max_tokens":  600,
        "temperature": 0.1,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_msg}
        ]
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/chat/completions",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type":  "application/json",
            "User-Agent":    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0",
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())["choices"][0]["message"]["content"].strip()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        if e.code == 429:
            print("  ⏳ Rate limit Groq — pause 60s...")
            time.sleep(60)
            return groq_summarize(context, section, lang, x_signals, _retry)
        if e.code == 403 and _retry < 3:
            detail = "1010 Cloudflare block" if "1010" in body else "accès refusé"
            wait = 30 * (2 ** _retry)
            print(f"  ⚠️  Groq 403 ({detail}) — retry {_retry+1}/3 dans {wait}s...")
            time.sleep(wait)
            return groq_summarize(context, section, lang, x_signals, _retry + 1)
        return f"⚠️ Erreur Groq {e.code} : {body[:200]}"
    except Exception as e:
        return f"⚠️ Erreur Groq : {e}"


# ── NITTER : signaux X ────────────────────────────────────────────────────────

class TweetParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tweets = []; self._in = False; self._buf = []

    def handle_starttag(self, tag, attrs):
        if tag == "div" and "tweet-content" in dict(attrs).get("class", ""):
            self._in = True; self._buf = []

    def handle_endtag(self, tag):
        if tag == "div" and self._in:
            t = " ".join(self._buf).strip()
            if t and len(t) > 20: self.tweets.append(t)
            self._in = False

    def handle_data(self, data):
        if self._in: self._buf.append(data.strip())


def fetch_nitter(handle, max_t=2):
    for base in NITTER_INSTANCES:
        try:
            req = urllib.request.Request(
                f"{base}/{handle}",
                headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"}
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                p = TweetParser()
                p.feed(r.read().decode("utf-8", "replace"))
                if p.tweets: return p.tweets[:max_t]
        except Exception:
            continue
    return []


def fetch_x_signals(x_accounts, section_id):
    """
    Collecte les tweets récents. On prend plus de comptes et plus de tweets
    pour donner à Groq plus de matière à sélectionner.
    """
    handles = active_handles(x_accounts, section_id)
    if not handles: return ""
    signals = []
    for h in handles[:6]:           # jusqu'à 6 comptes par section
        for t in fetch_nitter(h, 3):  # jusqu'à 3 tweets par compte
            clean = re.sub(r"\s+", " ", re.sub(r"http\S+", "", t)).strip()
            if len(clean) > 40:
                signals.append(f"@{h}: {clean[:250]}")
        time.sleep(0.3)
    return "\n".join(signals[:12])  # jusqu'à 12 signaux au total


# ── FETCH DIGEST ──────────────────────────────────────────────────────────────

def fetch_digest_content(sections_cfg, sources, x_accounts):
    results = {}
    active  = [s for s in sections_cfg["sections"] if s.get("active", True)]

    for sec in active:
        sid = sec["id"]

        print(f"  🔍 [{sid}] Signaux X...")
        xs = fetch_x_signals(x_accounts, sid)

        print(f"  🌐 [{sid}] Tavily search...")
        query, domains = build_tavily_query(sec, sources, "fr")
        context = tavily_search(query, domains)
        time.sleep(1)  # politesse API

        print(f"  🤖 [{sid}] Groq FR...")
        fr = groq_summarize(context, sec, "fr", xs)
        time.sleep(0.5)

        print(f"  🤖 [{sid}] Groq ES...")
        es = groq_summarize(context, sec, "es", xs)
        time.sleep(0.5)

        results[sid] = {
            "section":   sec,
            "fr":        fr,
            "es":        es,
            "x_handles": active_handles(x_accounts, sid)
        }

    return results


# ── EMAIL HTML : DIGEST ───────────────────────────────────────────────────────

def render_items(content):
    out = []
    for line in content.split("\n"):
        line = line.strip()
        if not line: continue
        # Supprimer numérotation Groq ("1. ", "2. " etc.) — le <ol> gère l'affichage
        line = re.sub(r'^\d+[\.\)]\s*', '', line)
        # Ignorer les lignes d'intro/conclusion sans citation de source
        if not line: continue
        if "[Source:" in line:
            idx    = line.index("[Source:")
            body   = line[:idx].rstrip(" →")
            source = line[idx:]
            out.append(
                f'<li style="margin-bottom:10px;font-size:13px;line-height:1.65;color:#1a1a2e">'
                f'{body}'
                f'<span style="display:block;margin-top:2px;font-size:10.5px;color:#888;font-style:italic">{source}</span>'
                f'</li>'
            )
        else:
            # Exclure les phrases d'intro sans source (ex : "Voici les N informations…")
            if len(line) < 30 or line.endswith(":") or line.lower().startswith(("voici", "voilà", "aquí", "estas son")):
                continue
            out.append(
                f'<li style="margin-bottom:10px;font-size:13px;line-height:1.65;color:#1a1a2e">{line}</li>'
            )
    return "\n".join(out)


def render_x_badges(handles):
    if not handles: return ""
    pills = "".join(
        f'<span style="background:#e8f1ff;color:#1b3a6b;font-size:10px;padding:2px 7px;border-radius:3px;margin:2px;display:inline-block">@{h}</span>'
        for h in handles[:5]
    )
    return (f'<div style="margin-top:8px;padding-top:6px;border-top:1px dashed #dce4f0">'
            f'<span style="font-size:10px;color:#aaa">📡 </span>{pills}</div>')


def render_section_block(section, content, color, title_key, handles):
    return f"""
<div style="margin-bottom:16px;border-radius:7px;overflow:hidden;box-shadow:0 1px 5px rgba(0,0,0,.07)">
  <div style="background:{color};padding:10px 16px;display:flex;align-items:center;gap:8px">
    <span style="font-size:16px">{section['emoji']}</span>
    <span style="color:#fff;font-weight:700;font-size:13.5px">{section[title_key]}</span>
  </div>
  <div style="background:#fff;padding:10px 16px 8px">
    <ol style="margin:0;padding-left:16px">{render_items(content)}</ol>
    {render_x_badges(handles)}
  </div>
</div>"""


def render_lang_block(results, lang, header, date_str, bg):
    sections_html = "".join(
        render_section_block(d["section"], d[lang], PALETTE[i % len(PALETTE)], f"title_{lang}", d["x_handles"])
        for i, (_, d) in enumerate(results.items())
    )
    return f"""
<div style="margin-bottom:28px">
  <div style="background:{bg};padding:16px 22px;border-radius:9px 9px 0 0">
    <div style="color:rgba(255,255,255,.4);font-size:9px;letter-spacing:3px;text-transform:uppercase">OSINT DIGEST</div>
    <h2 style="margin:3px 0 0;color:#fff;font-size:17px;font-weight:900">{header}</h2>
    <p style="margin:3px 0 0;color:rgba(255,255,255,.5);font-size:11px">{date_str}</p>
  </div>
  <div style="background:#f5f7fb;padding:14px 14px 4px;border-radius:0 0 9px 9px">{sections_html}</div>
</div>"""


def build_digest_html(results, edition, now):
    date_fr = now.strftime("%A %d %B %Y · %H:%M")
    date_es = now.strftime("%A %d de %B de %Y · %H:%M")
    fr = render_lang_block(results, "fr", f"Édition française — {edition}", date_fr, "#0f1f3d")
    es = render_lang_block(results, "es", f"Edición española — {edition}", date_es, "#1f0d0d")
    return f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#dde3ee;font-family:Georgia,serif">
<div style="max-width:620px;margin:22px auto;padding:0 10px">
  <div style="background:linear-gradient(135deg,#080e1f,#0f2040,#050d1a);padding:24px;border-radius:11px;text-align:center;margin-bottom:18px">
    <img src="data:image/png;base64,{LOGO_B64}" style="width:52px;height:52px;border-radius:10px;display:block;margin:0 auto 8px;" alt="OSINT Digest">
    <h1 style="margin:5px 0 0;color:#fff;font-size:19px;font-weight:900;letter-spacing:3px;text-transform:uppercase">OSINT Daily Digest</h1>
    <p style="margin:5px 0 0;color:rgba(255,255,255,.35);font-size:10px;letter-spacing:2px">{edition.upper()} · {now.strftime('%d/%m/%Y')} · ~5 MIN</p>
  </div>
  {fr}
  <div style="text-align:center;margin:4px 0 22px;color:#c0c8d8;font-size:14px;letter-spacing:6px">· · ·</div>
  {es}
  <div style="text-align:center;padding:14px;color:#aab;font-size:10px;line-height:1.9">
    Sources vérifiées via Tavily · Résumé par Groq (LLaMA 3.1) · Signaux X via Nitter<br>
    {now.strftime('%d/%m/%Y %H:%M')} · 100% gratuit · Dashboard GitHub Pages
  </div>
</div></body></html>"""


# ── EMAIL HTML : ALERTE ───────────────────────────────────────────────────────

def build_alert_html(subject, body_fr, body_es, now):
    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#0a0a0f;font-family:Georgia,serif">
<div style="max-width:580px;margin:30px auto;padding:0 12px">
  <div style="background:linear-gradient(135deg,#1a0a0a,#2d0f0f);border:1px solid #ff3d57;border-radius:10px;padding:24px 28px">
    <div style="text-align:center;margin-bottom:12px"><img src="data:image/png;base64,{LOGO_B64}" style="width:52px;height:52px;border-radius:10px;display:block;margin:0 auto 8px;" alt="OSINT Digest"><span style="font-size:22px">⚡</span></div>
    <h1 style="color:#ff3d57;font-size:20px;font-weight:900;text-align:center;letter-spacing:2px;text-transform:uppercase;margin-bottom:20px">{subject}</h1>
    <div style="background:rgba(255,255,255,.04);border-radius:7px;padding:16px;margin-bottom:14px">
      <div style="font-size:10px;color:#ff8fa3;letter-spacing:2px;text-transform:uppercase;margin-bottom:8px">🇫🇷 Français</div>
      <p style="color:#e2e8f0;font-size:13.5px;line-height:1.7;margin:0">{body_fr or '—'}</p>
    </div>
    <div style="background:rgba(255,255,255,.04);border-radius:7px;padding:16px">
      <div style="font-size:10px;color:#ff8fa3;letter-spacing:2px;text-transform:uppercase;margin-bottom:8px">🇪🇸 Español</div>
      <p style="color:#e2e8f0;font-size:13.5px;line-height:1.7;margin:0">{body_es or '—'}</p>
    </div>
    <p style="text-align:center;color:rgba(255,255,255,.25);font-size:10px;margin-top:18px">
      {now.strftime('%d/%m/%Y %H:%M')} · OSINT Digest Alert
    </p>
  </div>
</div></body></html>"""


# ── ENVOI ─────────────────────────────────────────────────────────────────────

def send(html, subject, recipients):
    if not MAIL_ENABLED:
        print("📵 Envoi email désactivé (INPUT_MAIL_ENABLED=false).")
        return
    print(f"\n📨 Envoi à {len(recipients)} destinataire(s)...")
    for to in recipients:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = GMAIL_USER
        msg["To"]      = to
        msg.attach(MIMEText(html, "html", "utf-8"))
        try:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
                s.login(GMAIL_USER, GMAIL_APP_PASSWORD)
                s.sendmail(GMAIL_USER, [to], msg.as_string())
            print(f"  ✅ → {to}")
        except Exception as e:
            print(f"  ❌ {to} : {e}")


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    now        = datetime.now()
    recipients = get_recipients()

    print(f"\n🛰️  OSINT Digest — {now.strftime('%d/%m/%Y %H:%M')}")
    print(f"📧 Mode : {NOTIF_TYPE} | Destinataires : {recipients}")
    print("─" * 55)

    if NOTIF_TYPE == "alert":
        print("⚡ Alerte rapide...")
        html    = build_alert_html(ALERT_SUBJECT, ALERT_BODY_FR, ALERT_BODY_ES, now)
        subject = ALERT_SUBJECT

    else:
        matin = now.hour < 14
        ed    = ("🌅 Morning news" if matin else "🌙 Night news")
        sources, sections_cfg, x_accounts = load_config()
        active = [s["id"] for s in sections_cfg["sections"] if s.get("active", True)]
        print(f"📋 Sections : {', '.join(active)}")
        print("\n📡 Récupération...")
        results = fetch_digest_content(sections_cfg, sources, x_accounts)
        html    = build_digest_html(results, ed, now)
        subject = f"🛰️ OSINT Digest — {ed} — {now.strftime('%d/%m/%Y')}"

    send(html, subject, recipients)
    print("\n✅ Terminé.")


if __name__ == "__main__":
    main()
