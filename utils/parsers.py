"""
Parsing Utilities

Centralized string parsing for days, times, and dates to reduce duplicate
code across reminder and embed watcher modules. Provides graceful failure
handling for invalid input.
"""

import re
from datetime import datetime, time

# Complete day mapping for parsing
DAY_MAP = {
    # Dutch
    "ma": "0", "maandag": "0",
    "di": "1", "dinsdag": "1",
    "wo": "2", "woe": "2", "woensdag": "2",
    "do": "3", "donderdag": "3",
    "vr": "4", "vrijdag": "4",
    "za": "5", "zaterdag": "5",
    "zo": "6", "zondag": "6",
    # English
    "monday": "0", "mon": "0",
    "tuesday": "1", "tue": "1", "tues": "1",
    "wednesday": "2", "wed": "2",
    "thursday": "3", "thu": "3", "thur": "3",
    "friday": "4", "fri": "4",
    "saturday": "5", "sat": "5",
    "sunday": "6", "sun": "6",
}


def parse_days_string(days_input: str | None) -> list[str]:
    """
    Centralized days parsing with normalization.
    
    Parses day strings like "ma,di,wo", "monday,tuesday", "0,1,2", etc.
    Returns empty list on invalid input (graceful failure).
    
    Args:
        days_input: String containing day names or numbers (comma or space separated)
        
    Returns:
        List[str]: Normalized list of day numbers (0-6, where 0=Monday, 6=Sunday)
    """
    if not days_input or not isinstance(days_input, str):
        return []
    
    # Handle special cases
    days_val = days_input.lower().strip()
    days_val = re.sub(r"daily\s*:\s*", "", days_val).strip()
    
    # Check for special keywords
    if any(word in days_val for word in ["daily", "dagelijks"]):
        return ["0", "1", "2", "3", "4", "5", "6"]
    elif "weekdays" in days_val:
        return ["0", "1", "2", "3", "4"]
    elif "weekends" in days_val:
        return ["5", "6"]
    
    # Split by comma or whitespace
    parts = re.split(r",\s*|\s+", days_val)
    normalized = []
    
    for part in parts:
        if not part.strip():
            continue
        
        day_lower = part.lower().strip()
        
        # Check day map first
        if day_lower in DAY_MAP:
            normalized.append(DAY_MAP[day_lower])
        # Check if it's already a valid day number
        elif day_lower.isdigit() and 0 <= int(day_lower) <= 6:
            normalized.append(day_lower)
        else:
            # Try partial matching (e.g., "ma" matches "maandag")
            for key, value in DAY_MAP.items():
                if key.startswith(day_lower) or day_lower.startswith(key):
                    normalized.append(value)
                    break
            # If no match found, skip this part (graceful failure)
    
    # Remove duplicates and return
    return list(set(normalized))


def parse_time_string(time_str: str | None) -> time | None:
    """
    Centralized time parsing (HH:MM format).
    
    Parses time strings like "14:30", "9:00", etc.
    Returns None on invalid input (graceful failure).
    
    Args:
        time_str: Time string in HH:MM format
        
    Returns:
        Optional[time]: Parsed time object or None if invalid
    """
    if not time_str or not isinstance(time_str, str):
        return None
    
    # Clean up the string
    time_str = time_str.strip()
    
    # Replace dots with colons (e.g., "14.30" -> "14:30")
    time_str = time_str.replace(".", ":")
    
    try:
        return datetime.strptime(time_str, "%H:%M").time()
    except (ValueError, AttributeError):
        # Try alternative formats
        try:
            # Try HH:MM:SS and just take HH:MM
            if ":" in time_str and len(time_str.split(":")) >= 2:
                parts = time_str.split(":")
                return datetime.strptime(f"{parts[0]}:{parts[1]}", "%H:%M").time()
        except (ValueError, IndexError):
            pass
        return None


def parse_relative_date(text: str) -> str | None:
    """
    Parse relative dates like 'This Wednesday', 'Next Friday'.
    
    This is a placeholder for future implementation. Currently returns None.
    Future enhancement: Implement relative date parsing with dateutil or similar.
    
    Args:
        text: Text containing relative date expressions
        
    Returns:
        Optional[str]: Parsed date string or None if not found/not implemented
    """
    # TODO: Implement relative date parsing
    # This would require dateutil or similar library
    # For now, return None to indicate not implemented
    return None


def format_days_for_display(days_list: list[str]) -> str:
    """
    Convert day numbers to readable day names for display.
    
    Args:
        days_list: List of day numbers (0-6)
        
    Returns:
        str: Comma-separated day names
    """
    day_names_en = {
        "0": "Monday",
        "1": "Tuesday",
        "2": "Wednesday",
        "3": "Thursday",
        "4": "Friday",
        "5": "Saturday",
        "6": "Sunday",
    }

    day_names = [day_names_en.get(day, f"Day {day}") for day in days_list if day in day_names_en]
    return ", ".join(sorted(day_names, key=lambda x: list(day_names_en.values()).index(x) if x in day_names_en.values() else 999))
