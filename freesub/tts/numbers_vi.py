"""Numbers read the Vietnamese way by the narration: 18 -> "mười tám", 3.000 -> "ba nghìn", 2,5 -> "hai phẩy
năm", 50% -> "năm mươi phần trăm", 10:30 -> "mười giờ ba mươi", 18/10/2026 -> "mười tám tháng mười năm hai nghìn
không trăm hai mươi sáu", 0901 234 567 -> one digit at a time...

Only the spoken text changes; the subtitles keep the digits.
"""
import re

D = ["không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín"]
UNITS = ["", "nghìn", "triệu", "tỷ", "nghìn tỷ", "triệu tỷ"]


def _three(g: int, full: bool) -> list[str]:
    """0 - 999. full: not the first group (so 5 -> "không trăm lẻ năm")."""
    h, t, u = g // 100, g // 10 % 10, g % 10
    out = []
    if h or full:
        out += [D[h], "trăm"]
    if t == 0:
        if u:
            if h or full:
                out.append("lẻ")
            out.append(D[u])
    elif t == 1:
        out.append("mười")
        if u == 5:
            out.append("lăm")
        elif u:
            out.append(D[u])
    else:
        out += [D[t], "mươi"]
        if u == 1:
            out.append("mốt")
        elif u == 4:
            out.append("tư")
        elif u == 5:
            out.append("lăm")
        elif u:
            out.append(D[u])
    return out


def number(n: int) -> str:
    """A whole number in words: 1005 -> "một nghìn không trăm lẻ năm"."""
    if n == 0:
        return "không"
    if n < 0:
        return "âm " + number(-n)
    groups = []
    while n:
        groups.append(n % 1000)
        n //= 1000
    if len(groups) > len(UNITS):
        return " ".join(D[int(c)] for c in str(sum(g * 1000 ** i for i, g in enumerate(groups))))
    words = []
    top = len(groups) - 1
    for i in range(top, -1, -1):
        g = groups[i]
        if g == 0:
            continue
        words += _three(g, full=i < top)
        if UNITS[i]:
            words.append(UNITS[i])
    return " ".join(words)


def digits(s: str) -> str:
    return " ".join(D[int(c)] for c in s if c.isdigit())


def _decimal(whole: str, frac: str) -> str:
    """2,5 -> hai phẩy năm; 3,05 -> ba phẩy không năm."""
    zeros = len(frac) - len(frac.lstrip("0"))
    rest = frac.lstrip("0")
    tail = ["không"] * zeros + ([number(int(rest))] if rest else [])
    return f"{number(int(whole))} phẩy {' '.join(tail)}"


def _plain(tok: str) -> str:
    """One number as written: 3.000 / 1,500,000 / 2,5 / 3.14 / 0901234567 / 18."""
    m = re.fullmatch(r"(\d{1,3}(?:([.,])\d{3})+)(?:([.,])(\d+))?", tok)
    if m and (m.group(3) is None or m.group(3) != m.group(2)):      # 3.000 / 1.500.000 / 3.000,5
        whole = int(re.sub(r"[.,]", "", m.group(1)))
        if m.group(4):
            return _decimal(str(whole), m.group(4))
        return number(whole)
    m = re.fullmatch(r"(\d+)[.,](\d+)", tok)
    if m:
        return _decimal(m.group(1), m.group(2))
    if tok.isdigit():
        if (len(tok) > 1 and tok.startswith("0")) or len(tok) > 12:     # a phone number, a code: digit by digit
            return digits(tok)
        return number(int(tok))
    return tok


NUM = r"\d+(?:[.,]\d+)*"
_MEASURE = {"km": "ki lô mét", "kg": "ki lô gam", "cm": "xen ti mét", "mm": "mi li mét", "m2": "mét vuông",
            "ml": "mi li lít", "kw": "ki lô oát", "h": "giờ", "k": "nghìn", "tr": "triệu", "ty": "tỷ", "tỷ": "tỷ",
            "đ": "đồng", "vnd": "đồng", "vnđ": "đồng", "usd": "đô la", "$": "đô la", "€": "ơ rô", "%": "phần trăm"}
_ORDINAL = {"1": "nhất", "4": "tư"}


def _date(d, m, y=None):
    s = f"{number(int(d))} tháng {number(int(m))}"
    return s + (f" năm {number(int(y))}" if y else "")


def say(text: str) -> str:
    """Every number in a line as words."""
    if not re.search(r"\d", text):
        return text
    t = text
    # dates: 18/10/2026, 18-10-2026; "ngày 18/10"
    t = re.sub(r"(?<![\d/])(\d{1,2})[/-](\d{1,2})[/-](\d{4})(?![\d/])",
               lambda m: _date(*m.groups()) if 1 <= int(m.group(1)) <= 31 and 1 <= int(m.group(2)) <= 12
               else m.group(0), t)
    t = re.sub(r"(?i)\b(ngày|hôm)\s+(\d{1,2})/(\d{1,2})(?![\d/])",
               lambda m: f"{m.group(1)} {_date(m.group(2), m.group(3))}"
               if 1 <= int(m.group(2)) <= 31 and 1 <= int(m.group(3)) <= 12 else m.group(0), t)
    # times: 10:30, 7:05:20
    t = re.sub(r"(?<![\d:])(\d{1,2}):(\d{2})(?::(\d{2}))?(?![\d:])",
               lambda m: f"{number(int(m.group(1)))} giờ" +
               (f" {number(int(m.group(2)))}" if int(m.group(2)) else "") +
               (f" phút {number(int(m.group(3)))} giây" if m.group(3) else ""), t)
    # fractions: 1/2 -> một phần hai
    t = re.sub(r"(?<![\d/])(\d+)/(\d+)(?![\d/])", lambda m: f"{number(int(m.group(1)))} phần {number(int(m.group(2)))}", t)
    # ranges: 5-10 -> năm đến mười (a score, 3-2: ba hai)
    def _range(m):
        a, b = m.group(1), m.group(2)
        try:
            up = float(re.sub(r"[.,]", "", a)) < float(re.sub(r"[.,]", "", b))
        except ValueError:
            up = False
        return f"{_plain(a)} {'đến ' if up else ''}{_plain(b)}"
    t = re.sub(rf"(?<![\d.,])({NUM})\s?[-–]\s?({NUM})(?![\d])", _range, t)
    # money and measures written with the number: $5, 5$, 50k, 5tr, 20%, 3km
    t = re.sub(rf"\$\s?({NUM})", lambda m: f"{_plain(m.group(1))} đô la", t)
    t = re.sub(rf"(?<![\w.,])({NUM})\s?(km|kg|cm|mm|m2|ml|kw|k|tr|tỷ|ty|đ|vnđ|vnd|usd|\$|€|%)(?![\w])",
               lambda m: f"{_plain(m.group(1))} {_MEASURE[m.group(2).lower()]}", t, flags=re.IGNORECASE)
    # ordinals: thứ 1 -> thứ nhất, thứ 4 -> thứ tư
    t = re.sub(r"(?i)\b(thứ|hạng)\s+([14])\b", lambda m: f"{m.group(1)} {_ORDINAL[m.group(2)]}", t)
    # negative numbers at the start of a word: -5 -> âm năm
    t = re.sub(rf"(?<![\w\d])-({NUM})", lambda m: "âm " + _plain(m.group(1)), t)
    # everything else
    t = re.sub(NUM, lambda m: _plain(m.group(0)), t)
    return " ".join(t.split())
