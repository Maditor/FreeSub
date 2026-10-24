"""Per-language filter for lines Whisper "hears" in music, noise or silence (from Japo)."""
import re

# Punctuation / spaces ignored when comparing phrases (text is also lower-cased)
_PUNCT = set("。、．，.,!！?？…‥・ー〜～ 　「」『』()（）\"'-~:;：；《》“”‘’")


FILLERS = set("嗯哦唉噢呃诶欸啊哈呀嘿哼哎呜呼うんえあおはふへ음응어아")
STUTTER = set("你我他她好是不这那")          # "你你", "好好好": a stutter heard alone, not a line
_TITLE = re.compile(r"\s*《[^》]{1,30}》[\s。.!！]*")


def norm(t: str) -> str:
    return "".join(c for c in (t or "").lower() if c not in _PUNCT)


FILTERS = {
    "ja": dict(
        suspicious=["ありがとうございました", "ありがとうございます", "ありがとう", "ご視聴ありがとうございました",
                    "おやすみなさい", "お疲れ様でした", "お疲れ様です", "ごちそうさまでした",
                    "チャンネル登録お願いします", "チャンネル登録よろしくお願いします", "字幕", "では",
                    "また会いましょう", "またね", "バイバイ", "さようなら"],
        interjection=r"^[あぁいぃうぅえぇおぉんンっッはハぁァアイウエオふフひヒー〜～…‥・。、!！?？♡♥\s]+$",
        real_short=["はい", "うん", "ううん", "いい", "いや", "ええ", "おい", "いえ", "あい", "いいえ",
                    "はいはい", "うんうん", "ええっ", "えっ", "へえ", "ほう", "おお"],
        repeatable=["イク", "いく", "ダメ", "だめ", "やめて", "もっと", "すごい", "待って", "やだ", "いや", "好き"],
        joiner="、",
    ),
    "en": dict(
        suspicious=["thank you", "thanks", "thanks for watching", "thank you for watching",
                    "thank you so much for watching", "thank you very much", "please subscribe",
                    "like and subscribe", "please like and subscribe", "subscribe to my channel",
                    "don't forget to subscribe", "see you next time", "see you in the next video",
                    "i'll see you in the next video", "bye", "bye bye", "goodbye", "you", "the end",
                    "so", "okay", "oh"],
        interjection=r"^(?:(?:a+h*|o+h*|u+h+|u+m+|h+m+|m+h*|h+a+|a+h+a+|o+o+h*|h+u+h+|e+h+|mm+|nn+)"
                     r"[\s,.!?…~\-]*)+$",
        real_short=["huh", "oh no", "ah ha", "aha"],
        repeatable=["yes", "no", "more", "please", "stop", "wait", "come on", "oh my god"],
        joiner=", ",
    ),
    "ko": dict(
        suspicious=["감사합니다", "고맙습니다", "시청해주셔서 감사합니다", "시청해 주셔서 감사합니다",
                    "구독과 좋아요 부탁드립니다", "구독 부탁드립니다", "좋아요와 구독", "구독", "좋아요",
                    "다음 영상에서 만나요", "안녕히 계세요", "안녕히 가세요", "자막", "끝", "바이바이"],
        interjection=r"^[아어오우으음응흐하헉흑앗읏힝앙흥후히ㅎㅋ~…!?.,\s]+$",
        real_short=["응", "네", "예", "어", "아니", "음 네", "그래"],
        repeatable=["더", "싫어", "안 돼", "안돼", "잠깐", "좋아", "제발", "아파", "그만", "빨리"],
        joiner=", ",
    ),
    "zh": dict(
        suspicious=["谢谢观看", "谢谢收看", "感谢观看", "谢谢大家", "谢谢", "谢谢观赏", "请订阅", "订阅",
                    "点赞", "请点赞订阅", "字幕", "中文字幕", "再见", "拜拜", "下期再见", "我们下期再见",
                    "谢谢观看下次见", "感谢观看下次见", "下次见", "感谢收看", "订阅订阅", "感谢观看下期再见"],
        interjection=r"^[啊嗯哦喔呃唔哈呀嘿哼噢哎诶欸嘤呜呼~…！？!?。，,\s]+$",
        real_short=["嗯", "嗯嗯", "哦", "好", "对", "是", "诶"],
        repeatable=["不要", "快", "快点", "等等", "等一下", "还要", "停", "好", "再来"],
        joiner="，",
    ),
    "ru": dict(
        suspicious=["спасибо за просмотр", "спасибо", "продолжение следует", "подписывайтесь на канал",
                    "ставьте лайки", "до свидания", "пока", "до новых встреч", "всем пока"],
        interjection=r"^(?:(?:а+х*|о+х*|у+х*|э+|м+|х+а+|а+а+|ох|ах|ух|ммм*|н+)[\s,.!?…~\-]*)+$",
        real_short=["да", "нет", "ну", "ага", "угу"],
        repeatable=["да", "нет", "ещё", "еще", "быстрее", "стой", "подожди", "пожалуйста"],
        joiner=", ",
    ),
    "vi": dict(
        suspicious=["cảm ơn các bạn đã theo dõi", "cảm ơn các bạn đã xem", "hãy đăng ký kênh",
                    "đăng ký kênh", "hẹn gặp lại các bạn", "hẹn gặp lại", "cảm ơn", "xin chào"],
        interjection=r"^(?:(?:a+|á+|à+|ơ+|ờ+|ừ+|ư+|ừm+|hừ+|ha+|há+|hả|ô+|ồ+|ui+|ối|ủa)[\s,.!?…~\-]*)+$",
        real_short=["ừ", "dạ", "vâng", "ờ", "ừm", "hả", "ủa"],
        repeatable=["không", "đi", "nhanh lên", "dừng lại", "đợi đã", "làm ơn"],
        joiner=", ",
    ),
}
# Credits / ads Whisper copies from its training data: never real dialogue in any language
ALWAYS_DROP = ["amara.org", "castingwords", "字幕由", "明镜", "点点栏目", "mbc 뉴스", "字幕提供",
               "字幕志愿者", "请不吝点赞", "打赏支持", "订阅转发", "点赞订阅",
               "字幕製作", "subtitles by", "transcribed by", "translated by", "subtitled by",
               "dimatorzok", "субтитры", "редактор субтитров", "корректор", "phụ đề bởi", "ghiền mì gõ"]

for _f in FILTERS.values():
    _f["suspicious"] = {norm(x) for x in _f["suspicious"]}
    _f["real_short"] = {norm(x) for x in _f["real_short"]}
    _f["repeatable"] = sorted(((norm(x), x) for x in _f["repeatable"]), key=lambda p: -len(p[0]))
    _f["interjection"] = re.compile(_f["interjection"], re.I)


# Gender / voice labels Whisper sometimes writes at the start of a line, e.g. "(nam) ...", "(Nữ): ...",
# "(男性)", "[Female]", "(male voice)". They describe the voice, they are not speech, so they are removed.
_TAG_WORDS = (r"(?:giọng\s+)?(?:nam|nữ)(?:\s+giới)?|đàn\s+ông|phụ\s+nữ|"
              r"(?:male|female|man|woman|boy|girl)(?:\s+(?:voice|speaker))?|"
              r"男性?|女性?|男の人|女の人|男声|女声|男声音|女声音|"
              r"남자|여자|남성|여성|"
              r"мужчина|женщина|мужской\s+голос|женский\s+голос")
_LEAD_TAG = re.compile(
    r"(^|\n)[ \t]*(?:[\(\[（【「『][ \t]*(?:" + _TAG_WORDS + r")[ \t]*[\)\]）】」』][ \t]*[:：]?[ \t]*)+",
    re.I)


# Dialogue dashes Whisper copies from subtitle files ("- 啊!- 啊!", "- Ăn được không?"): at the start of a line or
# right after the end of a sentence. A dash between words or numbers (3-5, well-known) is kept.
_DASH = re.compile(r"(^|(?<=[!！?？。.,，、…;；:：]))\s*[-–—]+\s*")


def strip_dashes(text: str) -> str:
    if not text:
        return text
    return "\n".join(_DASH.sub("", line).strip() for line in text.split("\n")).strip()


def strip_gender_tags(text: str) -> str:
    """'(nam) Xin chào' -> 'Xin chào'. Only labels at the start of a line are removed."""
    if not text:
        return text
    return _LEAD_TAG.sub(r"\1", text).strip()


def filters(lang: str) -> dict:
    return FILTERS.get(lang, FILTERS["ja"] if lang in ("ja", "zh", "ko") else FILTERS["en"])


def collapse_repeats(text: str, lang: str = "ja"):
    """'イクイクイクイク' -> 'イク、イク、イク…'. None if the text is not such a repeat."""
    f = filters(lang)
    n = norm(text)
    for w, shown in f["repeatable"]:
        if w and len(n) >= 2 * len(w) and n == w * (len(n) // len(w)):
            k = len(n) // len(w)
            return f["joiner"].join([shown] * min(k, 3)) + ("…" if k > 3 else "")
    return None


def keep_segment(text, avg_logprob, no_speech_prob, compression_ratio, lang="ja",
                 drop_interjections=True) -> bool:
    """Decide whether a transcribed segment is real speech."""
    return drop_reason(text, avg_logprob, no_speech_prob, compression_ratio, lang, drop_interjections) is None


def drop_reason(text, avg_logprob, no_speech_prob, compression_ratio, lang="ja", drop_interjections=True,
                sure_speech=False):
    """None = real speech, else why the segment is dropped (written in the 'heard' log). sure_speech: a piece
    we already know has a voice in it (the second listen has its own, looser bar): the confidence rules are
    skipped, the made-up-line rules stay."""
    t = strip_gender_tags((text or "").strip())
    n = norm(t)
    if not n:
        return "empty"
    f = filters(lang)
    low = t.lower()
    if any(x in low for x in ALWAYS_DROP):
        return "credits / 'thanks for watching' kind of line"
    if _TITLE.fullmatch(t):
        return "only a title in 《》 (a show name Whisper made up)"
    # 1) lines that are only vocal sounds
    if drop_interjections and f["interjection"].match(t) and len(n) <= 12 and n not in f["real_short"]:
        return "only a vocal sound (ah, oh, hm...)"
    # 2) common hallucinations: kept only when Whisper is very sure
    repeated = any(n == p * k for p in f["suspicious"] for k in (2, 3, 4))
    if n in f["suspicious"] or repeated:
        if repeated or avg_logprob < -0.3 or no_speech_prob > 0.15:
            return "a line Whisper often makes up, and it was not sure"
    # 3) repetitive or low-confidence output (expressive repeats are allowed)
    if compression_ratio > 2.4 and not collapse_repeats(t, lang):
        return f"repeats itself (compression {compression_ratio:.2f} > 2.4)"
    if sure_speech:
        # the second listen hears short noises as fillers ("嗯，嗯", "你你", "好好好") or one unsure character:
        # not worth a subtitle line (or a narration reading)
        if len(n) >= 2 and len(set(n)) == 1 and n[0] in FILLERS | STUTTER:     # not 妈妈 / 熊熊 / 哥哥
            return "a filler (one sound repeated)"
        if len(n) == 1 and (n in FILLERS or avg_logprob < -1.0):
            return "a filler / one unsure character"
        return None
    if no_speech_prob > 0.6 and avg_logprob < -0.8:
        return "probably not speech and unsure (no_speech > 0.6, lp < -0.8)"
    # shouted or quick lines come out less sure: dropped only when very unsure, or unsure AND maybe not speech
    if avg_logprob < -1.6:
        return "very unsure (lp < -1.6)"
    if avg_logprob < -1.2 and no_speech_prob > 0.3:
        return "unsure and maybe not speech (lp < -1.2, no_speech > 0.3)"
    return None


def drop_loops(cues, max_same=2):
    """Whisper sometimes repeats one line many times in a row (a decoding loop): keep at most max_same."""
    out, run = [], 0
    for c in cues:
        if out and norm(c.text) == norm(out[-1].text):
            run += 1
            if run >= max_same:
                out[-1].end = max(out[-1].end, min(c.end, out[-1].end + 2.0))
                continue
        else:
            run = 0
        out.append(c)
    return out
