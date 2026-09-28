import base64, hashlib, html, json, os, re, time
import requests
from xml.etree import ElementTree as ET

TG_TOKEN = os.environ.get("TG_TOKEN", "")
TG_CHAT  = os.environ.get("TG_CHAT", "")
STATE_FILE, MIN_USD_YEAR, CAP = "seen.json", 9600, 25   # $9600/yr ≈ $800/mo

# --- WHAT TO WATCH: company ATS boards (jobs appear here FIRST) -------------
# Add a company: open its careers page, copy the slug from the URL:
#   job-boards.greenhouse.io/CANONICAL -> greenhouse:"canonical"
#   jobs.ashbyhq.com/DOIST -> ashby:"doist" | jobs.lever.co/SLUG
#   apply.workable.com/SLUG | jobs.smartrecruiters.com/ORG | ORG.recruitee.com
BOARDS = {
    "greenhouse": ["canonical", "gitlab", "duckduckgo", "wikimedia", "krakenfx"],
    "ashby": ["doist"], "lever": [], "workable": [],
    "smartrecruiters": [], "recruitee": [],
}
WWR = ["https://weworkremotely.com/categories/remote-customer-support-jobs.rss",
       "https://weworkremotely.com/categories/remote-all-others-jobs.rss"]

# --- FILTERS (tuned to your resume) -----------------------------------------
TITLE_HIT = ["customer support","customer success","customer experience","support specialist",
 "support agent","support associate","technical support","happiness","help desk","helpdesk",
 "operations"," ops","ops coordinator","ops specialist","business operations","tutor","tutoring",
 "teacher","teaching","instructor","subject matter","sme","curriculum","executive assistant",
 "virtual assistant","administrative","admin assistant","community","moderation","moderator",
 "trust & safety","trust and safety","data entry","annotation","annotator","ai trainer","ai tutor",
 "transcription","transcriber","quality analyst","onboarding","coordinator","back office"]
TITLE_MISS = ["senior","sr.","sr ","staff","principal","lead","manager","director","head of","vp ",
 "chief","nurse","clinical","sdr","bdr","account executive","recruiter","talent acquisition"]
BAD = ["us only","usa only","u.s. only","united states only","must be located in the united states",
 "based in the us","us-based only","authorized to work in the united states","right to work in the us",
 "canada only","uk only","united kingdom only","europe only","emea only","eu only","must reside",
 "reside in the us","citizens of the us","within 4 hours","±4","us time zone only"]
STRONG = ["worldwide","anywhere","global","all countries","india","apac","asia"]

def J(src, company, title, url, loc, date, desc, sal=""):
    return {"id": hashlib.sha1((url or company+title).encode()).hexdigest()[:16], "src": src,
            "company": company, "title": title, "url": url, "loc": loc or "",
            "desc": re.sub(r"<[^>]+>", " ", desc or ""), "sal": sal}

def greenhouse(s):
    d = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{s}/jobs?content=true", timeout=30).json()
    for j in d.get("jobs", []):
        try: desc = base64.b64decode(j.get("content") or "").decode("utf-8", "ignore")
        except: desc = ""
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
    for j in requests.get(f"https://apply.workable.com/api/v1/widget/accounts/{s}", timeout=30).json().get("jobs", []):
        l = j.get("location") or {}
        yield J("workable", s, j.get("title",""), j.get("shortlink",""),
                ", ".join(str(v) for v in l.values() if v), j.get("published_on",""), j.get("description",""))

def smartrecruiters(o):
    for p in requests.get(f"https://api.smartrecruiters.com/v1/companies/{o}/postings?limit=100", timeout=30).json().get("content", []):
        l = p.get("location") or {}
        yield J("smartrecruiters", o, p.get("name",""), f"https://jobs.smartrecruiters.com/{o}/{p.get('id')}",
                f"{l.get('city','')}, {l.get('country','')}".strip(", "), p.get("releasedDate",""), "")

def remotive():
    for j in requests.get("https://remotive.com/api/remote-jobs?limit=100", timeout=30).json().get("jobs", []):
        yield J("remotive", j.get("company_name",""), j.get("title",""), j.get("url",""),
                j.get("candidate_required_location",""), j.get("publication_date",""),
                j.get("description",""), j.get("salary") or "")

def remoteok():
    d = requests.get("https://remoteok.com/api", headers={"User-Agent":"Mozilla/5.0"}, timeout=30).json()
    for j in d[1:]:
        sal = f'${j.get("salary_min")}-{j.get("salary_max")}/yr' if j.get("salary_min") else ""
        yield J("remoteok", j.get("company",""), j.get("position",""), j.get("url") or j.get("apply_url",""),
                j.get("location",""), j.get("date",""), j.get("description",""), sal)

def jobicy():
    for j in requests.get("https://jobicy.com/api/v2/remote-jobs?count=50", timeout=30).json().get("jobs", []):
        yield J("jobicy", j.get("companyName",""), j.get("jobTitle",""), j.get("url",""),
                j.get("jobGeo",""), j.get("pubDate",""), j.get("jobExcerpt") or j.get("jobDescription") or "")

def wwr():
    for f in WWR:
        try:
            root = ET.fromstring(requests.get(f, headers={"User-Agent":"Mozilla/5.0"}, timeout=30).content)
            for it in root.iter("item"):
                t = (it.findtext("title") or "").strip()
                yield J("wwr", t.split(":")[0] if ":" in t else "WWR", t.split(":",1)[-1].strip(),
                        (it.findtext("link") or "").strip(), "", it.findtext("pubDate") or "",
                        re.sub("<[^>]+>"," ", it.findtext("description") or ""))
        except Exception as e: print("wwr:", e)

def all_jobs():
    for s in BOARDS["greenhouse"]:
        try: yield from greenhouse(s)
        except Exception as e: print(s, e)
    for s in BOARDS["ashby"]:
        try: yield from ashby(s)
        except Exception as e: print(s, e)
    for s in BOARDS["lever"]:
        try: yield from lever(s)
        except Exception as e: print(s, e)
    for s in BOARDS["workable"]:
        try: yield from workable(s)
        except Exception as e: print(s, e)
    for o in BOARDS["smartrecruiters"]:
        try: yield from smartrecruiters(o)
        except Exception as e: print(o, e)
    for fn in (remotive, remoteok, jobicy, wwr):
        try: yield from fn()
        except Exception as e: print(fn.__name__, e)

def eligible(j):
    t = (j["title"] + " " + j["loc"]).lower()
    if not any(k in t for k in TITLE_HIT) or any(k in t for k in TITLE_MISS): return False
    blob = (j["loc"] + " " + j["desc"][:4000]).lower()
    if any(b in blob for b in BAD) and not any(s in blob for s in STRONG): return False
    return True

def salary_note(text):
    lows = []
    for m in re.finditer(r"(?:\$|usd\s?)([\d][\d,]*(?:\.\d+)?)\s*(k)?\s*(?:/|per\s+)?\s*(hr|hour|mo|month|yr|year|annum|annual)?", text.lower()):
        v = float(m.group(1).replace(",", "")) * (1000 if m.group(2) else 1)
        v *= {"hr":2080,"hour":2080,"mo":12,"month":12}.get(m.group(3) or "", 1)
        if (m.group(3) or v >= 5000) and 500 < v < 2_000_000: lows.append(v)
    for m in re.finditer(r"(?:₹|\binr\b|\brs\.?)\s?([\d][\d,]*(?:\.\d+)?)\s*(lpa|lakh|k|/mo|per month|/month|/yr|per annum|/year)?", text.lower()):
        v, u = float(m.group(1).replace(",", "")), m.group(2)
        v *= {"lpa":100000,"lakh":100000,"k":1000}.get(u, 1)
        if u and ("month" in u or "mo" in u): v *= 12
        if 50_000 < v < 50_000_000: lows.append(v / 83.5)
    if not lows: return "— pay not listed", False
    low = min(lows)
    if low >= MIN_USD_YEAR: return f"💰 from ~${round(low/12):,}/mo", False
    if low < 4800: return f"💰 below target (~${round(low/12)}/mo)", True
    return f"💰 range starts ~${round(low/12)}/mo", False

def esc(s): return html.escape(s or "")

def main():
    try: state = json.load(open(STATE_FILE))
    except: state = {}
    state = {k: v for k, v in state.items() if v > time.time() - 30*86400}
    sent = 0
    for j in all_jobs():
        if j["id"] in state: continue
        state[j["id"]] = time.time()
        if not eligible(j): continue
        note, drop = salary_note(j["desc"] + " " + j["sal"])
        if drop: continue
        blob = (j["desc"] + j["sal"]).lower()
        hot = "🔥 " if any(p in blob for p in ["deel","remote.com","oyster","multiplier","payoneer","wise.com"]) else ""
        msg = (f'{hot}<b>{esc(j["title"])}</b>\n{esc(j["company"])} · {esc(j["loc"])}\n'
               f'{note}\n{esc(j["url"])}\n<i>via {j["src"]}</i>')
        if TG_TOKEN:
            try:
                requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                              json={"chat_id": TG_CHAT, "text": msg[:4096], "parse_mode": "HTML"},
                              timeout=30).raise_for_status()
                time.sleep(1.1)
            except Exception as e: print("tg:", e)
        else: print(msg)
        sent += 1
        if sent >= CAP: print("cap reached"); break
    json.dump(state, open(STATE_FILE, "w"))

if __name__ == "__main__":
    main()