"""Achievement engine.

- registry.py: what a rule is and the registry of rules (key -> how progress is measured).
- rules.py: the rules themselves. Adding a built-in achievement = a rule here (or a generic one)
  + a row seeded by a migration (name, texts, category, windows, tiers: all editable in Gestión
  afterwards). Its medal artwork goes in static/achievements/<code>.svg; until it exists the
  generic medal is shown (a test lists which codes have their own artwork).
- windows.py: the time windows windowed rules read.
- engine.py: evaluating rules for a user and awarding levels, triggered after commit.
- display.py: what profiles show (earned, locked with progress, secret).
- medals.py: how a medal looks (uploaded image, bundled artwork or the generic medal; ring by level).
- gestion.py: what Gestión may delete."""
