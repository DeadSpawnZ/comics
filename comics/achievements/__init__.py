"""Achievement engine.

- registry.py: what a rule is and the registry of rules (code -> how progress is measured).
- rules.py: the rules themselves. Adding an achievement = a rule here + a row seeded by a
  migration (name, texts, icon, tiers: all editable in Gestión afterwards) + its medal artwork
  in static/achievements/<code>.svg (a test fails without it).
- engine.py: evaluating rules for a user and awarding levels, triggered after commit.
- display.py: what profiles show (earned, locked with progress, secret).
- medals.py: how a medal looks (uploaded image, bundled artwork or icon; ring by level)."""
