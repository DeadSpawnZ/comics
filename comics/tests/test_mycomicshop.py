"""MyComicShop source: the HTML download (POST, cache, Imperva challenge, errors), the series page
parser and the regular expressions that read each variant's free text. The fixtures are trimmed
copies of real series pages; the free-text strings in the tables are copied from those pages."""

from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import mock
from urllib.request import Request

from comics.catalog_sources import http, mycomicshop
from comics.catalog_sources.errors import BadResponse, NotFound, RateLimited, Unavailable
from comics.catalog_sources.http import decode_html, is_allowed_cover_url
from comics.catalog_sources.mycomicshop import (
    McsSeries,
    McsVariant,
    fetch_series,
    parse_free_text,
    parse_series,
    sentence_credits,
    split_label,
    split_sentences,
    take_price,
)

from .base import ComiTestCase
from .test_catalog_sources import OPEN_URL, CatalogTestCase, FakeResponse, http_error

FIXTURES = Path(__file__).parent / "fixtures"
BATMAN_SPAWN = (FIXTURES / "mycomicshop_series_57389539.html").read_bytes()  # has a stray cp1252 byte
VAMPIRELLA = (FIXTURES / "mycomicshop_series_65211284.html").read_bytes()
TMNT = (FIXTURES / "mycomicshop_series_58451662.html").read_bytes()
CHALLENGE = (
    b'<html><head><META NAME="robots" CONTENT="noindex,nofollow"><script src="/_Incapsula_Resource?'
    b'SWJIYLWA=719d34d31c8e3a6e6fffd425f7e032f3"></script><body></body></html>'
)
NO_RESULTS = b'<html><body><div id="resultstab"><ul class="results-detailed"></ul></div></body></html>'

# Real free texts (whitespace collapsed), shortened after the first words of the synopsis.
BATMAN_CREDITS = (
    "Written by Todd McFarlane. Art by Greg Capullo (pencils), Todd McFarlane (inks) and Jonathan Glapion "
    "(inks assist)."
)
BATMAN_SYNOPSIS = "Two dark heroes, cursed by tragedy, find their paths again crossing...but not by choice!"
VAMPI_SYNOPSIS = (
    "Two titans of comics collide as Dynamite Entertainment and Top Cow Productions unleash the fury of the "
    "Witchblade and Vampirella is this special red-hot one-shot! Vampirella has spent millennia defeating the "
    "forces of evil."
)
VAMPI_PRICE = "48 pages, full color, rated T+. Cover price $5.99."
TMNT3_SYNOPSIS = (
    "The gloves are off as the Turtles and Street Fighters team up in an all-out brawl against a squad of "
    "mysterious mercenaries"
)


def vampi(cover: str, note: str = "", middle: str = "", price: str = VAMPI_PRICE) -> str:
    return f"{note}(W) Erica Schultz (A) Valentina Pinti (CA) {cover} {VAMPI_SYNOPSIS}{middle} {price}"


def credits_of(text: str, siblings: tuple[str, ...] = ()) -> list[tuple[str, list[str], bool]]:
    return [(credit.name, credit.roles, credit.uncertain) for credit in parse_free_text(text, siblings).credits]


class SentenceAndLabelTests(ComiTestCase):
    def test_sentences_do_not_end_after_an_initial(self) -> None:
        cases = [
            (
                "Cover by J. Scott Campbell. Two dark heroes",
                ["Cover by J. Scott Campbell.", "Two dark heroes"],
            ),
            (
                "Cover by Freddie E. Williams II. When a prestigious martial arts tournament",
                ["Cover by Freddie E. Williams II.", "When a prestigious martial arts tournament"],
            ),
            (
                "enough with Baxter Stockman and M. Bison harvesting latent Psycho Power for their own use! Can they",
                ["enough with Baxter Stockman and M. Bison harvesting latent Psycho Power for their own use! Can they"],
            ),
            (
                "Art by John Romita Jr. and Klaus Janson. Cover by X.",
                ["Art by John Romita Jr. and Klaus Janson.", "Cover by X."],
            ),
            (
                "Limited 1 for 10 Retailer Incentive Variant Cover.(W) Erica Schultz",
                ["Limited 1 for 10 Retailer Incentive Variant Cover.(W) Erica Schultz"],
            ),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(split_sentences(text), expected)

    def test_labels_split_into_number_and_suffix(self) -> None:
        cases = {
            "1A": ("1", "A"),
            "1B&N": ("1", "B&N"),
            "1CUSTOM.B": ("1", "CUSTOM.B"),
            "1RI.A": ("1", "RI.A"),
            "2RIA": ("2", "RIA"),
            "4NYCC": ("4", "NYCC"),
            "12": ("12", ""),
            "1.5B": ("1.5", "B"),
            "nn": ("nn", ""),
        }
        for label, expected in cases.items():
            with self.subTest(label=label):
                self.assertEqual(split_label(label), expected)

    def test_price_is_the_last_cover_price(self) -> None:
        cases = [
            (
                "48 pages, full color. Rated Teen Cover price $6.99.",
                Decimal("6.99"),
                "48 pages, full color. Rated Teen",
            ),
            ("32 pages, full color. Rated M", None, "32 pages, full color. Rated M"),
            ("Cover price $3.99. Reprint. Cover price $1,004.50.", Decimal("1004.50"), "Cover price $3.99. Reprint."),
        ]
        for text, price, rest in cases:
            with self.subTest(text=text):
                self.assertEqual(take_price(text), (price, rest))


class NoteTests(ComiTestCase):
    """The variant note: everything before the first credit."""

    def test_note_facts(self) -> None:
        tmnt_credits = "Written by Paul Allor. Art by Ariel Medel. When a prestigious martial arts tournament"
        cases = [
            # text, ratio, print run, printing, stores, event
            (f"Limited 1 for 25 Retailer Incentive Variant Cover. {BATMAN_CREDITS}", "1:25", None, None, [], ""),
            (
                f"Limited 1 for 666 Retailer Incentive Variant Cover - Signed by Todd McFarlane. {BATMAN_CREDITS}",
                "1:666",
                None,
                None,
                [],
                "",
            ),
            (f"2nd Printing. {BATMAN_CREDITS}", "", None, 2, [], ""),
            (
                f"Carnivore Comics Exclusive Cover by Tiago Da Silva - Limited to 1,200 copies. {tmnt_credits}",
                "",
                1200,
                None,
                ["Carnivore Comics"],
                "",
            ),
            (
                "Final Order Comics, Bridgital Collective, Street Level Hero, and Bandukswami Comics joint Exclusive "
                f"Variant Cover by REIQ. {tmnt_credits}",
                "",
                None,
                None,
                ["Final Order Comics", "Bridgital Collective", "Street Level Hero", "Bandukswami Comics"],
                "",
            ),
            (
                f"ComicXposure and Epic Nation Collectibles exclusive cover by Sajad Shah. {tmnt_credits}",
                "",
                None,
                None,
                ["ComicXposure", "Epic Nation Collectibles"],
                "",
            ),
            (
                f"Barnes & Noble Exclusive Cover by Greg Capullo and Todd McFarlane. {BATMAN_CREDITS}",
                "",
                None,
                None,
                ["Barnes & Noble"],
                "",
            ),
            (
                f"New York Comic Con Exclusive Variant Cover by Rose Besch. {tmnt_credits}",
                "",
                None,
                None,
                [],
                "New York Comic Con",
            ),
            (vampi("John Royle", note="Limited 1 for 7 Retailer Incentive Variant Cover. "), "1:7", None, None, [], ""),
        ]
        for text, ratio, print_run, printing, stores, event in cases:
            with self.subTest(text=text[:60]):
                details = parse_free_text(text)
                self.assertEqual(
                    (details.ratio, details.print_run, details.printing, details.stores, details.event),
                    (ratio, print_run, printing, stores, event),
                )

    def test_cover_by_in_the_note_names_the_cover_artists_but_signed_by_does_not(self) -> None:
        carnivore = parse_free_text(
            "Carnivore Comics Exclusive Cover by Tiago Da Silva - Limited to 1200 copies. Written by Paul Allor. "
            "Art by Ariel Medel. When a prestigious martial arts tournament"
        )
        self.assertEqual(carnivore.note, "Carnivore Comics Exclusive Cover by Tiago Da Silva - Limited to 1200 copies.")
        self.assertEqual([(c.name, c.roles) for c in carnivore.credits if c.is_cover], [("Tiago Da Silva", ["Cover"])])
        signed = parse_free_text(
            "Limited 1 for 666 Retailer Incentive Variant Cover - Signed by Greg Capullo. Written by Todd McFarlane. "
            "Cover by Jason Fabok. Two dark heroes"
        )
        self.assertEqual([c.name for c in signed.credits if c.is_cover], ["Jason Fabok"])

    def test_exclusive_to_and_dashes(self) -> None:
        cases = [
            ("Cover by Jane Doe, exclusive to Midtown Comics.", ["Midtown Comics"], ["Jane Doe"]),
            ("Foil Cover - Midtown Comics Exclusive.", ["Midtown Comics"], []),
            (
                "Virgin Cover - Jolzar Collectibles and Devil Dog Comics Exclusive.",
                ["Jolzar Collectibles", "Devil Dog Comics"],
                [],
            ),
        ]
        for note, stores, covers in cases:
            with self.subTest(note=note):
                details = parse_free_text(f"{note} Written by Paul Allor. Art by Ariel Medel. When a prestigious")
                self.assertEqual(details.stores, stores)
                self.assertEqual([c.name for c in details.credits if c.is_cover], covers)

    def test_a_note_without_credits(self) -> None:
        cases = [
            # text, note, synopsis, ratio, printing, event, price
            (
                "Limited 1 for 25 Retailer Incentive Variant Cover. Cover price $3.99.",
                "Limited 1 for 25 Retailer Incentive Variant Cover.",
                "",
                "1:25",
                None,
                "",
                Decimal("3.99"),
            ),
            ("2nd Printing.", "2nd Printing.", "", "", 2, "", None),
            (
                f"New York Comic Con Exclusive Variant Cover. {BATMAN_SYNOPSIS} Cover price $3.99.",
                "New York Comic Con Exclusive Variant Cover.",
                BATMAN_SYNOPSIS,
                "",
                None,
                "New York Comic Con",
                Decimal("3.99"),
            ),
            (BATMAN_SYNOPSIS, "", BATMAN_SYNOPSIS, "", None, "", None),
        ]
        for text, note, synopsis, ratio, printing, event, price in cases:
            with self.subTest(text=text[:50]):
                details = parse_free_text(text)
                self.assertEqual(
                    (details.note, details.synopsis, details.ratio, details.printing, details.event, details.price),
                    (note, synopsis, ratio, printing, event, price),
                )
                self.assertEqual(details.credits, [])

    def test_exclusive_to_stops_at_a_dash(self) -> None:
        details = parse_free_text(
            "Cover by Jane Doe, exclusive to Midtown Comics - Limited to 500 copies. Written by Paul Allor. When a"
        )
        self.assertEqual((details.stores, details.print_run), (["Midtown Comics"], 500))
        self.assertEqual([c.name for c in details.credits if c.is_cover], ["Jane Doe"])

    def test_which_sentences_without_credits_are_a_note(self) -> None:
        collects = "This special edition collects issues one to three."
        long_sentence = (
            "Our heroes find a variant of themselves on a cover of a magazine and the hunt for its origin begins."
        )
        cases = [
            # text, note, synopsis
            (f"Foil cover. {BATMAN_SYNOPSIS}", "Foil cover.", BATMAN_SYNOPSIS),
            (f"Blank Sketch Cover. {BATMAN_SYNOPSIS}", "Blank Sketch Cover.", BATMAN_SYNOPSIS),
            (f"Limited to 500 copies. {BATMAN_SYNOPSIS}", "Limited to 500 copies.", BATMAN_SYNOPSIS),
            (f"Midtown Comics Exclusive. {BATMAN_SYNOPSIS}", "Midtown Comics Exclusive.", BATMAN_SYNOPSIS),
            (f"{collects} {BATMAN_SYNOPSIS}", "", f"{collects} {BATMAN_SYNOPSIS}"),  # "edition", no cover word
            (long_sentence, "", long_sentence),  # cover words, but too long for a note
        ]
        for text, note, synopsis in cases:
            with self.subTest(text=text[:40]):
                details = parse_free_text(text)
                self.assertEqual((details.note, details.synopsis), (note, synopsis))

    def test_a_reprint_without_price(self) -> None:
        details = parse_free_text(
            "McFarlane Toys Store Exclusive Action Figure Foil Cover. Reprints Batman Spawn (2022 DC) #1. Story by "
            "Todd McFarlane. Art by Greg Capullo (pencils), Todd McFarlane (inks) and Jonathan Glapion (inks assist)."
        )
        self.assertEqual(
            details.note, "McFarlane Toys Store Exclusive Action Figure Foil Cover. Reprints Batman Spawn (2022 DC) #1."
        )
        self.assertEqual(details.stores, ["McFarlane Toys Store"])
        self.assertIsNone(details.price)
        self.assertEqual(details.synopsis, "")
        self.assertEqual(details.credits[0].name, "Todd McFarlane")
        self.assertEqual(details.credits[0].roles, ["Story", "Inker"])


class SentenceCreditTests(ComiTestCase):
    """Credits written as sentences: "Written by X. Art by Y (pencils). Cover by Z."."""

    def test_credits(self) -> None:
        cases = [
            (
                f"{BATMAN_CREDITS} Cover by Greg Capullo and Todd McFarlane. {BATMAN_SYNOPSIS}",
                [
                    ("Todd McFarlane", ["Writer", "Inker", "Cover"], False),
                    ("Greg Capullo", ["Penciller", "Cover"], False),
                    ("Jonathan Glapion", ["Inks Assist"], False),
                ],
            ),
            (
                f"{BATMAN_CREDITS} Cover by Greg Capullo, Todd McFarlane. {BATMAN_SYNOPSIS}",
                [
                    ("Todd McFarlane", ["Writer", "Inker", "Cover"], False),
                    ("Greg Capullo", ["Penciller", "Cover"], False),
                    ("Jonathan Glapion", ["Inks Assist"], False),
                ],
            ),
            (
                f"{BATMAN_CREDITS} Cover by J. Scott Campbell. {BATMAN_SYNOPSIS}",
                [
                    ("Todd McFarlane", ["Writer", "Inker"], False),
                    ("Greg Capullo", ["Penciller"], False),
                    ("Jonathan Glapion", ["Inks Assist"], False),
                    ("J. Scott Campbell", ["Cover"], False),
                ],
            ),
            (
                "Written by Paul Allor. Art by Ariel Medel. Cover by Freddie E. Williams II. When a prestigious "
                "martial arts tournament",
                [
                    ("Paul Allor", ["Writer"], False),
                    ("Ariel Medel", ["Artist"], False),
                    ("Freddie E. Williams II", ["Cover"], False),
                ],
            ),
            (
                "Written by Paul Allor. Art by Ariel Medel. Cover by Ariel Medel. With Raphael and Chun-Li kidnapped",
                [("Paul Allor", ["Writer"], False), ("Ariel Medel", ["Artist", "Cover"], False)],
            ),
            (
                "Story and art by Jim Lee & Scott Williams. Colors by Alex Sinclair. Letters by Richard Starkings. Done.",
                [
                    ("Jim Lee", ["Story", "Artist"], False),
                    ("Scott Williams", ["Story", "Artist"], False),
                    ("Alex Sinclair", ["Colorist"], False),
                    ("Richard Starkings", ["Letterer"], False),
                ],
            ),
            (
                "Written and drawn by Jeff Smith. Cover art by Frank Miller. Done.",
                [("Jeff Smith", ["Writer", "Artist"], False), ("Frank Miller", ["Cover"], False)],
            ),
        ]
        for text, expected in cases:
            with self.subTest(text=text[-60:]):
                self.assertEqual(credits_of(text), expected)

    def test_phrases_match_their_role_whatever_the_spacing_or_case(self) -> None:
        for phrases in ("Cover art", "Cover\n  Art", "COVER ART"):
            with self.subTest(phrases=phrases):
                self.assertEqual(
                    [(c.name, c.roles) for c in sentence_credits(phrases, "Frank Miller")],
                    [("Frank Miller", ["Cover"])],
                )

    def test_parts_of_a_full_text(self) -> None:
        text = (
            "Barnes & Noble Exclusive Cover by Greg Capullo and Todd McFarlane. Art by Greg Capullo (pencils), Todd "
            "McFarlane (inks) and Jonathan Glapion (inks assist). Written by Todd McFarlane. Two dark heroes, cursed "
            "by tragedy, find their paths again crossing...but not by choice! 48 pages, full color. Rated Teen "
            "Cover price $12.99."
        )
        details = parse_free_text(text)
        self.assertEqual(details.text, text)
        self.assertEqual(details.note, "Barnes & Noble Exclusive Cover by Greg Capullo and Todd McFarlane.")
        self.assertEqual(
            details.synopsis,
            "Two dark heroes, cursed by tragedy, find their paths again crossing...but not by choice! 48 pages, "
            "full color. Rated Teen",
        )
        self.assertEqual(details.price, Decimal("12.99"))
        todd = next(credit for credit in details.credits if credit.name == "Todd McFarlane")
        self.assertEqual(todd.roles, ["Cover", "Inker", "Writer"])
        self.assertTrue(todd.is_cover and todd.is_story)
        glapion = next(credit for credit in details.credits if credit.name == "Jonathan Glapion")
        self.assertTrue(glapion.is_story)
        self.assertFalse(glapion.is_cover)
        self.assertFalse(details.uncertain)


class CodeCreditTests(ComiTestCase):
    """Solicitation codes, "(W) X (A) Y (CA) Z Synopsis…": the other variants of the issue tell where
    the last name ends."""

    def test_the_other_variants_tell_where_the_synopsis_starts(self) -> None:
        group = {
            "Stjepan Sejic": vampi("Stjepan Sejic"),
            "Lucio Parrillo": vampi("Lucio Parrillo"),
            "Joseph Michael Linsner": vampi("Joseph Michael Linsner"),
            "John Royle": vampi("John Royle", note="Limited 1 for 7 Retailer Incentive Variant Cover. "),
            "Francesco Mattina": vampi("Francesco Mattina", note="Limited 1 for 20 Retailer Incentive Variant Cover."),
        }
        for cover, text in group.items():
            with self.subTest(cover=cover):
                siblings = tuple(other for other in group.values() if other != text)
                details = parse_free_text(text, siblings)
                self.assertEqual(
                    [(c.name, c.roles, c.uncertain) for c in details.credits],
                    [
                        ("Erica Schultz", ["Writer"], False),
                        ("Valentina Pinti", ["Artist"], False),
                        (cover, ["Cover"], False),
                    ],
                )
                self.assertEqual(details.synopsis, VAMPI_SYNOPSIS + " 48 pages, full color, rated T+.")
                self.assertEqual(details.price, Decimal("5.99"))
        self.assertEqual(
            parse_free_text(group["Francesco Mattina"]).note, "Limited 1 for 20 Retailer Incentive Variant Cover."
        )

    def test_the_synopsis_may_end_differently(self) -> None:
        blind_bag = vampi(
            "Blind Bag",
            middle=" This issue features a PREMIUM MYSTERY BLIND BAG which contains three limited editions of the issue.",
            price="48 pages, full color, rated T+. Cover price $19.99.",
        )
        details = parse_free_text(blind_bag, (vampi("Stjepan Sejic"), vampi("Lucio Parrillo")))
        self.assertTrue(details.synopsis.startswith("Two titans of comics collide"))
        self.assertIn("PREMIUM MYSTERY BLIND BAG", details.synopsis)
        # "Blind Bag" (and "Blank Authentix") stand where the cover artist goes, but name nobody.
        self.assertEqual([c.name for c in details.credits], ["Erica Schultz", "Valentina Pinti"])
        self.assertEqual(details.price, Decimal("19.99"))
        authentix = parse_free_text(vampi("Blank Authentix"), (vampi("Stjepan Sejic"),))
        self.assertEqual([c.name for c in authentix.credits], ["Erica Schultz", "Valentina Pinti"])

    def test_a_sentence_style_sibling_tells_where_the_synopsis_starts(self) -> None:
        rid = (
            "Limited 1 for 250 Retailer Incentive Variant Cover. (W) Paul Allor (A) Ariel Medel (CA) Khary Randolph "
            f"(C) Sarah Myer {TMNT3_SYNOPSIS} Cover price $3.99."
        )
        sibling = (
            f"Written by Paul Allor. Art by Ariel Medel. Cover by Ariel Medel. {TMNT3_SYNOPSIS} Cover price $3.99."
        )
        details = parse_free_text(rid, (sibling,))
        self.assertEqual(
            [(c.name, c.roles, c.uncertain) for c in details.credits],
            [
                ("Paul Allor", ["Writer"], False),
                ("Ariel Medel", ["Artist"], False),
                ("Khary Randolph", ["Cover"], False),
                ("Sarah Myer", ["Colorist"], False),
            ],
        )
        self.assertEqual(details.ratio, "1:250")
        self.assertEqual(details.synopsis, TMNT3_SYNOPSIS)

    def test_names_sharing_their_last_word(self) -> None:
        jae, jim, sejic = vampi("Jae Lee"), vampi("Jim Lee"), vampi("Stjepan Sejic")
        # Alone, "Jim Lee" matches "Jae Lee" from "Lee": the cut is wrong but flagged.
        alone = parse_free_text(jae, (jim,))
        self.assertEqual((alone.credits[-1].name, alone.credits[-1].uncertain), ("Jae", True))
        # A third sibling moves the cut to the right place; the disagreement is still flagged.
        three = parse_free_text(jae, (jim, sejic))
        self.assertEqual((three.credits[-1].name, three.credits[-1].uncertain), ("Jae Lee", True))
        self.assertTrue(three.synopsis.startswith("Two titans"))
        # A sentence-style sibling gives the exact start.
        sentences = (
            f"Written by Erica Schultz. Art by Valentina Pinti. Cover by Jim Lee. {VAMPI_SYNOPSIS} {VAMPI_PRICE}"
        )
        exact = parse_free_text(jae, (jim, sentences))
        self.assertEqual((exact.credits[-1].name, exact.credits[-1].uncertain), ("Jae Lee", False))
        # Several siblings with different names agree: certain.
        agreed = parse_free_text(sejic, (jim, vampi("Lucio Parrillo")))
        self.assertEqual((agreed.credits[-1].name, agreed.credits[-1].uncertain), ("Stjepan Sejic", False))

    def test_combined_codes(self) -> None:
        text = f"(W/A/CA) Ariel Medel {TMNT3_SYNOPSIS}"
        sibling = f"Written by Paul Allor. Art by Ariel Medel. {TMNT3_SYNOPSIS}"
        self.assertEqual(credits_of(text, (sibling,)), [("Ariel Medel", ["Writer", "Artist", "Cover"], False)])

    def test_without_help_two_words_are_taken_and_flagged(self) -> None:
        details = parse_free_text(vampi("Joseph Michael Linsner"))
        self.assertEqual(details.credits[-1].name, "Joseph Michael")
        self.assertTrue(details.credits[-1].uncertain)
        self.assertTrue(details.uncertain)
        self.assertTrue(details.synopsis.startswith("Linsner Two titans"))
        # Siblings with the same cover artist say nothing either.
        same = parse_free_text(vampi("Lucio Parrillo"), (vampi("Lucio Parrillo", note="Foil cover. "),))
        self.assertTrue(same.uncertain)


class SeriesPageTests(ComiTestCase):
    def variant(self, series: McsSeries, label: str) -> McsVariant:
        return next(variant for variant in series.variants if variant.label == label)

    def test_series_and_variants(self) -> None:
        series = parse_series(decode_html(BATMAN_SPAWN))
        self.assertEqual(
            (series.tid, series.title, series.year, series.publisher_short, series.publisher),
            (57389539, "Batman Spawn", 2023, "DC", "DC"),
        )
        self.assertEqual([v.label for v in series.variants], ["1A", "1B&N", "1CUSTOM.B", "1L", "1P", "1U"])
        first = series.variants[0]
        self.assertEqual((first.ivid, first.number, first.suffix), (57507058, "1", "A"))
        self.assertEqual((first.year, first.month, first.cover_date), (2023, 2, date(2023, 2, 1)))
        self.assertEqual(first.cover_url, "https://media.mycomicshop.com/n_iv/600/6672200.jpg")
        self.assertEqual(first.thumb_url, "https://media.mycomicshop.com/n_iv/120/6672200.jpg")
        self.assertTrue(is_allowed_cover_url(first.cover_url))
        self.assertEqual(first.details.price, Decimal("6.99"))
        reprint = self.variant(series, "1CUSTOM.B")
        self.assertEqual((reprint.suffix, reprint.month, reprint.details.price), ("CUSTOM.B", 8, None))
        self.assertEqual(self.variant(series, "1L").details.ratio, "1:25")
        self.assertEqual(self.variant(series, "1U").details.printing, 2)

    def test_stock_ebay_and_not_in_stock_are_ignored(self) -> None:
        series = parse_series(decode_html(BATMAN_SPAWN))
        in_stock = series.variants[0]  # its tab has a stock table
        self.assertTrue(in_stock.details.text.startswith("Written by Todd McFarlane."))
        without_stock = self.variant(series, "1B&N")  # its tab has the "not in stock" paragraph
        self.assertTrue(without_stock.details.text.startswith("Barnes & Noble Exclusive Cover"))
        for variant in series.variants:
            with self.subTest(label=variant.label):
                self.assertNotIn("not in stock", variant.details.text)
                self.assertNotIn("Add to cart", variant.details.text)
                self.assertNotIn("eBay", variant.details.text)

    def test_title_with_only_the_year(self) -> None:
        # Older series name no publisher in the title, e.g. "Shattered Image (1996)".
        page = BATMAN_SPAWN.replace(b"Batman Spawn (2023 DC)", b"Batman Spawn (2023)")
        series = parse_series(decode_html(page))
        self.assertEqual(
            (series.title, series.year, series.publisher_short, series.publisher), ("Batman Spawn", 2023, "", "DC")
        )

    def test_stray_cp1252_byte_keeps_its_character(self) -> None:
        self.assertIn(b"crossing\x85but", BATMAN_SPAWN)
        synopsis = parse_series(decode_html(BATMAN_SPAWN)).variants[0].details.synopsis
        self.assertIn("crossing…but not by choice!", synopsis)
        self.assertNotIn("�", synopsis)

    def test_code_style_group(self) -> None:
        series = parse_series(decode_html(VAMPIRELLA))
        self.assertEqual(
            (series.title, series.year, series.publisher_short, series.publisher),
            ("Vampirella X Witchblade Special", 2026, "Dynamite", "Dynamite Entertainment"),
        )
        covers = {v.label: [c.name for c in v.details.credits if c.is_cover] for v in series.variants}
        self.assertEqual(
            covers, {"1A": ["Stjepan Sejic"], "1B": ["Lucio Parrillo"], "1F": [], "1I": [], "1J": ["Lucio Parrillo"]}
        )
        self.assertFalse(any(v.details.uncertain for v in series.variants))
        blind_bag = self.variant(series, "1I")
        self.assertEqual((blind_bag.cover_url, blind_bag.thumb_url), ("", ""))  # the page has no picture
        self.assertEqual(blind_bag.details.price, Decimal("19.99"))
        self.assertEqual(self.variant(series, "1J").details.ratio, "1:10")

    def test_exclusives_and_mixed_styles(self) -> None:
        series = parse_series(decode_html(TMNT))
        self.assertEqual((series.publisher_short, series.publisher), ("IDW", "IDW Publishing"))
        carnivore = self.variant(series, "1CARNIVORE.B")
        self.assertEqual((carnivore.number, carnivore.suffix), ("1", "CARNIVORE.B"))
        self.assertEqual((carnivore.year, carnivore.month, carnivore.cover_date), (2023, None, None))
        self.assertEqual((carnivore.details.stores, carnivore.details.print_run), (["Carnivore Comics"], 1200))
        self.assertEqual([c.name for c in carnivore.details.credits if c.is_cover], ["Tiago Da Silva"])
        nycc = self.variant(series, "4NYCC")
        self.assertEqual((nycc.details.event, nycc.details.stores), ("New York Comic Con", []))
        rid = self.variant(series, "3RID")  # codes, while its sibling 3A uses sentences
        self.assertEqual([(c.name, c.uncertain) for c in rid.details.credits][-1], ("Sarah Myer", False))
        self.assertTrue(rid.details.synopsis.startswith("The gloves are off"))

    def test_a_page_without_variants(self) -> None:
        self.assertEqual(parse_series(NO_RESULTS.decode()).variants, [])


class DecodeTests(ComiTestCase):
    def test_utf8_with_stray_cp1252_bytes(self) -> None:
        cases = [
            ("café".encode(), "café"),
            (b"loss\x97on", "loss—on"),  # em dash
            (b"\x93quoted\x94 \x85", "“quoted” …"),
            ("ok — ".encode() + b"\x92", "ok — ’"),  # valid UTF-8 next to a stray byte
            (b"undefined \x81", "undefined �"),  # cp1252 leaves 0x81 undefined
        ]
        for body, text in cases:
            with self.subTest(body=body):
                self.assertEqual(decode_html(body), text)


class FetchSeriesTests(CatalogTestCase):
    def test_posts_with_user_agent_then_caches(self) -> None:
        with mock.patch(OPEN_URL, return_value=FakeResponse(BATMAN_SPAWN)) as open_url:
            series = fetch_series(57389539)
            again = fetch_series(57389539)
        self.assertEqual(len(series.variants), 6)
        self.assertEqual(again, series)
        self.assertEqual(open_url.call_count, 1)  # the second call is served from the cache
        self.assertIs(open_url.call_args.args[0], http._api_opener)  # redirects are refused
        request: Request = open_url.call_args.args[1]
        self.assertEqual(request.full_url, "https://www.mycomicshop.com/search?TID=57389539")
        self.assertEqual(request.get_method(), "POST")
        self.assertTrue(request.get_header("User-agent").startswith("Comi/"))
        self.assertTrue(mycomicshop.is_configured())

    def test_challenge_and_missing_series(self) -> None:
        blocked = "MyComicShop is refusing automated requests right now. Try again later."
        cases = [
            (FakeResponse(CHALLENGE), Unavailable, blocked),
            (FakeResponse(NO_RESULTS), NotFound, "MyComicShop has no series with the id 5."),
            (http_error(mycomicshop.series_url(5), 403), Unavailable, blocked),
            (http_error(mycomicshop.series_url(5), 404), NotFound, "MyComicShop has no series with the id 5."),
            (
                http_error(mycomicshop.series_url(5), 503),
                Unavailable,
                "MyComicShop is not working right now (error 503). Try again later.",
            ),
        ]
        for answer, error, message in cases:
            with self.subTest(error=error.__name__, message=message):
                key = "return_value" if isinstance(answer, FakeResponse) else "side_effect"
                with mock.patch(OPEN_URL, **{key: answer}), self.assertRaises(error) as raised:
                    fetch_series(5)
                self.assertEqual(raised.exception.message, message)
        self.assertIsNone(mycomicshop.cache.get("catalog:mycomicshop:series:5"))  # failures are not cached

    def test_too_large_page(self) -> None:
        with (
            mock.patch.object(http, "MAX_HTML_BYTES", 100),
            mock.patch(OPEN_URL, return_value=FakeResponse(BATMAN_SPAWN)),
            self.assertRaises(BadResponse),
        ):
            fetch_series(57389539)

    def test_throttled(self) -> None:
        with (
            mock.patch.object(mycomicshop, "REQUESTS_PER_MINUTE", 1),
            mock.patch(OPEN_URL, return_value=FakeResponse(TMNT)) as open_url,
        ):
            fetch_series(58451662)
            with self.assertRaises(RateLimited):
                fetch_series(58451663)
        self.assertEqual(open_url.call_count, 1)

    def test_invalid_id_is_not_requested(self) -> None:
        with mock.patch(OPEN_URL) as open_url, self.assertRaises(NotFound):
            fetch_series(0)
        open_url.assert_not_called()
