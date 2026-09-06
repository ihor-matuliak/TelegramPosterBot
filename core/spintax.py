import re
import random
from typing import List


class SpintaxEngine:
    """
    Engine to parse Spintax templates and generate unique human-like message variations.
    Example: "{Привіт|Вітаю|Доброго дня}, {пропонуємо|маємо в наявності} {якісні|топові} товари!"
    """

    @staticmethod
    def parse(text: str) -> str:
        """Recursively process spintax patterns {opt1|opt2|opt3}."""
        if not text:
            return ""

        pattern = re.compile(r"\{([^{}]+)\}")
        while True:
            match = pattern.search(text)
            if not match:
                break
            options = match.group(1).split("|")
            chosen = random.choice(options)
            text = text[:match.start()] + chosen + text[match.end():]
        return text

    @staticmethod
    def inject_anti_fingerprint(text: str, enabled: bool = True) -> str:
        """
        Inject non-visible zero-width characters at random safe positions in text.
        This changes the cryptographic hash (MD5/SHA256) of every message sent to Telegram,
        preventing Telegram's neural anti-spam from identifying repeated bulk messages,
        while remaining completely invisible to humans.
        """
        if not enabled or not text:
            return text

        # Invisible unicode characters: Zero Width Space, Zero Width Non-Joiner, Zero Width Joiner
        invisible_chars = ["\u200B", "\u200C", "\u200D", "\uFEFF"]
        
        # Inject 1 to 3 invisible characters at the end of paragraphs or spaces
        parts = text.split("\n")
        modified_parts = []
        for p in parts:
            if p.strip():
                noise = "".join(random.choices(invisible_chars, k=random.randint(1, 3)))
                modified_parts.append(p + noise)
            else:
                modified_parts.append(p)
        
        return "\n".join(modified_parts)
