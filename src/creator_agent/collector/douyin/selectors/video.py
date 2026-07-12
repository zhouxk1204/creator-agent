"""Video card DOM selectors.

Note: the collector now scrapes ``a[href*="/video/"]`` links directly (the grid
card carries no publish time), so these class-based selectors are currently
unused. Kept for reference in case card-level fields are needed again.
"""

VIDEO_CARD = 'div[class*="video-card"]'
TITLE = 'a[class*="title"]'
COVER = 'img[class*="cover"]'
VIEWS = 'span[class*="play-count"]'
LIKES = 'span[class*="digg-count"]'
COMMENTS = 'span[class*="comment-count"]'
PUBLISH_TIME = 'span[class*="publish-time"]'
VIDEO_LINK = 'a[class*="video-link"]'
