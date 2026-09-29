"""Human-readable labels (Uzbek Latin / Russian) for every ISearchModel field — used for UI chips."""

from __future__ import annotations

FIELD_LABELS: dict[str, tuple[str, str]] = {
    "inn": ("STIR", "ИНН"),
    "firstName": ("Ismi", "Имя"),
    "lastName": ("Familiyasi", "Фамилия"),
    "middleName": ("Otasining ismi", "Отчество"),
    "regionId": ("Hudud", "Регион"),
    "birthRegionId": ("Tug'ilgan hududi", "Регион рождения"),
    "districtId": ("Tuman", "Район"),
    "sexId": ("Jinsi", "Пол"),
    "nationId": ("Millati", "Национальность"),
    "languageId": ("Til bilishi", "Знание языка"),
    "fromAge": ("Yoshi (dan)", "Возраст (от)"),
    "toAge": ("Yoshi (gacha)", "Возраст (до)"),
    "educationId": ("Ma'lumoti", "Образование"),
    "fromYear": ("Tug'ilgan yili (dan)", "Год рождения (от)"),
    "toYear": ("Tug'ilgan yili (gacha)", "Год рождения (до)"),
    "specialistId": ("Mutaxassisligi", "Специальность"),
    "specialistIds": ("Mutaxassisligi", "Специальность"),
    "educationTypes": ("Ta'lim turlari", "Виды образования"),
    "eduTypeId": ("Ta'lim shakli", "Форма обучения"),
    "degreeId": ("Daraja", "Степень"),
    "academicDegreeId": ("Ilmiy darajasi", "Учёная степень"),
    "academicTitleId": ("Ilmiy unvoni", "Учёное звание"),
    "institutionInn": ("Tashkilot STIR", "ИНН организации"),
    "institutionId": ("Tashkilot", "Организация"),
    "institutionName": ("Tashkilot nomi", "Название организации"),
    "institutionTypeId": ("Tashkilot turi", "Тип организации"),
    "institutionGroupId": ("Tashkilot guruhi", "Группа организаций"),
    "typeOfPosition": ("Lavozim turi", "Тип должности"),
    "spCategoryOfPositionId": ("Lavozim toifasi", "Категория должности"),
    "isAwarded": ("Mukofotlangan", "Награждён"),
    "isReserved": ("Zaxirada", "В резерве"),
    "languageCertificateId": ("Til sertifikati", "Языковой сертификат"),
    "isAcademicDegree": ("Ilmiy darajasi bor", "Есть учёная степень"),
    "isAcademicTitle": ("Ilmiy unvoni bor", "Есть учёное звание"),
    "isGlobalEducation": ("Xorijda ta'lim olgan", "Зарубежное образование"),
    "term": ("Kalit so'z", "Ключевое слово"),
    "pinfl": ("JSHSHIR", "ПИНФЛ"),
    "first": ("Boshlanish", "Смещение"),
    "rows": ("Qatorlar soni", "Количество строк"),
    "experienceYear": ("Ish staji (yil)", "Стаж (лет)"),
    "dateOfReceipt": ("Qabul qilingan sana", "Дата приёма"),
    "typeOfService": ("Xizmat turi", "Вид службы"),
}

ENUM_LABELS: dict[str, dict[int, tuple[str, str]]] = {
    "typeOfPosition": {
        0: ("Barchasi", "Все"),
        1: ("Rahbar", "Руководитель"),
        2: ("Boshqaruv xodimi", "Управленческий персонал"),
        3: ("Texnik", "Техник"),
        4: ("Mutaxassis", "Специалист"),
        5: ("Ishlab chiqarish xodimi", "Производственный персонал"),
        6: ("Xizmat ko'rsatuvchi xodim", "Обслуживающий персонал"),
    },
    "typeOfService": {
        0: ("Davlat xizmatchisi emas", "Не госслужащий"),
        1: ("Davlat xizmatchisi", "Госслужащий"),
    },
}

BOOL_LABELS: dict[bool, tuple[str, str]] = {True: ("Ha", "Да"), False: ("Yo'q", "Нет")}


def label_for(field: str) -> tuple[str, str]:
    return FIELD_LABELS.get(field, (field, field))
