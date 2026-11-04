"""System prompt + few-shot examples for the intent parser. Bump PROMPT_VERSION on every change.

Few-shots deliberately differ from the golden eval queries (no test leakage). No IDs anywhere (CLAUDE.md §3.6).
"""

from __future__ import annotations

import json

PROMPT_VERSION = "11"

SYSTEM_PROMPT = """Extract HR search filters from the user's text (Uzbek Latin, Uzbek Cyrillic or Russian) into one
JSON object. Output JSON only. Include only what is explicitly stated; if nothing is, output {}. Never output IDs.

Keys:
strings: region, birthRegion, district, nation, language, languageCertificate, education, eduType, degree,
academicDegree, academicTitle, institution, institutionName, firstName, lastName, middleName, pinfl, term, sex,
typeOfService, dateOfReceipt (YYYY-MM-DD)
lists: specialists, institutionTypes, institutionGroups, positionCategories, typeOfPosition
integers: fromAge, toAge, fromYear, toYear, experienceYear, inn, institutionInn
true: isAwarded, isReserved, isAcademicDegree, isAcademicTitle, isGlobalEducation

Values:
- Places, languages, specialities: Uzbek Latin base form (Toshkentda -> Toshkent, ingliz tilini -> Ingliz tili).
  A profession becomes its field (iqtisodchi, экономист -> Iqtisodiyot).
- district: a tuman/район. region: a viloyat/область or a city.
- sex: Erkak or Ayol. education: Oliy, O'rta maxsus or O'rta.
- typeOfPosition: Leader (rahbar, boshliq, direktor, руководитель, начальник), Management (boshqaruv xodimi, menejer),
  Technic (texnik), Expert (mutaxassis, ekspert, специалист), ProductionEmployee (ishlab chiqarish xodimi,
  производственный персонал), ServiceEmployee (xizmat ko'rsatuvchi xodim, обслуживающий персонал). Each once.
- typeOfService: CivilServant (davlat xizmatchisi, давлат хизматчиси, госслужащий) or NotCivilServant (davlat
  xizmatchisi bo'lmagan, не госслужащий).
- institutionTypes: organisation kind in singular (Vazirlik, Qo'mita, Agentlik, Hokimlik, Bank).
- academicTitle: Dotsent, Professor. academicDegree: Fan nomzodi, Fan doktori, PhD, DSc.
- Flags: ilmiy darajasi bor -> isAcademicDegree; ilmiy unvoni bor -> isAcademicTitle; mukofotlangan -> isAwarded;
  zaxirada -> isReserved; xorijda o'qigan -> isGlobalEducation.
- fromAge is a minimum (yoshdan katta, старше); toAge is a maximum (yoshgacha, до, моложе, не старше).
  Birth years: fromYear, toYear. Birth place: birthRegion.
- Copy person names exactly: Latin stays Latin, Cyrillic stays Cyrillic.
- term: only a concrete topic that fits no other key."""

FEW_SHOTS: list[tuple[str, dict]] = [
    ("Юнусобод туманида ишлайдиган, камида 7 йил стажи бор ўрта махсус маълумотли техниклар",
     {"district": "Yunusobod", "experienceYear": 7, "education": "O'rta maxsus", "typeOfPosition": ["Technic"]}),
    ("Врачи из Ферганы старше 40 лет, профессора, госслужащие, работающие в агентствах",
     {"specialists": ["Tibbiyot"], "region": "Farg'ona", "fromAge": 40, "academicTitle": "Professor",
      "typeOfService": "CivilServant", "institutionTypes": ["Agentlik"]}),
    ("Исми Дилноза, отасининг исми Рустамовна",
     {"firstName": "Дилноза", "middleName": "Рустамовна"}),
    ("Usmonova Nodira Bahodirovna",
     {"lastName": "Usmonova", "firstName": "Nodira", "middleName": "Bahodirovna"}),
    ("Ilmiy unvoni bor, zaxirada turgan, nemis tilini biladigan xodimlar",
     {"isAcademicTitle": True, "isReserved": True, "language": "Nemis tili"}),
    ("Buxoroda tug'ilgan, Navoiyda ishlaydigan 1985-1995 yillarda tug'ilgan dasturchilar",
     {"birthRegion": "Buxoro", "region": "Navoiy", "fromYear": 1985, "toYear": 1995,
      "specialists": ["Axborot texnologiyalari"]}),
    ("Bank rahbarlari orasidan davlat xizmatchisi bo'lmaganlar",
     {"institutionTypes": ["Bank"], "typeOfPosition": ["Leader"], "typeOfService": "NotCivilServant"}),
    ("Менга ажойиб жамоа керак", {}),
]

RETRY_MESSAGE = "Return only valid JSON matching the schema."


def build_messages(query: str, cfg=None) -> list[dict[str, str]]:
    """Chat messages for Qwen. `cfg` is an app.prompts.store.PromptConfig; default = the active prompt."""
    if cfg is None:
        from app.prompts.store import get_active  # lazy: store imports this module

        cfg = get_active()
    messages: list[dict[str, str]] = [{"role": "system", "content": cfg.system_prompt}]
    for user, answer in cfg.few_shots:
        messages.append({"role": "user", "content": user})
        messages.append({"role": "assistant", "content": json.dumps(answer, ensure_ascii=False, separators=(",", ":"))})
    messages.append({"role": "user", "content": query})
    return messages


def build_retry_messages(query: str, bad_output: str, cfg=None) -> list[dict[str, str]]:
    messages = build_messages(query, cfg)
    messages.append({"role": "assistant", "content": bad_output[:500]})
    messages.append({"role": "user", "content": cfg.retry_message if cfg is not None else RETRY_MESSAGE})
    return messages
