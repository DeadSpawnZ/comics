"""The app's models, one module per area, re-exported so `comics.models.X` keeps working everywhere.

Migrations and the rest of the project import from `comics.models` (e.g. the validators
comics.models.validate_phone, comics.models.validate_site_path and
comics.models.Publishing.max_value_current_year), so every public name stays available here.
Module dependencies run one way: base <- catalog <- collections and change_requests;
base <- profiles and achievements; achievements and change_requests <- notifications."""

# isort: off
# Same order as the models were defined in the former single module: Django registers models in
# import order, which is the order of apps.get_models() and so of dumpdata (backups).
from .base import GENERATED_CODE_PREFIX, PictureModel, localized, unique_code
from .catalog import (
    Artist,
    ArtistQuerySet,
    CollectedIssue,
    Edition,
    EditionCoverArtist,
    Editorial,
    EditorialAlias,
    EditorialAliasManager,
    EditorialQuerySet,
    ExternalIdentifier,
    Issue,
    IssueCreator,
    IssueQuerySet,
    Publishing,
    Title,
    current_year,
)
from .collections import (
    Article,
    Collectable,
    CollectableQuerySet,
    Collection,
    CollectionQuerySet,
    Connecting,
    ConnectingPiece,
    Dealer,
    ReadingArc,
    ReadingArcEntry,
    Signature,
    TradeQuerySet,
)
from .profiles import PHONE_ALLOWED, AreaAccess, Profile, ProfileManager, validate_phone
from .achievements import (
    Achievement,
    AchievementCategory,
    AchievementCategoryManager,
    AchievementManager,
    AchievementQuerySet,
    AchievementTier,
    AchievementTierManager,
    UserAchievement,
    UserAchievementManager,
)
from .change_requests import ChangeRequest, ChangeRequestQuerySet
from .notifications import Announcement, Notification, NotificationManager, NotificationQuerySet, validate_site_path
# isort: on

__all__ = [
    # base
    "GENERATED_CODE_PREFIX",
    "PictureModel",
    "localized",
    "unique_code",
    # catalog
    "Artist",
    "ArtistQuerySet",
    "CollectedIssue",
    "Edition",
    "EditionCoverArtist",
    "Editorial",
    "EditorialAlias",
    "EditorialAliasManager",
    "EditorialQuerySet",
    "ExternalIdentifier",
    "Issue",
    "IssueCreator",
    "IssueQuerySet",
    "Publishing",
    "Title",
    "current_year",
    # collections
    "Article",
    "Collectable",
    "CollectableQuerySet",
    "Collection",
    "CollectionQuerySet",
    "Connecting",
    "ConnectingPiece",
    "Dealer",
    "ReadingArc",
    "ReadingArcEntry",
    "Signature",
    "TradeQuerySet",
    # profiles
    "PHONE_ALLOWED",
    "AreaAccess",
    "Profile",
    "ProfileManager",
    "validate_phone",
    # achievements
    "Achievement",
    "AchievementCategory",
    "AchievementCategoryManager",
    "AchievementManager",
    "AchievementQuerySet",
    "AchievementTier",
    "AchievementTierManager",
    "UserAchievement",
    "UserAchievementManager",
    # change_requests
    "ChangeRequest",
    "ChangeRequestQuerySet",
    # notifications
    "Announcement",
    "Notification",
    "NotificationManager",
    "NotificationQuerySet",
    "validate_site_path",
]
