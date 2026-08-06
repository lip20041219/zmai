"""Verify that .box width has a proper CSS unit."""
import re
import sys

css = open("project/style.css", encoding="utf-8").read()

# Find the .box rule
box_match = re.search(r'\.box\s*\{[^}]*\}', css, re.DOTALL)
if not box_match:
    print("FAIL: .box rule not found")
    sys.exit(1)

box_rule = box_match.group()

# Check if width has a proper unit
width_match = re.search(r'width\s*:\s*[0-9]+(?:px|%|em|rem|vw|vh)', box_rule)
if not width_match:
    print("FAIL: .box width missing proper CSS unit")
    sys.exit(1)

print(f"OK: .box width set to {width_match.group()}")
