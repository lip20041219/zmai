"""CSV Processor — Parse and analyze CSV data.

Bug: parse_line() doesn't handle quoted fields containing commas.
Example: parse_line('"a,b",c') should return ['a,b', 'c'] but returns ['"a', 'b"', 'c'].
"""


def parse_line(line: str) -> list[str]:
    """Parse a single CSV line into fields.

    BUG: This simple split doesn't handle quoted fields with commas.
    """
    return line.split(",")


def parse_csv(text: str) -> list[list[str]]:
    """Parse full CSV text into rows."""
    lines = text.strip().splitlines()
    return [parse_line(line) for line in lines]


def total_sales(csv_text: str, column: int = 1) -> float:
    """Sum a numeric column from CSV data."""
    rows = parse_csv(csv_text)
    total = 0.0
    for row in rows[1:]:  # Skip header
        if len(row) > column and row[column].strip():
            total += float(row[column])
    return total


def filter_by(csv_text: str, column: int, value: str) -> list[list[str]]:
    """Filter rows where column matches value."""
    rows = parse_csv(csv_text)
    return [row for row in rows if len(row) > column and row[column] == value]
