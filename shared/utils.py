"""
Shared utilities for SBO Bot
"""
from shared import config


def is_target_match(text: str) -> bool:
    """
    Check if text matches our target business using MATCH_KEYWORDS.
    
    Strategy:
    1. Full business name match → instant True
    2. EXCLUDE known competitors first (blacklist)
    3. Then check match keywords
    
    Configure in .env:
        MATCH_KEYWORDS=your business,اسم عملك
        EXCLUDE_KEYWORDS=competitor one,competitor two
    """
    text_lower = text.lower()
    
    # Check full business name first (strongest match)
    if config.BUSINESS_NAME.lower() in text_lower:
        return True
    
    # Exclude known competitors/wrong listings
    for exclude in config.EXCLUDE_KEYWORDS:
        if exclude in text_lower:
            return False
    
    # Check individual match keywords
    for keyword in config.MATCH_KEYWORDS:
        if keyword in text_lower:
            return True
    
    return False
