"""System prompt + few-shot examples for the intent parser. Bump PROMPT_VERSION on every change.

Few-shots deliberately differ from the golden eval queries (no test leakage). No IDs anywhere (CLAUDE.md §3.6).
"""

from __future__ import annotations

import json

PROMPT_VERSION = "1"

SYSTEM_PROMPT = """You are a search-query parser for an HR personnel advanced-search system.
Convert the user's request (Uzbek Latin, Uzbek Cyrillic or Russian) into ONE JSON object using only these keys:

{"region":str,"birthRegion":str,"district":str,"sex":"Erkak|Ayol","nation":str,"language":str,
"languageCertificate":str,"education":str,"eduType":str,"degree":str,"academicDegree":str,"academicTitle":str,
"specialists":[str],"institution":str,"institutionTypes":[str],"positionCategories":[str],
"typeOfPosition":["Leader|Management|Technic|Expert|ProductionEmployee|ServiceEmployee"],
"typeOfService":"CivilServant|NotCivilServant","fromAge":int,"toAge":int,"fromYear":int,"toYear":int,
"experienceYear":int,"firstName":str,"lastName":str,"middleName":str,"pinfl":str,"inn":int,
"isAwarded":bool,"isReserved":bool,"isAcademicDegree":bool,"isAcademicTitle":bool,"isGlobalEducation":bool,
"term":str}

Rules:
- Include ONLY criteria the user explicitly stated. Omit every other key. Never guess, never add defaults.
- If the request has no concrete criterion, output {}.
- Never output IDs or numbers for places, specialities, education etc. - only their names.
- Write names in Uzbek Latin in base form, without suffixes (Toshkentda -> "Toshkent", в Самарканде -> "Samarqand",
  ingliz tilini -> "Ingliz tili"). A profession becomes its field: iqtisodchi/экономист -> "Iqtisodiyot".
- sex: "Erkak" (erkak, мужчины) or "Ayol" (ayol, аёл, женщины).
- education: "Oliy" (oliy, олий, высшее), "O'rta maxsus", "O'rta".
- Age: "30 yoshdan katta"/"старше 30" -> fromAge 30; "40 yoshgacha"/"до 40 лет" -> toAge 40;
  "25-40 yosh"/"от 25 до 40" -> fromAge 25, toAge 40. Birth years -> fromYear/toYear.
- Experience: "kamida 5 yil", "5 yildan ortiq staj", "стаж от 5 лет" -> experienceYear 5.
- typeOfPosition: rahbar/boshliq/direktor/руководитель/начальник -> "Leader"; boshqaruv xodimi/menejer -> "Management";
  texnik -> "Technic"; mutaxassis/ekspert/специалист -> "Expert"; ishlab chiqarish xodimi -> "ProductionEmployee";
  xizmat ko'rsatuvchi xodim -> "ServiceEmployee".
- typeOfService: davlat xizmatchisi/госслужащий -> "CivilServant"; davlat xizmatchisi bo'lmagan -> "NotCivilServant".
- Booleans only when stated: mukofotlangan/награждённый -> isAwarded; zaxirada -> isReserved;
  ilmiy darajasi bor -> isAcademicDegree; ilmiy unvoni bor -> isAcademicTitle; xorijda o'qigan -> isGlobalEducation.
- Person names: copy exactly as written.
- term: only for a stated topic that fits no other key.
- Output JSON only. No explanations, no markdown."""

FEW_SHOTS: list[tuple[str, dict]] = [
    ("Buxoro viloyatida yashovchi 45 yoshgacha erkaklar",
     {"region": "Buxoro viloyati", "toAge": 45, "sex": "Erkak"}),
    ("Юнусобод туманидаги ўрта махсус маълумотли аёллар",
     {"district": "Yunusobod", "education": "O'rta maxsus", "sex": "Ayol"}),
    ("Юристы из Ферганы в возрасте от 28 до 50 лет, госслужащие",
     {"specialists": ["Huquqshunoslik"], "region": "Farg'ona", "fromAge": 28, "toAge": 50,
      "typeOfService": "CivilServant"}),
    ("kamida 10 yil ish stajiga ega texnik xodimlar va mutaxassislar",
     {"experienceYear": 10, "typeOfPosition": ["Technic", "Expert"]}),
    ("Ismi Dilnoza, otasining ismi Rustamovna",
     {"firstName": "Dilnoza", "middleName": "Rustamovna"}),
    ("Ilmiy unvoni bor, zaxirada turgan, nemis tilini biladigan xodimlar",
     {"isAcademicTitle": True, "isReserved": True, "language": "Nemis tili"}),
    ("Руководители с сертификатом TOEFL",
     {"typeOfPosition": ["Leader"], "languageCertificate": "TOEFL"}),
    ("1985-1995 yillarda tug'ilgan dasturchilar",
     {"fromYear": 1985, "toYear": 1995, "specialists": ["Axborot texnologiyalari"]}),
    ("Xorijda o'qigan, fan nomzodi darajasiga ega xodimlar",
     {"isGlobalEducation": True, "academicDegree": "Fan nomzodi"}),
    ("eng zo'r xodimlarni ko'rsat", {}),
]

RETRY_MESSAGE = "Return only valid JSON matching the schema."


def build_messages(query: str) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for user, answer in FEW_SHOTS:
        messages.append({"role": "user", "content": user})
        messages.append({"role": "assistant", "content": json.dumps(answer, ensure_ascii=False)})
    messages.append({"role": "user", "content": query})
    return messages


def build_retry_messages(query: str, bad_output: str) -> list[dict[str, str]]:
    messages = build_messages(query)
    messages.append({"role": "assistant", "content": bad_output[:500]})
    messages.append({"role": "user", "content": RETRY_MESSAGE})
    return messages
