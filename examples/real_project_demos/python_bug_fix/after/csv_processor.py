"""CSV Processor — Parse and analyze CSV data.

Fixed: parse_line() now handles quoted fields containing commas.
"""


def parse_line(line: str) -> list[str]:
    """Parse a single CSV line into fields.

    Handles quoted fields: '"a,b",c' → ['a,b', 'c']
    """
    fields = []
    current = []
    in_quotes = False
    for char in line:
        if char == '"':
            in_quotes = not in_quotes
        elif char == ',' and not in_quotes:
            fields.append(''.join(current))
            current = []
        else:
            current.append(char)
    fields.append(''.join(current))
    return fields


def parse_csv(text: str) -> list[list[str]]:
    """Parse full CSV text into rows."""
    lines = text.strip().splitlines()
    return [parse_line(line) for line in lines]


def total_sales(csv_text: str, column: int = 1) -> float:
    """Sum a numeric column from CSV data."""
    rows = parse_csv(csv_text)
    total = 0.0
    for row in rows[1:]:
        if len(row) > column and row[column].strip():
            total += float(row[column])
    return total


def filter_by(csv_text: str, column: int, value: str) -> list[list[str]]:
    """Filter rows where column matches value."""
    rows = parse_csv(csv_text)
    return [row for row in rows if len(row) > column and row[column] == value]
