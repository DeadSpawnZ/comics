"""Publishing titles matched the way a catalogue writes them (comics.helper.title_key): punctuation
between words, acronym dots, "&"/"and" and "vs."/"versus" are ignored when matching an imported
series, while the titles stay as printed."""

import copy
import dataclasses
from typing import Any

from django.db import connection
from django.test import SimpleTestCase
from django.test.utils import CaptureQueriesContext

from comics.catalog_sources import metron
from comics.catalog_sources.mapper import build_proposal, match_publishing, propose_publishing
from comics.catalog_sources.types import SourceIssue, SourceSeries
from comics.helper import title_key

from .factories import make_editorial, make_publishing
from .test_catalog_sources import METRON_ISSUE, CatalogTestCase

EQUIVALENT = [
    ("Grifter / Badrock", "Grifter Badrock"),
    ("Grifter/Badrock", "grifter  badrock"),
    ("Batman vs. Predator", "Batman Versus Predator"),
    ("Batman vs. Predator", "Batman vs Predator"),
    ("Batman VS Predator", "batman versus predator"),
    ("Marvel/DC", "Marvel DC"),
    ("Crossover Classics La Colección Marvel/DC", "Crossover Classics La Coleccion Marvel DC"),
    ("JLA WildC.A.T.S.", "JLA WildCATS"),
    ("JLA/WildC.A.T.S.", "JLA WildCATS"),
    ("S.H.I.E.L.D.", "SHIELD"),
    ("Gen 13 # 13 A,B & C Collected Edition", "Gen 13 13 A B and C Collected Edition"),
    ("Archie & Friends", "Archie and Friends"),
    ("Batman: Year One", "Batman - Year One"),
    ("Batman: Year One", "Batman – Year One"),
    ("Spider-Man", "Spider Man"),
    ("X-Men", "X Men"),
    ("Who's Who?", "Who’s Who!"),
    ("“Hello”, Batman", '"Hello" Batman'),
    ("Mr. Fantastic", "Mr Fantastic"),
]

NOT_EQUIVALENT = [
    ("Archie vs. Predator", "Archie vs. Predator II"),
    ("Grifter Badrock", "Grifter and the Mask"),
    ("Batman vs Depredador", "Batman vs. Predator"),
    ("Spider-Man", "Spiderman"),  # letters are never joined: a hyphen is a separator
    ("X-Men", "XMen"),
    ("Batman & Robin", "Batman Robin"),  # "&" is a word ("and"), not a separator
    ("Mr. Fantastic", "MrFantastic"),  # only an acronym's dots join letters
    ("Batman 2.0", "Batman 20"),  # nor do the dots between digits
    ("Batman vs Superman", "Batman v Superman"),
]


class TitleKeyTests(SimpleTestCase):
    def test_equivalent_titles(self) -> None:
        for first, second in EQUIVALENT:
            with self.subTest(first=first, second=second):
                self.assertEqual(title_key(first), title_key(second))

    def test_titles_that_are_not_equivalent(self) -> None:
        for first, second in NOT_EQUIVALENT:
            with self.subTest(first=first, second=second):
                self.assertNotEqual(title_key(first), title_key(second))

    def test_keys(self) -> None:
        self.assertEqual(title_key("JLA WildC.A.T.S."), "jla wildcats")
        self.assertEqual(title_key("Batman Versus Predator"), "batman vs predator")
        self.assertEqual(title_key(" / - : "), "")


def metron_issue(series_name: str, year: int = 1995) -> dict[str, Any]:
    data = copy.deepcopy(METRON_ISSUE)
    data["series"].update({"name": series_name, "year_began": year, "volume": 1})
    return data


class MatchPublishingByTitleKeyTests(CatalogTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.image = make_editorial(name="Image")
        self.grifter = make_publishing(
            publishing_title="Grifter Badrock", year=1995, serie="1st", language="en", editorials=[self.image]
        )
        make_publishing(publishing_title="Grifter and the Mask", year=1995, language="en")
        make_publishing(publishing_title="Batman vs Depredador", year=1995, language="es")

    def source(self, series_name: str, year: int = 1995) -> SourceIssue:
        return metron.to_source_issue(metron_issue(series_name, year))

    def test_a_metron_series_written_with_a_slash_matches(self) -> None:
        with CaptureQueriesContext(connection) as queries:
            publishing, candidates = match_publishing(self.source("Grifter / Badrock"), self.image)
        self.assertEqual((publishing, candidates), (self.grifter, []))
        # The series ids, the exact title, the keyed titles of the year, then the match and its editorials.
        self.assertEqual(len(queries), 5)

    def test_the_query_count_does_not_grow_with_the_publishings(self) -> None:
        for number in range(5):
            make_publishing(publishing_title=f"Grifter / Badrock {number}", year=1995, language="en")
            make_publishing(publishing_title="Grifter: Badrock", year=1990 + number, language="en")
        with self.assertNumQueries(5):
            publishing, _candidates = match_publishing(self.source("Grifter / Badrock"), None)
        self.assertEqual(publishing, self.grifter)

    def test_a_different_series_does_not_match(self) -> None:
        with self.assertNumQueries(3):  # nothing keyed alike: no publishing is loaded
            publishing, candidates = match_publishing(self.source("Grifter / Shadowhawk"), self.image)
        self.assertEqual((publishing, candidates), (None, []))

    def test_another_year_is_another_publishing(self) -> None:
        self.assertEqual(match_publishing(self.source("Grifter / Badrock", 1996), self.image), (None, []))

    def test_another_language_is_another_publishing(self) -> None:
        source = self.source("Batman vs. Depredador")
        self.assertEqual(source.language, "en")
        self.assertEqual(match_publishing(source, None), (None, []))  # the Spanish one is not keyed

    def test_the_key_is_tried_when_no_case_match_has_the_year(self) -> None:
        make_publishing(publishing_title="Batman & Robin", year=2009, language="en")
        later = make_publishing(publishing_title="Batman and Robin", year=2011, language="en")
        warnings: list[str] = []
        # The series ids, the case matches and their editorials, the keyed titles, the key match and its editorials.
        with self.assertNumQueries(6):
            publishing, candidates = match_publishing(self.source("Batman & Robin", 2011), None, warnings=warnings)
        self.assertEqual((publishing, candidates), (later, []))
        self.assertEqual(
            warnings, ["Matched “Batman & Robin” as “Batman and Robin”: check that it is the same series."]
        )

    def test_the_key_is_tried_when_no_case_match_has_the_language(self) -> None:
        make_publishing(publishing_title="Spawn / Batman", year=1995, language="es")
        english = make_publishing(publishing_title="Spawn Batman", year=1995, language="en")
        self.assertEqual(match_publishing(self.source("Spawn / Batman"), None), (english, []))

    def test_case_matches_stay_candidates_when_nothing_is_keyed_alike(self) -> None:
        older = make_publishing(publishing_title="Spawn", year=1992, language="en")
        with self.assertNumQueries(4):  # the series ids, the case matches and their editorials, the keyed titles
            self.assertEqual(match_publishing(self.source("Spawn"), None), (None, [older]))

    def test_the_exact_title_wins_over_the_key(self) -> None:
        exact = make_publishing(publishing_title="Grifter / Badrock", year=1995, serie="1st", language="en")
        warnings: list[str] = []
        self.assertEqual(match_publishing(self.source("grifter / badrock"), self.image, warnings=warnings), (exact, []))
        self.assertEqual(warnings, [])  # the same title, only in another case

    def test_without_a_year_every_publishing_is_compared(self) -> None:
        source = dataclasses.replace(self.source("Grifter / Badrock"), series_year=None)
        self.assertEqual(match_publishing(source, self.image), (self.grifter, []))

    def test_the_proposal_selects_the_publishing_and_warns(self) -> None:
        proposal = build_proposal(self.source("Grifter / Badrock"))
        self.assertEqual(proposal.publishing["id"], self.grifter.pk)
        self.assertIsNone(proposal.new_publishing)
        self.assertIn(
            "Matched “Grifter / Badrock” as “Grifter Badrock”: check that it is the same series.", proposal.warnings
        )


class SlashTitleWarningTests(CatalogTestCase):
    def test_a_title_with_a_spaced_slash_is_proposed_as_written_with_a_warning(self) -> None:
        source = metron.to_source_issue(metron_issue("Grifter / Badrock"))
        proposed = propose_publishing(source, SourceSeries(), None)
        self.assertEqual(proposed.publishing_title, "Grifter / Badrock")
        self.assertIn(
            "Metron writes the title as “Grifter / Badrock”: check it against the cover (Comi keeps the title as "
            "printed).",
            proposed.warnings,
        )

    def test_a_slash_without_spaces_is_not_warned(self) -> None:
        source = metron.to_source_issue(metron_issue("Marvel/DC"))
        proposed = propose_publishing(source, SourceSeries(), None)
        self.assertEqual(proposed.publishing_title, "Marvel/DC")
        self.assertFalse(any("writes the title as" in warning for warning in proposed.warnings))
