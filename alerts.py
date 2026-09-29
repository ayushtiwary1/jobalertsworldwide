import base64, hashlib, html, json, os, re, time
import requests
from xml.etree import ElementTree as ET

TG_TOKEN = os.environ.get("TG_TOKEN", "")
TG_CHAT  = os.environ.get("TG_CHAT", "")
STATE_FILE, CAP = "seen.json", 25
MIN_USD_YEAR   = 3600   # ignore jobs listing less than ~$300/mo
REQUIRE_DIRECT = True   # aggregator jobs with NO direct career link are dropped
RESOLVE        = True   # open wwr/remotive/jobicy pages to extract the real apply link

BOARDS = {
    "greenhouse": ["canonical", "gitlab", "duckduckgo", "wikimedia", "krakenfx"],
    "ashby": ["doist"], "lever": [], "workable": [],
    "smartrecruiters": [], "recruitee": [],
}
WWR = ["https://weworkremotely.com/categories/remote-customer-support-jobs.rss",
       "https://weworkremotely.com/categories/remote-all-others-jobs.rss"]
AGG = {"wwr", "remotive", "remoteok", "jobicy"}

TITLE_HIT = ["customer support","customer success","customer experience","support specialist",
 "support agent","support associate","technical support","happiness","help desk","helpdesk",
 "operations"," ops","ops coordinator","ops specialist","business operations","tutor","tutoring",
 "teacher","teaching","instructor","subject matter","sme","curriculum","executive assistant",
 "virtual assistant","administrative","admin assistant","community","moderation","moderator",
 "trust & safety","trust and safety","data entry","annotation","annotator","ai trainer","ai tutor",
 "transcription","transcriber","quality analyst","onboarding","coordinator","back office",
 "night shift","us shift","est overlap","evening shift","graveyard","night support"]
TITLE_MISS = ["senior","sr.","sr ","staff","principal","lead","manager","director","head of","vp ",
 "chief","nurse","clinical","sdr","bdr","account executive","recruiter","talent acquisition"]

HARD_BLOCK = [" us only"," usa only","us citizens","us residents","us permanent","united states only",
 "us-based only","us based only","based in the us only","remote in us","remote in usa",
 "remote in the us","remote within the united states","must be located in the us",
 "must be located in the united states","must reside in the us","must reside in the united states",
 "must be a us resident","authorized to work in the us","authorized to work in the united states",
 "legally authorized to work in the united states","eligible to work in the us",
 "eligible to work in the united states","right to work in the us","us work authorization",
 "canada only","canadian citizens","uk only","united kingdom only","europe only"," eu only",
 "uk-based only","europe-based only","emea only","latam only","north america only",
 "must be based in europe","must be based in the uk","must be based in canada",
 "anywhere in the us","anywhere in the united states","anywhere in north america"]

LANG_BLOCK = ["german","french","spanish","portuguese","italian","dutch","polish","japanese",
 "mandarin","chinese","korean","vietnamese","thai","arabic","hebrew","russian","turkish","greek",
 "danish","swedish","norwegian","finnish","czech","hungarian","bulgarian","ukrainian",
 "indonesian","bahasa","tagalog","filipino"]
GOOD_LOC_RE = re.compile(r"\b(worldwide|anywhere|global|all countries|india|bharat|apac|asia)\b", re.I)

WORLDWIDE = ["worldwide", "anywhere", "all countries", "global", "work from anywhere",
             "location-independent", "no location restrictions"]

# any country/region here (India is NOT in the list) in the location/title = reject
LOC_COUNTRY_RE = re.compile(
    r"\b(usa|us|u\.s\.|united states|america|americas|canada|mexico|brazil|argentina|colombia|chile|"
    r"latam|uk|u\.k\.|united kingdom|britain|england|scotland|ireland|europe|emea|ceur|eeur|weur|"
    r"germany|france|spain|portugal|italy|poland|netherlands|holland|romania|ukraine|czech|hungary|"
    r"bulgaria|greece|austria|switzerland|sweden|norway|denmark|finland|belgium|philippines|"
    r"australia|zealand|nigeria|kenya|egypt|pakistan|bangladesh|sri lanka|nepal|china|japan|korea|"
    r"singapore|malaysia|indonesia|vietnam|thailand|uae|dubai|saudi|qatar|israel|turkey|"
    r"north america|south america|central america|middle east|mena)\b", re.I)

# countries used inside description-scan (bare "us" excluded — it collides with the pronoun)
DESC_COUNTRY = (r"(?:usa|u\.s\.|united states|canada|united kingdom|europe|emea|germany|france|spain|"
    r"italy|poland|netherlands|romania|philippines|australia|brazil|mexico|argentina|colombia|"
    r"singapore|japan|china|uae|dubai|israel|nigeria|kenya|egypt|pakistan|bangladesh|sri lanka|"
    r"north america|latam|middle east|ireland|new zealand|vietnam|indonesia|malaysia|thailand)")

# catches "must be based in X", "X residents only", "eligible to work in X", etc.
RESIDE_RE = re.compile(
    r"(?:must|should|will need to|need to|have to|required to)\s+(?:be\s+)?(?:currently\s+)?"
    r"(?:located|based|resid\w+|living|situated)[^.;\n]{0,80}" + DESC_COUNTRY + r"\b"
    r"|\b" + DESC_COUNTRY + r"\b[^.;\n]{0,40}\b(?:residents?|citizens?|nationals?)\b"
    r"|(?:eligible|authorized|authorised|entitled|right)\s+to\s+work[^.;\n]{0,80}" + DESC_COUNTRY + r"\b"
    r"|candidates?[^.;\n]{0,60}(?:based|located|living|resid\w+)[^.;\n]{0,30}(?:in|within|from)\s+"
    + DESC_COUNTRY + r"\b"
    r"|(?:open|available|hiring|recruiting|accepting applications)\s+(?:only\s+)?"
    r"(?:to|in|for|from|within)[^.;\n]{0,40}" + DESC_COUNTRY + r"\b"
    r"|(?:willing|required|need(ed)?|must)\s+to\s+relocat\w+[^.;\n]{0,40}" + DESC_COUNTRY + r"\b", re.I)

TRUST_VAGUE_REMOTE = True  # bare "Remote" location on ATS jobs = accepted unless description restricts

# "US hours / EST shift" in a title is GOOD for you (night coverage) — stripped before the country check
TZ_STRIP = re.compile(r"\b(?:us|usa|est|cst|mst|pst|et|pt)\b\s*(?:hours?|timezone|time\s+zone|shift|"
                      r"evenings?|nights?|mornings?|daytime|coverage|business)", re.I)

ATS_RE = re.compile(r"https?://[^\s\"'<>()]*(?:greenhouse\.io|lever\.co|ashbyhq\.com|workable\.com|"
 "smartrecruiters\.com|recruitee\.com|bamboohr\.com|myworkdayjobs\.com|join\.com|teamtailor\.com|"
 "breezy\.hr|pinpoint\.hq|personio\.de|personio\.com)/[^\s\"'<>()]*", re.I)
MAIL_RE = re.compile(r"mailto:[^\s\"'<>]+")
BAD_MAIL = ("weworkremotely", "remotive", "jobicy", "remoteok", "noreply", "no-reply")

def esc(s): return html.escape(s or "")

def J(src, company, title, url, loc, date, desc_html, sal="", apply=""):
    hrefs = " ".join(re.findall(r'href=["\']([^"\']+)', desc_html or ""))
    desc = re.sub(r"<[^>]+>", " ", desc_html or "")
    return {"id": hashlib.sha1((url or (company + title)).encode()).hexdigest()[:16], "src": src,
            "company": company or "?", "title": (title or "?").strip(), "url": url or "",
            "loc": (loc or "").strip(), "desc": desc, "sal": sal or "", "apply": apply or "", "hrefs": hrefs}

# ---------- sources (ATS = direct career pages) ----------
def greenhouse(s):
    d = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{s}/jobs?content=true", timeout=30).json()
    for j in d.get("jobs", []):
        try: desc = base64.b64decode(j.get("content") or "").decode("utf-8", "ignore")
        except Exception: desc = ""
        yield J("greenhouse", s, j.get("title",""), j.get("absolute_url",""),
                (j.get("location") or {}).get("name",""), j.get("updated_at",""), desc)

def ashby(s):
    d = requests.get(f"https://api.ashbyhq.com/posting-api/job-board/{s}?includeCompensation=true", timeout=30).json()
    for j in d.get("jobs", []):
        loc = j.get("location"); loc = loc if isinstance(loc, str) else (loc or {}).get("name","")
        c = j.get("compensation") or {}
        yield J("ashby", s, j.get("title",""), j.get("jobUrl",""), loc, j.get("publishedAt",""),
                j.get("descriptionPlain") or "", str(c.get("summary") or c.get("compensationTierSummary") or ""))

def lever(s):
    for j in requests.get(f"https://api.lever.co/v0/postings/{s}?mode=json", timeout=30).json():
        yield J("lever", s, j.get("text",""), j.get("hostedUrl",""),
                (j.get("categories") or {}).get("location",""), "", j.get("descriptionPlain") or "")

def workable(s):
    d = requests.get(f"https://apply.workable.com/api/v1/widget/accounts/{s}", timeout=30).json()
    for j in d.get("jobs", []):
        l = j.get("location") or {}
        yield J("workable", s, j.get("title",""), j.get("shortlink",""),
                ", ".join(str(v) for v in l.values() if v), j.get("published_on",""), j.get("description",""))

def smartrecruiters(o):
    d = requests.get(f"https://api.smartrecruiters.com/v1/companies/{o}/postings?limit=100", timeout=30).json()
    for p in d.get("content", []):
        l = p.get("location") or {}
        yield J("smartrecruiters", o, p.get("name",""),
                f"https://jobs.smartrecruiters.com/{o}/{p.get('id')}",
                f"{l.get('city','')}, {l.get('country','')}".strip(", "), p.get("releasedDate",""), "")

def recruitee(s):
    d = requests.get(f"https://{s}.recruitee.com/api/offers/", timeout=30).json()
    for j in d.get("offers", []):
        l = j.get("location") or {}
        loc = l if isinstance(l, str) else ", ".join(str(v) for v in [l.get("city"), l.get("country")] if v)
        yield J("recruitee", s, j.get("title",""), j.get("careers_url") or j.get("careers_apply_url") or "",
                loc, "", j.get("description") or "")

def remotive():
    for j in requests.get("https://remotive.com/api/remote-jobs?limit=100", timeout=30).json().get("jobs", []):
        yield J("remotive", j.get("company_name",""), j.get("title",""), j.get("url",""),
                j.get("candidate_required_location",""), j.get("publication_date",""),
                j.get("description",""), j.get("salary") or "")

def remoteok():
    d = requests.get("https://remoteok.com/api", headers={"User-Agent":"Mozilla/5.0"}, timeout=30).json()
    for j in d[1:]:
        sal = f'${j.get("salary_min")}-{j.get("salary_max")}/yr' if j.get("salary_min") else ""
        yield J("remoteok", j.get("company",""), j.get("position",""), j.get("url") or "",
                j.get("location",""), j.get("date",""), j.get("description") or "", sal,
                apply=j.get("apply_url") or "")

def jobicy():
    for j in requests.get("https://jobicy.com/api/v2/remote-jobs?count=50", timeout=30).json().get("jobs", []):
        yield J("jobicy", j.get("companyName",""), j.get("jobTitle",""), j.get("url",""),
                j.get("jobGeo",""), j.get("pubDate",""), j.get("jobDescription") or j.get("jobExcerpt") or "",
                apply=j.get("jobApplyEmail") or "")

def wwr():
    for f in WWR:
        try:
            root = ET.fromstring(requests.get(f, headers={"User-Agent":"Mozilla/5.0"}, timeout=30).content)
            for it in root.iter("item"):
                t = (it.findtext("title") or "").strip()
                yield J("wwr", t.split(":")[0] if ":" in t else "WWR", t.split(":",1)[-1].strip(),
                        (it.findtext("link") or "").strip(), "", it.findtext("pubDate") or "",
                        it.findtext("description") or "")
        except Exception as e: print("wwr:", e)

def all_jobs():
    for src, fn, keys in (("greenhouse", greenhouse, BOARDS["greenhouse"]),
                          ("ashby", ashby, BOARDS["ashby"]),
                          ("lever", lever, BOARDS["lever"]),
                          ("workable", workable, BOARDS["workable"]),
                          ("smartrecruiters", smartrecruiters, BOARDS["smartrecruiters"]),
                          ("recruitee", recruitee, BOARDS["recruitee"])):
        for s in keys:
            try: yield from fn(s)
            except Exception as e: print(src, s, e)
    for fn in (remotive, remoteok, jobicy, wwr):
        try: yield from fn()
        except Exception as e: print(fn.__name__, e)

# ---------- filters ----------
def title_ok(j):
    t = (j["title"] + " " + j["loc"]).lower()
    return any(k in t for k in TITLE_HIT) and not any(k in t for k in TITLE_MISS)

def region_ok(j):
    t, loc, head = j["title"].lower(), j["loc"].lower(), j["desc"][:6000].lower()
    blob = t + " || " + loc + " || " + head
    if any(b in blob for b in HARD_BLOCK): return False
    if any(l in t for l in LANG_BLOCK): return False
    # 1) explicit good signals in the location line win instantly
    if GOOD_LOC_RE.search(loc): return True
    # 2) any other country/region named in the location -> hard reject (NO description override)
    if LOC_COUNTRY_RE.search(re.sub(r"\bremote\b", " ", loc)): return False
    # 3) country/region named in the title (after removing "US hours" phrases) -> reject
    if LOC_COUNTRY_RE.search(TZ_STRIP.sub(" ", t)): return False
    # 4) vague location ("Remote"/empty): scan description for restriction language
    if RESIDE_RE.search(head): return False
    if j["src"] in AGG or not TRUST_VAGUE_REMOTE:
        return any(w in head for w in WORLDWIDE)
    return True

def _mail(m): return not any(b in m.group(0).lower() for b in BAD_MAIL)

def find_direct(j):
    pool = " ".join([j.get("apply",""), j["url"], j["sal"], j["hrefs"], j["desc"][:8000]])
    m = ATS_RE.search(pool)
    if m: return m.group(0).rstrip('.,;)"\''), "direct"
    m = MAIL_RE.search(pool)
    if m and _mail(m): return m.group(0), "email"
    return None, None

def resolve(url):
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        m = ATS_RE.search(r.text)
        if m: return m.group(0).rstrip('.,;)"\'')
        m = MAIL_RE.search(r.text)
        if m and _mail(m): return m.group(0)
    except Exception as e: print("resolve:", e)
    return None

def salary_note(text):
    lows = []
    for m in re.finditer(r"(?:\$|usd\s?)([\d][\d,]*(?:\.\d+)?)\s*(k)?\s*(?:/|per\s+)?\s*(hr|hour|mo|month|yr|year|annum|annual)?", text.lower()):
        v = float(m.group(1).replace(",", "")) * (1000 if m.group(2) else 1)
        v *= {"hr": 2080, "hour": 2080, "mo": 12, "month": 12}.get(m.group(3) or "", 1)
        if (m.group(3) or v >= 5000) and 500 < v < 2_000_000: lows.append(v)
    for m in re.finditer(r"(?:₹|\binr\b|\brs\.?)\s?([\d][\d,]*(?:\.\d+)?)\s*(lpa|lakh|k|/mo|per month|/month|/yr|per annum|/year)?", text.lower()):
        v, u = float(m.group(1).replace(",", "")), m.group(2)
        v *= {"lpa": 100000, "lakh": 100000, "k": 1000}.get(u, 1)
        if u and ("month" in u or "mo" in u): v *= 12
        if 50_000 < v < 50_000_000: lows.append(v / 88)
    if not lows: return "— pay not listed", False
    low = min(lows)
    if low < MIN_USD_YEAR: return f"💰 below floor (~${round(low/12)}/mo)", True
    return f"💰 from ~${round(low/12):,}/mo", False

def main():
    try:
        with open(STATE_FILE) as f: state = json.load(f)
    except Exception: state = {}
    state = {k: v for k, v in state.items() if v > time.time() - 30*86400}
    sent, sent_links = 0, set()
    for j in all_jobs():
        if j["id"] in state: continue
        state[j["id"]] = time.time()
        if not (title_ok(j) and region_ok(j)): continue
        link, kind = find_direct(j)
        if not link and RESOLVE and j["src"] in AGG and j["url"]:
            link, kind = resolve(j["url"]), "resolved"
        if not link:
            if REQUIRE_DIRECT and j["src"] in AGG: continue
            link, kind = j["url"], "listing"
        if link in sent_links: continue
        sent_links.add(link)
        note, drop = salary_note(j["desc"] + " " + j["sal"])
        if drop: continue
        blob = (j["desc"] + " " + j["sal"]).lower()
        hot = "🔥 " if any(p in blob for p in ["deel","remote.com","oyster","multiplier","payoneer","wise.com"]) else ""
        meta = esc(j["company"]) + (f' · {esc(j["loc"])}' if j["loc"] else "")
        label = {"direct": "direct career page ✅", "email": "apply by email ✅",
                 "resolved": "direct link (auto-extracted) ✅", "listing": "listing page only ⚠️"}.get(kind, kind)
        msg = (f'{hot}<b>{esc(j["title"])}</b>\n{meta}\n{note}\n🔗 {esc(link)}\n<i>{label} · {j["src"]}</i>')
        if TG_TOKEN:
            try:
                requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                              json={"chat_id": TG_CHAT, "text": msg[:4096], "parse_mode": "HTML",
                                    "disable_web_page_preview": True},
                              timeout=30).raise_for_status()
                time.sleep(1.1)
            except Exception as e: print("tg:", e)
        else: print(msg)
        sent += 1
        if sent >= CAP: print("cap reached"); break
    with open(STATE_FILE, "w") as f: json.dump(state, f)

if __name__ == "__main__":
    main()
