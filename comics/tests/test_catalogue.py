"""Edition rarity and the second batch of achievements (rules, triggers, seeds, cost)."""

import datetime
import json
import zoneinfo
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from comics.achievements import engine
from comics.achievements.display import showcase
from comics.achievements.registry import get_rule
from comics.forms import EditionForm, EditionManageForm
from comics.models import (
    Achievement,
    AchievementTier,
    Artist,
    CollectedIssue,
    Collection,
    Connecting,
    Edition,
    ReadingArc,
    Signature,
    UserAchievement,
)
from comics.roles import ADMINISTRATORS

from .base import ComiTestCase
from .factories import make_collection, make_edition, make_publishing, make_user

Rarity = Edition.RarityChoices
BUYING, SELLING = Collection.TradeChoices.BUYING, Collection.TradeChoices.SELLING
LOCAL = zoneinfo.ZoneInfo("America/Monterrey")
DAY = datetime.date(2021, 5, 1)


def progress(code: str, user: object) -> int:
    """`user`'s progress for the achievement `code`, through its rule (generic rules read the row)."""
    row = Achievement.objects.get(code=code)
    return get_rule(row.rule).progress_for(user, row)


def held(user: object, code: str) -> int:
    award = UserAchievement.objects.filter(user=user, achievement__code=code).first()
    return award.level if award else 0


def own(user: object, edition: Edition, **fields: object) -> Collection:
    return make_collection(collector=user, edition=edition, **fields)


def sell(user: object, piece: Collection, date: datetime.date = datetime.date(2022, 1, 1)) -> Collection:
    return make_collection(
        collector=user, edition=piece.edition, trade_type=SELLING, previous_trade=piece, trade_date=date
    )


def variants(count: int, *, number: str = "1", **fields: object) -> list[Edition]:
    """`count` editions (variants A, B, ...) of the same issue."""
    publishing = make_publishing()
    return [make_edition(publishing=publishing, number=number, variant=chr(65 + i), **fields) for i in range(count)]


class RarityFieldTests(ComiTestCase):
    def setUp(self) -> None:
        self.common = make_edition()
        self.legendary = make_edition(rarity=Rarity.LEGENDARY)

    def test_default_and_labels(self) -> None:
        self.assertEqual(self.common.rarity, Rarity.COMMON)
        self.assertEqual([label for _value, label in Rarity.choices], ["Common", "Rare", "Very rare", "Legendary"])
        self.assertIn("rarity", EditionManageForm.Meta.fields)
        self.assertIn("rarity", EditionForm.Meta.fields)

    def test_notable_rarity_is_rare_and_above(self) -> None:
        notable = {value: Edition(rarity=value).is_notable_rarity for value, _label in Rarity.choices}
        self.assertEqual(
            notable, {Rarity.COMMON: False, Rarity.RARE: True, Rarity.VERY_RARE: True, Rarity.LEGENDARY: True}
        )

    def test_gestion_edit_saves_the_rarity(self) -> None:
        self.client.force_login(make_user(role=ADMINISTRATORS))
        url = reverse("manage_edition_edit", args=[self.common.pk])
        self.assertContains(self.client.get(url), 'name="rarity"')
        edition = self.common
        data = {
            "publishing": edition.publishing_id,
            "number": edition.number,
            "variant": edition.variant,
            "printing": edition.printing,
            "format": edition.format,
            "rarity": Rarity.VERY_RARE,
            "release_date": edition.release_date.isoformat(),
            "cover_price": "0",
            "content": "single",
        }
        response = self.client.post(url, data)
        self.assertEqual(
            response.status_code, 302, getattr(response, "context", None) and response.context["form"].errors
        )
        self.assertEqual(Edition.objects.get(pk=edition.pk).rarity, Rarity.VERY_RARE)

    def test_lists_filter_by_rarity(self) -> None:
        for url_name, role in [("manage_editions", ADMINISTRATORS), ("comics_editions", None)]:
            with self.subTest(url_name=url_name):
                self.client.force_login(make_user(role=role))
                response = self.client.get(reverse(url_name), {"rarity": Rarity.LEGENDARY})
                self.assertEqual([edition.pk for edition in response.context["page_obj"]], [self.legendary.pk])
                self.assertContains(response, 'id="filter-rarity"')

    def test_badge_only_for_rare_and_above(self) -> None:
        self.client.force_login(make_user())
        html = self.client.get(reverse("comics_editions")).content.decode()
        self.assertIn('class="rarity-badge rarity-badge--legendary"', html)
        self.assertEqual(html.count("rarity-badge rarity-badge--"), 1)  # none for the common one
        detail = self.client.get(reverse("comics_edition_detail", args=[self.legendary.pk])).content.decode()
        self.assertIn("rarity-badge--legendary", detail)
        detail = self.client.get(reverse("comics_edition_detail", args=[self.common.pk])).content.decode()
        self.assertNotIn("rarity-badge--", detail)

    def test_badge_on_the_collection_page(self) -> None:
        user = make_user()
        own(user, self.legendary)
        self.client.force_login(user)
        response = self.client.get(reverse("comics"), {"letter": ""})
        self.assertContains(response, "rarity-badge--legendary")


class RuleTests(ComiTestCase):
    def setUp(self) -> None:
        self.user = make_user()

    # ----- infinity_gauntlet -----
    def test_infinity_gauntlet_counts_distinct_owned_great_rarities(self) -> None:
        stones = [make_edition(rarity=Rarity.VERY_RARE) for _ in range(3)] + [
            make_edition(rarity=Rarity.LEGENDARY) for _ in range(3)
        ]
        for edition in stones:
            own(self.user, edition)
        own(self.user, stones[0], trade_date=datetime.date(2020, 6, 1))  # a second copy counts once
        own(self.user, make_edition(rarity=Rarity.RARE))  # rare is not "great rarity"
        self.assertEqual(progress("infinity_gauntlet", self.user), 6)
        engine.evaluate(self.user, ["infinity_gauntlet"])
        self.assertEqual(held(self.user, "infinity_gauntlet"), 1)

    def test_infinity_gauntlet_ignores_sold_pieces(self) -> None:
        piece = own(self.user, make_edition(rarity=Rarity.LEGENDARY))
        self.assertEqual(progress("infinity_gauntlet", self.user), 1)
        sell(self.user, piece)
        self.assertEqual(progress("infinity_gauntlet", self.user), 0)

    # ----- multiverse -----
    def test_multiverse_counts_distinct_editions_of_one_issue(self) -> None:
        editions = variants(5)
        for edition in editions:
            own(self.user, edition)
        own(self.user, editions[0], trade_date=datetime.date(2020, 6, 1))  # duplicate copy
        for edition in variants(3, number="2"):
            own(self.user, edition)
        self.assertEqual(progress("multiverse", self.user), 5)

    def test_multiverse_needs_the_same_issue_and_owned_pieces(self) -> None:
        for number in ["1", "2", "3", "4", "5"]:
            own(self.user, make_edition(number=number))
        self.assertEqual(progress("multiverse", self.user), 1)
        pieces = [own(self.user, edition) for edition in variants(5, number="9")]
        self.assertEqual(progress("multiverse", self.user), 5)
        sell(self.user, pieces[0])
        self.assertEqual(progress("multiverse", self.user), 4)

    # ----- speed_force -----
    def test_speed_force_counts_purchases_on_the_busiest_day(self) -> None:
        pieces = [own(self.user, make_edition(), trade_date=DAY) for _ in range(9)]
        own(self.user, make_edition(), trade_date=DAY + datetime.timedelta(days=1))
        self.assertEqual(progress("speed_force", self.user), 9)
        own(self.user, make_edition(), trade_date=DAY)
        self.assertEqual(progress("speed_force", self.user), 10)
        # Selling them later does not undo the shopping spree; sales themselves are not purchases.
        sell(self.user, pieces[0], date=DAY)
        self.assertEqual(progress("speed_force", self.user), 10)
        engine.evaluate(self.user, ["speed_force"])
        self.assertEqual(held(self.user, "speed_force"), 1)

    # ----- batcave_vault -----
    def test_batcave_vault_counts_owned_signed_pieces(self) -> None:
        artist, other_artist = Artist.objects.create(name="Jim Lee"), Artist.objects.create(name="Todd McFarlane")
        signed = own(self.user, make_edition())
        Signature.objects.create(artist=artist, collectable=signed)
        Signature.objects.create(artist=other_artist, collectable=signed)  # one piece, two signatures
        own(self.user, make_edition())  # not signed
        self.assertEqual(progress("batcave_vault", self.user), 1)
        sold = own(self.user, make_edition())
        Signature.objects.create(artist=artist, collectable=sold)
        self.assertEqual(progress("batcave_vault", self.user), 2)
        sell(self.user, sold)
        self.assertEqual(progress("batcave_vault", self.user), 1)

    # ----- assemble -----
    def connecting(self, editions: list[Edition], name: str = "Wall") -> Connecting:
        wall = Connecting.objects.create(name=name, rows=1, columns=max(len(editions), 1))
        wall.set_pieces([(1, column, edition.pk) for column, edition in enumerate(editions, start=1)])
        return wall

    def test_assemble_counts_complete_connectings(self) -> None:
        editions = [make_edition() for _ in range(3)]
        self.connecting(editions)
        self.connecting([], name="Empty")  # no pieces: nothing to complete
        pieces = [own(self.user, edition) for edition in editions[:2]]
        self.assertEqual(progress("assemble", self.user), 0)  # a piece is missing
        own(self.user, editions[2])
        self.assertEqual(progress("assemble", self.user), 1)
        second = [make_edition() for _ in range(2)]
        self.connecting(second, name="Second")
        for edition in second:
            own(self.user, edition)
        self.assertEqual(progress("assemble", self.user), 2)
        sell(self.user, pieces[0])
        self.assertEqual(progress("assemble", self.user), 1)

    # ----- chronicler -----
    def test_chronicler_counts_complete_arcs_through_editions_and_compilations(self) -> None:
        issues = [make_edition(number=str(n)).issue for n in range(1, 4)]
        arc = ReadingArc.objects.create(name="Origins")
        arc.set_issues([issue.pk for issue in issues])
        ReadingArc.objects.create(name="Empty arc")
        own(self.user, make_edition(publishing=issues[0].publishing, number=issues[0].number, variant="Z"))
        own(self.user, make_edition(publishing=issues[1].publishing, number=issues[1].number, variant="Z"))
        self.assertEqual(progress("chronicler", self.user), 0)
        compilation = make_edition(number="100", format=Edition.FormatChoices.TRADE_PAPERBACK)
        CollectedIssue.objects.create(edition=compilation, issue=issues[2], order=1)
        own(self.user, compilation)
        self.assertEqual(progress("chronicler", self.user), 1)

    # ----- lazarus_pit -----
    def test_lazarus_pit_needs_a_purchase_after_a_sale_of_the_same_issue(self) -> None:
        first, other_cover = variants(2)
        piece = own(self.user, first, trade_date=datetime.date(2020, 1, 1))
        sell(self.user, piece, date=datetime.date(2020, 2, 1))
        self.assertEqual(progress("lazarus_pit", self.user), 0)
        own(self.user, other_cover, trade_date=datetime.date(2020, 2, 1))  # same day: no order
        self.assertEqual(progress("lazarus_pit", self.user), 0)
        own(self.user, make_edition(), trade_date=datetime.date(2020, 3, 1))  # another issue
        self.assertEqual(progress("lazarus_pit", self.user), 0)
        own(self.user, other_cover, trade_date=datetime.date(2020, 3, 1), amount=99)
        self.assertEqual(progress("lazarus_pit", self.user), 1)

    def test_lazarus_pit_ignores_purchases_before_the_sale(self) -> None:
        first, second = variants(2)
        own(self.user, second, trade_date=datetime.date(2019, 1, 1))
        piece = own(self.user, first, trade_date=datetime.date(2020, 1, 1))
        sell(self.user, piece, date=datetime.date(2020, 2, 1))
        self.assertEqual(progress("lazarus_pit", self.user), 0)

    # ----- halloween -----
    def test_halloween_uses_the_last_sign_in_in_the_project_time_zone(self) -> None:
        cases = [
            (datetime.datetime(2025, 10, 31, 0, 10, tzinfo=LOCAL), 1),
            (datetime.datetime(2025, 10, 31, 23, 30, tzinfo=LOCAL), 1),  # already Nov 1 in UTC
            (datetime.datetime(2025, 11, 1, 0, 30, tzinfo=LOCAL), 0),
            (datetime.datetime(2025, 10, 31, 3, 0, tzinfo=datetime.UTC), 0),  # still Oct 30 locally
            (datetime.datetime(2025, 10, 30, 12, 0, tzinfo=LOCAL), 0),
            (None, 0),
        ]
        for last_login, expected in cases:
            with self.subTest(last_login=last_login):
                self.user.last_login = last_login
                self.assertEqual(progress("halloween", self.user), expected)


class TriggerTests(ComiTestCase):
    """Fixtures inside self.committed() (see ComiTestCase)."""

    def setUp(self) -> None:
        with self.committed():
            self.user = make_user(username="hero", password="pass-1234")
            self.manager = make_user(role=ADMINISTRATORS)
            self.client.force_login(self.manager)  # signing in is an event of its own

    def test_rarity_change_reevaluates_the_owners(self) -> None:
        with self.committed():
            stones = [make_edition(rarity=Rarity.VERY_RARE) for _ in range(5)] + [make_edition()]
            for edition in stones:
                own(self.user, edition)
            make_user()  # owns nothing: must not be evaluated
        self.assertEqual(held(self.user, "infinity_gauntlet"), 0)

        edition = Edition.objects.get(pk=stones[-1].pk)
        with mock.patch.object(engine, "evaluate", wraps=engine.evaluate) as evaluate, self.committed():
            edition.rarity = Rarity.LEGENDARY
            edition.save()
        self.assertEqual(held(self.user, "infinity_gauntlet"), 1)
        self.assertEqual([call.args[0].pk for call in evaluate.call_args_list], [self.user.pk])

    def test_saving_without_a_rarity_change_or_creating_schedules_nothing(self) -> None:
        with self.committed():
            edition = make_edition()
            own(self.user, edition)
        edition = Edition.objects.get(pk=edition.pk)
        with self.captureOnCommitCallbacks() as callbacks:
            edition.notes = "Just a note"
            edition.save()
            make_edition(rarity=Rarity.LEGENDARY)
        self.assertEqual(callbacks, [])

    def edit_edition(self, edition: Edition, **content: str) -> object:
        """POST the Gestión edition form (as the manager signed in by setUp) keeping the edition's
        data, with a new content section."""
        data = {
            "publishing": edition.publishing_id,
            "number": edition.number,
            "variant": edition.variant,
            "printing": edition.printing,
            "format": edition.format,
            "rarity": edition.rarity,
            "release_date": edition.release_date.isoformat(),
            "cover_price": "0",
            **content,
        }
        return self.client.post(reverse("manage_edition_edit", args=[edition.pk]), data)

    def test_gestion_turning_an_owned_edition_into_a_compilation_completes_an_arc(self) -> None:
        with self.committed():
            arc_issue = make_edition().issue  # nobody owns it
            arc = ReadingArc.objects.create(name="Origins")
            arc.set_issues([arc_issue.pk])
            owned = make_edition(format=Edition.FormatChoices.TRADE_PAPERBACK)
            own(self.user, owned)
        # An edition without issue (Gestión flags them "No issue"): becoming a compilation changes
        # neither its issue nor deletes collected entries, so no signal tells its owners.
        Edition.objects.filter(pk=owned.pk).update(issue=None)
        owned = Edition.objects.get(pk=owned.pk)
        with self.committed():
            response = self.edit_edition(owned, content="compilation", collected=json.dumps([arc_issue.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(list(owned.collected_issues.all()), [arc_issue])
        self.assertEqual(held(self.user, "chronicler"), 1)

    def test_gestion_changing_what_an_owned_compilation_collects(self) -> None:
        # Its issue stays empty; replacing the collected issues deletes the old ones (signal).
        with self.committed():
            arc_issue = make_edition().issue
            arc = ReadingArc.objects.create(name="Origins")
            arc.set_issues([arc_issue.pk])
            compilation = make_edition(number="100", format=Edition.FormatChoices.TRADE_PAPERBACK)
            CollectedIssue.objects.create(edition=compilation, issue=make_edition().issue, order=1)
            compilation.sync_compilation_state()
            own(self.user, compilation)
        compilation = Edition.objects.get(pk=compilation.pk)
        self.assertIsNone(compilation.issue_id)
        self.assertEqual(held(self.user, "chronicler"), 0)
        with self.committed():
            response = self.edit_edition(compilation, content="compilation", collected=json.dumps([arc_issue.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(list(compilation.collected_issues.all()), [arc_issue])
        self.assertEqual(held(self.user, "chronicler"), 1)

    def test_gestion_moving_an_owned_edition_to_another_issue_reevaluates_its_owners(self) -> None:
        with self.committed():
            covers = variants(4)
            for edition in covers:
                own(self.user, edition)
            foreign = make_edition(publishing=make_publishing(), number="1", variant="Z")  # its own issue
            own(self.user, foreign)
        self.assertEqual(held(self.user, "multiverse"), 0)
        with self.committed():
            response = self.edit_edition(foreign, content="single", issue=str(covers[0].issue_id))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Edition.objects.get(pk=foreign.pk).issue_id, covers[0].issue_id)
        self.assertEqual(held(self.user, "multiverse"), 1)

    def test_gestion_edit_without_content_change_schedules_nobody(self) -> None:
        with self.committed():
            edition = make_edition()
            own(self.user, edition)
        with mock.patch.object(engine, "evaluate") as evaluate, self.committed():
            self.edit_edition(edition, content="single")
        evaluate.assert_not_called()

    def test_gestion_connecting_editor_completes_assemble(self) -> None:
        with self.committed():
            editions = [make_edition() for _ in range(2)]
            for edition in editions:
                own(self.user, edition)
        self.client.force_login(self.manager)
        payload = {
            "name": "Wall",
            "rows": 1,
            "columns": 2,
            "notes": "",
            "pieces": [{"row": 1, "column": n, "edition": edition.pk} for n, edition in enumerate(editions, start=1)],
        }
        with self.committed():
            response = self.client.post(
                reverse("manage_connecting_new"), json.dumps(payload), content_type="application/json"
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(held(self.user, "assemble"), 1)

    def test_removing_the_missing_piece_completes_a_connecting(self) -> None:
        with self.committed():
            editions = [make_edition() for _ in range(3)]
            wall = Connecting.objects.create(name="Wall", rows=1, columns=3)
            wall.set_pieces([(1, column, edition.pk) for column, edition in enumerate(editions, start=1)])
            own(self.user, editions[0])
            own(self.user, editions[1])
        self.assertEqual(held(self.user, "assemble"), 0)
        with self.committed():
            wall.pieces.get(edition=editions[2]).delete()  # like the admin inline
        self.assertEqual(held(self.user, "assemble"), 1)

    def test_gestion_reading_arc_form_completes_chronicler(self) -> None:
        with self.committed():
            issue = make_edition().issue
            own(self.user, issue.editions.first())
        self.client.force_login(self.manager)
        with self.committed():
            response = self.client.post(
                reverse("manage_arc_new"), {"name": "Origins", "issues": json.dumps([issue.pk])}
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(held(self.user, "chronicler"), 1)

    def test_compilation_contents_count_for_reading_arcs(self) -> None:
        with self.committed():
            issue = make_edition().issue
            arc = ReadingArc.objects.create(name="Origins")
            arc.set_issues([issue.pk])
            compilation = make_edition(number="100", format=Edition.FormatChoices.TRADE_PAPERBACK)
            own(self.user, compilation)
        self.assertEqual(held(self.user, "chronicler"), 0)
        with self.committed():
            CollectedIssue.objects.create(edition=compilation, issue=issue, order=1)
        self.assertEqual(held(self.user, "chronicler"), 1)

    def test_signatures_trigger_batcave_vault(self) -> None:
        with self.committed():
            piece = own(self.user, make_edition())
        with self.committed():
            Signature.objects.create(artist=Artist.objects.create(name="Frank Miller"), collectable=piece)
        self.assertEqual(held(self.user, "batcave_vault"), 1)

    def test_signing_in_on_halloween(self) -> None:
        halloween_night = datetime.datetime(2025, 10, 31, 22, 0, tzinfo=LOCAL)
        with mock.patch("django.utils.timezone.now", return_value=halloween_night), self.committed():
            self.assertTrue(self.client.login(username="hero", password="pass-1234"))
        self.assertEqual(held(self.user, "halloween"), 1)

    def test_collection_saves_evaluate_the_new_rules(self) -> None:
        with self.committed():
            for _ in range(10):
                own(self.user, make_edition(), trade_date=DAY)
        self.assertEqual(held(self.user, "speed_force"), 1)


class CatalogueTests(ComiTestCase):
    def test_every_rule_has_a_row_and_every_row_a_rule(self) -> None:
        self.client.force_login(make_user(role=ADMINISTRATORS))
        response = self.client.get(reverse("manage_achievements"))
        self.assertEqual(response.context["rules_without_row"], [])
        rows = response.context["achievements"]
        self.assertEqual(len(rows), 12)
        self.assertTrue(all(row.rule_info is not None and not row.missing_tiers for row in rows))
        self.assertNotContains(response, "No rule in code")

    def test_seeded_levels_and_secrets(self) -> None:
        tiers = {
            achievement.code: list(achievement.tiers.values_list("threshold", flat=True))
            for achievement in Achievement.objects.all()
        }
        self.assertEqual(tiers["infinity_gauntlet"], [6])
        self.assertEqual(tiers["multiverse"], [5])
        self.assertEqual(tiers["speed_force"], [10, 25, 50])
        self.assertEqual(tiers["batcave_vault"], [1, 5, 15])
        self.assertEqual(tiers["assemble"], [1, 3, 5])
        self.assertEqual(tiers["chronicler"], [1, 3, 5])
        secrets = set(Achievement.objects.filter(is_secret=True).values_list("code", flat=True))
        self.assertEqual(secrets, {"secret_identity", "lazarus_pit", "halloween"})

    def test_retroactive_command_awards_the_new_rules(self) -> None:
        user = make_user()
        for edition in variants(5):
            own(user, edition, trade_date=DAY)
        for _ in range(5):
            own(user, make_edition(), trade_date=DAY)
        call_command("award_achievements", stdout=StringIO(), stderr=StringIO())
        levels = dict(UserAchievement.objects.filter(user=user).values_list("achievement__code", "level"))
        self.assertEqual(levels, {"origin": 1, "multiverse": 1, "speed_force": 1})
        self.assertTrue(all(UserAchievement.objects.filter(user=user).values_list("bulk_awarded", flat=True)))

    def test_showcase_cost_does_not_grow_with_the_collection(self) -> None:
        owner = make_user()
        AchievementTier.objects.filter(achievement__code="great_power").update(threshold=1000)

        def queries() -> int:
            with CaptureQueriesContext(connection) as captured:
                showcase(owner, include_locked=True)
            return len(captured)

        own(owner, make_edition())
        small = queries()
        for _ in range(30):
            own(owner, make_edition(rarity=Rarity.LEGENDARY))
        self.assertEqual(queries(), small)
        self.assertLessEqual(small, 15)

    def test_evaluating_everything_is_a_few_queries(self) -> None:
        owner = make_user()
        for _ in range(30):
            own(owner, make_edition(), trade_date=DAY)
        with CaptureQueriesContext(connection) as captured:
            engine.evaluate(owner)
        evaluate_all = len(captured)
        self.assertLessEqual(evaluate_all, 30)
        with CaptureQueriesContext(connection) as captured:
            engine.evaluate(owner)
        self.assertLessEqual(len(captured), 15)  # nothing new to award: only the rule queries

    def test_now_is_not_used_by_halloween(self) -> None:
        # The rule reads last_login, so a nightly retroactive run on Nov 1 still sees Oct 31 sign-ins.
        user = make_user(last_login=datetime.datetime(2025, 10, 31, 20, 0, tzinfo=LOCAL))
        with mock.patch.object(timezone, "now", return_value=datetime.datetime(2025, 11, 1, 4, 0, tzinfo=LOCAL)):
            call_command("award_achievements", "--code", "halloween", stdout=StringIO(), stderr=StringIO())
        self.assertEqual(held(user, "halloween"), 1)
