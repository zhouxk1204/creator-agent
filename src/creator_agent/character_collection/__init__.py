"""Character material collection (Doraemon V1.0).

Reads existing scene-split output (``scenes.json`` from scripts/split_scene.py),
extracts candidate frames from the ORIGINAL story video at the recorded
timestamps, filters black/solid/blurry/duplicate frames, builds paged contact
sheets for human review, and syncs human classification back into a manifest.

This package never modifies the scene-splitting pipeline; it only consumes its
output.
"""
