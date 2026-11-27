"""Named people are not sent to stock search. Objects and unknown people still are."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from studio import footage  # noqa: E402


def _sent(monkeypatch, queries, text="", people=None):
    got = []
    monkeypatch.setattr(footage, "pexels", lambda q, *a, **k: got.append(q) or [])
    monkeypatch.setattr(footage, "pixabay", lambda q, *a, **k: [])
    footage.search_slot(queries, "portrait", text, lambda m: None, 2560, {}, people=people)
    return got


def test_a_kite_with_a_man_in_the_phrase_is_searched(monkeypatch):
    assert _sent(monkeypatch, ["a man flying a kite"], "the kite caught the wind", ["Donald Trump"]) == ["a man flying a kite"]


def test_a_server_rack_is_searched(monkeypatch):
    assert _sent(monkeypatch, ["technician beside a server rack"], "the racks filled the hall") == ["technician beside a server rack"]


def test_an_unknown_person_is_searched(monkeypatch):
    assert _sent(monkeypatch, ["surgeon in operating room"], "the operation took four hours") == ["surgeon in operating room"]
    assert footage._named_person({"name": "a surgeon", "kind": "person"}) is False


def test_a_named_subject_is_not_sent(monkeypatch):
    assert footage._named_person({"name": "Donald Trump", "kind": "person"}) is True
    assert _sent(monkeypatch, ["Donald Trump speaking", "oval office desk"], "Donald Trump announced it", ["Donald Trump"]) == ["oval office desk"]


def test_a_lowercase_name_from_the_story_is_not_sent(monkeypatch):
    assert _sent(monkeypatch, ["donald trump speaking"], "he announced it", ["Donald Trump"]) == []


def test_a_stand_in_for_a_named_person_is_not_sent(monkeypatch):
    text = "Prime Minister Hale announced the rule."
    assert _sent(monkeypatch, ["politician at a podium"], text) == []
    assert _sent(monkeypatch, ["classified folder on a desk"], text) == ["classified folder on a desk"]


def test_an_object_subject_is_still_prepended():
    assert footage.queries_for_stock(["runway landing"], "the jet diverted", people=["flydubai Boeing 737"]) == ["runway landing"]
    assert footage._named_person({"name": "flydubai Boeing 737", "kind": "aircraft"}) is False
