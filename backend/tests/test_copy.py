"""Spoken copy is only what a presenter says: nothing a model leaked into a text field reaches the air, and
opening habits stay with the opener (a story segment that "opens with the time" invents one)."""

from src.studio.presenters import load_presenters
from src.studio.scriptwriter import leaked, story_habits


def test_leaked_structure_and_notes_are_caught():
    assert leaked('Ozi taa gụnyere ụbọchị iri gara aga."} Actually let me fix formatting. {') == "}"
    assert leaked("Here is the translation: Sannu da safe.") == "Here is the translation"
    assert leaked("Kano <b>police</b> said so.") == "<"


def test_ordinary_copy_is_not_flagged():
    for line in ["Let us be clear about what we know.", "Here is your midday check-in.",
                 "Ẹ kú àárọ̀, ẹ̀yin olùgbọ́ wa.", "The APC and the PDP said different things about prices."]:
        assert leaked(line) is None, line


def test_story_copy_gets_no_opening_or_closing_habits():
    hosts = load_presenters()
    assert story_habits(hosts["hausa_female1"]) == ["Reads the headlines slowly and repeats the most important one at the end."]
    for host in hosts.values():
        assert not any(h.startswith(("Opens", "Closes", "Signs off", "Ends")) for h in story_habits(host)), host.key
        assert not any(f" in {lang}" in h for lang in ("Hausa", "Yoruba", "Igbo", "Pidgin") for h in host.habits), \
            f"{host.key}: habits are written in English and translated; none may ask for another language"
