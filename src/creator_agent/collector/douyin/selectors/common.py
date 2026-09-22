"""Common DOM selectors (containers, loading, etc.).

Note: currently unused - the collector scrapes ``a[href*="/video/"]`` links
directly rather than via these container selectors. Kept for reference.
"""

VIDEO_LIST = 'div[class*="video-list"]'
LOADING = 'div[class*="loading"]'
SKELETON = 'div[class*="skeleton"]'
NO_MORE = 'div[class*="no-more"]'
SCROLL_TRIGGER = 'div[class*="scroll-trigger"]'
