"""PACK (M4b): the review pack is generated from the catalogue and never claims a review that the catalogue does not carry."""
import re
from pathlib import Path

import yaml

from physicore_collector.localization.vocabulary import find_forbidden
from physicore_collector.rootcause import catalogue as C, review_pack as R
from tests.rootcause.test_catalogue import user_entry, user_file

ROOT = Path(__file__).resolve().parents[2]


def test_PACK_01_the_committed_review_pack_is_not_stale():
    assert (ROOT / "docs/M4_CATALOGUE_REVIEW.md").read_text() == R.render()


def test_PACK_02_it_lists_every_entry_and_states_all_are_proposed():
    t = R.render()
    for e in C.builtin().entries:
        assert f"## `{e['id']}`" in t
    assert "12 entries; 12 proposed, 0 reviewed" in t and "Reviewed by" not in t


def test_PACK_03_prose_passes_the_wording_screen():
    assert find_forbidden(re.sub(r"`[^`]*`", "", R.render())) == []


def test_PACK_04_a_reviewed_entry_shows_its_reviewer_and_date_and_user_entries_show_user_supplied():
    d = yaml.safe_load(__import__("physicore_collector.rootcause.catalogue_v1", fromlist=["x"]).CATALOGUE_V1_YAML)
    d["entries"][0]["review"] = {"status": "reviewed", "note": "Checked.", "reviewed_by": "A. Person", "reviewed_on": "2026-11-10"}
    r = C.parse_catalogue(yaml.safe_dump(d).encode(), supplied_by="builtin")
    cat = C.Catalogue(tuple(r.entries), C.digest_of(r.entries), ())
    t = R.render(cat)
    assert "Reviewed by A. Person on 2026-11-10" in t and "11 proposed, 1 reviewed" in t
    u = C.parse_catalogue(yaml.safe_dump(user_file(user_entry())).encode())
    assert "Review status: user_supplied" in R.render(C.merged(u))


def test_PACK_05_the_guide_exists_and_says_entries_are_proposed():
    g = (ROOT / "docs/M4_CATALOGUE.md").read_text()
    assert "proposed" in g and "outstanding" in g
