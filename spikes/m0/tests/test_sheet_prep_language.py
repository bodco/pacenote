"""PB-32 S4: the Meeting Sheet and the brief are written in the language the app resolved (the call language)."""
from livetips_m0 import localprep
from livetips_m0.sheet import prompt as P


def test_sheet_system_prompt_names_the_language():
    s = P.system("uk", ["uk", "en"])
    assert "Write the fact texts in Ukrainian" in s
    assert "Ukrainian, English" in s


def test_sheet_language_beyond_the_old_six():
    assert P.lang_name("it") == "Italian"
    assert P.lang_name("xx") == "xx"
    assert "Write the fact texts in Italian" in P.system("it", ["it"])


def test_brief_language_names():
    assert localprep.lang_name("en") == "English"
    assert localprep.lang_name("ru") == "Russian"
    assert localprep.lang_name("pt-BR") == "Portuguese"
    assert localprep.lang_name("") == "English"
