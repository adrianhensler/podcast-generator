import re


def normalize_for_speech(text: str) -> str:
    """
    Safety-net normalization layer for TTS input.
    Removes/replaces markdown, symbols, and patterns that degrade TTS quality.
    """
    # Currency: $1,500 → "1500 dollars", $3.5M → "3.5 million dollars"
    text = re.sub(r'\$(\d[\d,]*\.?\d*)\s*[Tt]rillion', lambda m: f"{m.group(1).replace(',', '')} trillion dollars", text)
    text = re.sub(r'\$(\d[\d,]*\.?\d*)\s*[Bb]illion', lambda m: f"{m.group(1).replace(',', '')} billion dollars", text)
    text = re.sub(r'\$(\d[\d,]*\.?\d*)\s*[Mm]illion', lambda m: f"{m.group(1).replace(',', '')} million dollars", text)
    text = re.sub(r'\$(\d[\d,]*\.?\d*)\s*[Kk]', lambda m: f"{m.group(1).replace(',', '')} thousand dollars", text)
    text = re.sub(r'\$(\d[\d,]*)', lambda m: f"{m.group(1).replace(',', '')} dollars", text)

    # Percentages: 3.5% → "3.5 percent"
    text = re.sub(r'(\d+\.?\d*)\s*%', r'\1 percent', text)

    # Abbreviations
    text = re.sub(r'\be\.g\.\s*', 'for example ', text)
    text = re.sub(r'\bi\.e\.\s*', 'that is ', text)
    text = re.sub(r'\betc\.\s*', 'etcetera ', text)
    text = re.sub(r'\bvs\.\s*', 'versus ', text)
    text = re.sub(r'\bDr\.\s+', 'Doctor ', text)
    text = re.sub(r'\bMr\.\s+', 'Mister ', text)
    text = re.sub(r'\bMrs\.\s+', 'Missus ', text)
    text = re.sub(r'\bMs\.\s+', 'Miss ', text)
    text = re.sub(r'\bSt\.\s+', 'Saint ', text)

    # URLs
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'www\.\S+', '', text)

    # Bold/italic markdown: **text** or *text* or __text__ or _text_
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'__(.+?)__', r'\1', text)
    text = re.sub(r'_(.+?)_', r'\1', text)

    # Markdown headers
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)

    # Bullet points / list markers at line start
    text = re.sub(r'^\s*[-*•]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)

    # Em dash / en dash → comma space
    text = re.sub(r'[—–]', ', ', text)

    # Remaining problematic chars
    text = re.sub(r'[*#_>|`]', '', text)

    # Collapse multiple spaces/newlines
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ \t]{2,}', ' ', text)

    return text.strip()
