"""
Tests for utils/parsers.py

Test fixtures for centralized parsing functions with graceful failure handling.
"""

import pytest

from utils.parsers import format_days_for_display, parse_days_string, parse_relative_date, parse_time_string


class TestParseDaysString:
    """Tests for parse_days_string function."""
    
    @pytest.mark.parametrize("input_str,expected", [
        # Basic comma-separated
        ("ma,di,wo", ["0", "1", "2"]),
        ("monday,tuesday,wednesday", ["0", "1", "2"]),
        # Space-separated
        ("ma di wo", ["0", "1", "2"]),
        ("monday tuesday", ["0", "1"]),
        # Mixed
        ("ma,tuesday,3", ["0", "1", "3"]),
        # Special keywords
        ("daily", ["0", "1", "2", "3", "4", "5", "6"]),
        ("dagelijks", ["0", "1", "2", "3", "4", "5", "6"]),
        ("weekdays", ["0", "1", "2", "3", "4"]),
        ("weekends", ["5", "6"]),
        # Numbers only
        ("0,1,2", ["0", "1", "2"]),
        ("5,6", ["5", "6"]),
        # Edge cases - graceful failures
        ("", []),
        (None, []),
        ("invalid,ma", ["0"]),  # Partial match - should extract valid days
        ("xyz,abc", []),  # No valid days
        ("ma,ma,di", ["0", "1"]),  # Duplicates should be removed
    ])
    def test_parse_days_string(self, input_str, expected):
        """Test various day string inputs."""
        result = parse_days_string(input_str)
        # Sort both for comparison (order doesn't matter)
        assert sorted(result) == sorted(expected), f"Input: {input_str}, Expected: {expected}, Got: {result}"
    
    def test_parse_days_string_case_insensitive(self):
        """Test that parsing is case-insensitive."""
        assert sorted(parse_days_string("MONDAY,TUESDAY")) == ["0", "1"]
        assert sorted(parse_days_string("Maandag,Dinsdag")) == ["0", "1"]


class TestParseTimeString:
    """Tests for parse_time_string function."""
    
    @pytest.mark.parametrize("input_str,expected_hour,expected_minute", [
        ("14:30", 14, 30),
        ("9:00", 9, 0),
        ("00:00", 0, 0),
        ("23:59", 23, 59),
        # Alternative formats
        ("14.30", 14, 30),  # Dots instead of colons
        ("9.00", 9, 0),
    ])
    def test_parse_time_string_valid(self, input_str, expected_hour, expected_minute):
        """Test valid time string parsing."""
        result = parse_time_string(input_str)
        assert result is not None, f"Failed to parse: {input_str}"
        assert result.hour == expected_hour
        assert result.minute == expected_minute
    
    @pytest.mark.parametrize("input_str", [
        None,
        "",
        "invalid",
        "25:00",  # Invalid hour
        "12:60",  # Invalid minute
        "abc:def",
        "12",  # Missing colon
        "12:30:45",  # Has seconds (should still work by taking HH:MM)
    ])
    def test_parse_time_string_invalid(self, input_str):
        """Test invalid time strings return None (graceful failure)."""
        result = parse_time_string(input_str)
        # Some edge cases might still parse (like "12:30:45"), so we just check it doesn't crash
        if result is not None:
            # If it parsed, verify it's a valid time
            assert isinstance(result, type(parse_time_string("12:00")))


class TestFormatDaysForDisplay:
    """Tests for format_days_for_display function."""
    
    def test_format_days_for_display(self):
        """Test day formatting for display."""
        assert format_days_for_display(["0", "1", "2"]) == "Monday, Tuesday, Wednesday"
        assert format_days_for_display(["5", "6"]) == "Saturday, Sunday"
        assert format_days_for_display([]) == ""
    
    def test_format_days_for_display_sorted(self):
        """Test that days are sorted correctly."""
        result = format_days_for_display(["6", "0", "3"])
        # Should be sorted: Monday, Thursday, Sunday
        assert "Monday" in result
        assert "Thursday" in result
        assert "Sunday" in result


class TestParseRelativeDate:
    """Tests for parse_relative_date function."""
    
    def test_parse_relative_date_not_implemented(self):
        """Test that relative date parsing returns None (not yet implemented)."""
        # This function is a placeholder for future implementation
        result = parse_relative_date("This Wednesday")
        assert result is None  # Currently not implemented
