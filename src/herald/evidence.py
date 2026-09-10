"""Conservative local evidence alignment; substantive character edits are forbidden."""
import re
import unicodedata


def _normalize(text):
    text = unicodedata.normalize('NFKC', text)
    return re.sub(r'\s+', '', text).translate(str.maketrans('“”「」『』‘’', '\"\"\"\"\"\"\'\''))


def quote_matches(quote: str, text: str) -> bool:
    quote, text = _normalize(quote), _normalize(text)
    # A punctuation-only quote carries no evidence.
    if not any(c.isalnum() for c in quote):
        return False
    # Local alignment permits punctuation edits only, with a bounded edit budget.
    pattern = ''.join(re.escape(c) if not unicodedata.category(c).startswith('P') else r'[^\w\s]*' for c in quote)
    patterns = [re.escape(quote)]
    if len(quote) >= 8:
        characters = [c for c in quote if not unicodedata.category(c).startswith('P')]
        patterns.append(r'[^\w\s]*'.join(re.escape(c) for c in characters))
    patterns.append(pattern)
    for expression in patterns:
        for match in re.finditer(expression, text):
            # Avoid extracting a positive statement from a directly negated phrase.
            if match.start() and text[match.start()-1] in '不未无非':
                continue
            matched = match.group()
            if abs(len(matched) - len(quote)) <= max(2, len(quote) // 10):
                return True
    return False
