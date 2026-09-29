import pytest

from app.models.enums import TypeOfPosition, TypeOfService, parse_type_of_position, parse_type_of_service


def test_type_of_position_values():
    assert TypeOfPosition.All == 0
    assert TypeOfPosition.Leader == 1
    assert TypeOfPosition.Management == 2
    assert TypeOfPosition.Technic == 3
    assert TypeOfPosition.Expert == 4
    assert TypeOfPosition.ProductionEmployee == 5
    assert TypeOfPosition.ServiceEmployee == 6


def test_type_of_service_values():
    assert TypeOfService.NotCivilServant == 0
    assert TypeOfService.CivilServant == 1


POSITION_PHRASES = {
    1: ["rahbar", "boshliq", "direktor", "bo'lim boshlig'i", "raxbar", "руководитель", "начальник", "директор"],
    2: ["boshqaruv xodimi", "menejer", "менеджер", "управленческий персонал"],
    3: ["texnik", "техник", "technician"],
    4: ["mutaxassis", "ekspert", "yetakchi mutaxassis", "специалист", "эксперт"],
    5: ["ishlab chiqarish xodimi", "производственный персонал"],
    6: ["xizmat ko'rsatuvchi xodim", "обслуживающий персонал"],
}

SERVICE_PHRASES = {
    1: ["davlat xizmatchisi", "davlat fuqarolik xizmatchisi", "госслужащий", "государственный служащий"],
    0: ["davlat xizmatchisi bo'lmagan", "не госслужащий"],
}


@pytest.mark.parametrize(
    ("phrase", "value"), [(p, v) for v, ps in POSITION_PHRASES.items() for p in ps]
)
def test_position_phrases(phrase, value):
    member, _how = parse_type_of_position(phrase)
    assert member is not None, phrase
    assert int(member) == value


@pytest.mark.parametrize(("phrase", "value"), [(p, v) for v, ps in SERVICE_PHRASES.items() for p in ps])
def test_service_phrases(phrase, value):
    member, _how = parse_type_of_service(phrase)
    assert member is not None, phrase
    assert int(member) == value


@pytest.mark.parametrize(
    ("value", "expected"),
    [("Leader", 1), ("Management", 2), ("Technic", 3), ("Expert", 4), ("ProductionEmployee", 5),
     ("ServiceEmployee", 6), (3, 3), ("3", 3), ("direktorlar", 1), ("Rahbar", 1), ("Директор", 1)],
)
def test_position_names_ints_plurals(value, expected):
    member, _ = parse_type_of_position(value)
    assert member is not None
    assert int(member) == expected


def test_technic_is_value_3():
    member, _ = parse_type_of_position("Technic")
    assert member is TypeOfPosition.Technic
    assert int(member) == 3


@pytest.mark.parametrize(("value", "expected"), [("CivilServant", 1), ("NotCivilServant", 0), (1, 1), (0, 0)])
def test_service_names_and_ints(value, expected):
    member, _ = parse_type_of_service(value)
    assert member is not None
    assert int(member) == expected


@pytest.mark.parametrize("value", ["banan", "", 99, "xyzzy"])
def test_unknown_position_is_none(value):
    member, _ = parse_type_of_position(value)
    assert member is None


@pytest.mark.parametrize("value", ["banan", 7])
def test_unknown_service_is_none(value):
    member, _ = parse_type_of_service(value)
    assert member is None
