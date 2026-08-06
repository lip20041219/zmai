"""Tests for CSV Processor — one test will fail until the bug is fixed."""

from csv_processor import parse_line, parse_csv, total_sales, filter_by


def test_simple_fields():
    assert parse_line("a,b,c") == ["a", "b", "c"]


def test_quoted_field_with_comma():
    """This test FAILS until parse_line() is fixed."""
    result = parse_line('"a,b",c')
    assert result == ["a,b", "c"], f"Expected ['a,b', 'c'], got {result}"


def test_parse_csv():
    data = "name,value\nx,1\ny,2"
    rows = parse_csv(data)
    assert len(rows) == 3
    assert rows[0] == ["name", "value"]


def test_total_sales():
    data = "product,price\napple,1.50\nbanana,2.00"
    assert total_sales(data) == 3.5


def test_total_sales_with_quoted_name():
    """Commas in quoted fields should not break column calculation."""
    data = 'product,price\n"Delicious, Apple",1.50\nBanana,2.00'
    assert total_sales(data) == 3.5


def test_filter_by():
    data = "name,city\nAlice,NYC\nBob,LA"
    result = filter_by(data, 1, "LA")
    assert len(result) == 1
    assert result[0][0] == "Bob"


def test_empty_line():
    assert parse_line("") == [""]
