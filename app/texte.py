"""Conversion HTML -> texte brut pour les fiches et les mails."""

import re
from html import unescape


def html_vers_texte(html: str) -> str:
    html = unescape(html)  # Greenhouse renvoie du HTML lui-même échappé (&lt;p&gt;)
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</h\d>|</tr>", "\n", html)
    html = re.sub(r"(?i)<li[^>]*>", "\n- ", html)
    texte = unescape(re.sub(r"<[^>]+>", " ", html)).replace("\xa0", " ")
    texte = re.sub(r"[ \t]+", " ", texte)
    return re.sub(r"\n\s*\n+", "\n\n", texte).strip()
