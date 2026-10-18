"""Translation styles: a short guide the AI translator follows (setting, how people address each other, tone,
fixed terms) for one kind of video.

FreeSub ships a set written for Vietnamese subtitles. The user can edit them, rename them, or add their own;
those changes are kept in Documents\\FreeSub Data\\translation_styles.json (the shipped text stays as it is,
so "Restore" brings it back).

In a guide, a line like   皇上 = Hoàng thượng   is a fixed term: the AI must use it, and with Google Translate
it is put in before the line is translated. Lines with → are only advice.
"""
import json
import os
import re
import threading
import time

from ..config import user_dir

FILE = "translation_styles.json"
DEFAULT = "general"
_lock = threading.Lock()

# ---------------------------------------------------------------------------------------------- shipped styles
_GENERAL = """Dịch tự nhiên như người Việt nói, ngắn gọn, dễ đọc kịp.
Xưng hô theo tuổi, quan hệ và cảm xúc của nhân vật; giữ nhất quán từ đầu đến cuối.
Tên riêng giữ như bản gốc (tên Trung, Hàn đọc theo cách quen dùng ở Việt Nam)."""

_HISTORICAL = """Bối cảnh: cung đình, triều đình Trung Hoa xưa. Lời thoại trang trọng, có tôn ti.

Xưng hô:
- 我 → ta (người trên, ngang hàng), tại hạ (khiêm tốn); 你 → ngươi; 他 → hắn; 她 → nàng
- Vua tự xưng Trẫm; quan tự xưng thần, hạ thần; vương gia tự xưng bổn vương; phi tần tự xưng thần thiếp, bổn cung
- Người dưới gọi người trên bằng chức danh, không dùng "bạn", "anh", "em"

Giọng văn: trang nghiêm, câu gọn; lời mắng nhiếc, châm chọc vẫn giữ kiểu nói xưa.
Tên người: phiên âm Hán-Việt (李明 → Lý Minh).
Tránh từ hiện đại: điện thoại, công ty, OK, sếp, anh ấy, cô ấy.

Thuật ngữ:
皇上 = Hoàng thượng
陛下 = Bệ hạ
太后 = Thái hậu
皇后 = Hoàng hậu
太子 = Thái tử
王爷 = Vương gia
公主 = Công chúa
丞相 = Thừa tướng
将军 = Tướng quân
大人 = Đại nhân
娘娘 = Nương nương
贵妃 = Quý phi
小姐 = Tiểu thư
公子 = Công tử
夫人 = Phu nhân
奴婢 = nô tì
微臣 = vi thần
遵命 = Tuân lệnh
告退 = Cáo lui
退下 = Lui xuống
来人 = Người đâu
圣旨 = Thánh chỉ
冷宫 = Lãnh cung"""

_WUXIA = """Bối cảnh: giang hồ võ hiệp, tu tiên. Lời thoại hào sảng, nhiều thuật ngữ Hán-Việt.

Xưng hô:
- 我 → ta, tại hạ; người tu đạo tự xưng bần đạo; 你 → ngươi, các hạ (lịch sự); 他 → hắn; 她 → nàng
- Trong môn phái: sư phụ, sư huynh, sư đệ, sư tỷ, sư muội, sư thúc, chưởng môn
- Người trẻ gọi người lớn: tiền bối; tự xưng: vãn bối

Giọng văn: khí phách, dứt khoát; chiêu thức đọc theo Hán-Việt, không dịch nghĩa.
Tên người, môn phái, công pháp: phiên âm Hán-Việt.

Thuật ngữ:
师父 = Sư phụ
师兄 = Sư huynh
师弟 = Sư đệ
师姐 = Sư tỷ
师妹 = Sư muội
掌门 = Chưởng môn
前辈 = Tiền bối
晚辈 = Vãn bối
大侠 = Đại hiệp
江湖 = giang hồ
武功 = võ công
内力 = nội lực
真气 = chân khí
轻功 = khinh công
经脉 = kinh mạch
丹田 = đan điền
走火入魔 = tẩu hỏa nhập ma
修炼 = tu luyện
灵力 = linh lực
渡劫 = độ kiếp
飞升 = phi thăng
仙尊 = Tiên Tôn
魔头 = ma đầu
宗门 = tông môn
法宝 = pháp bảo
阵法 = trận pháp
炼气 = Luyện Khí
筑基 = Trúc Cơ
金丹 = Kim Đan
元婴 = Nguyên Anh"""

_TIME_TRAVEL = """Bối cảnh: nhân vật hiện đại lạc về thời xưa (hoặc ngược lại).
Người thời xưa nói kiểu cổ trang (ta, ngươi, chức danh Hán-Việt).
Nhân vật xuyên không nói kiểu hiện đại (tôi, anh, cô, trời ơi, OK...); sự lệch giọng này là điểm hài, cứ giữ.
Khi nhân vật xuyên không tự nói một mình, dùng giọng thường ngày, có thể hơi lém.

Thuật ngữ:
皇上 = Hoàng thượng
王爷 = Vương gia
小姐 = Tiểu thư
公子 = Công tử
穿越 = xuyên không"""

_MODERN = """Bối cảnh: phim Trung Quốc hiện đại (gia đình, tình cảm, công sở).
Xưng hô theo tuổi và quan hệ: anh / em, tôi / cậu, con / bố mẹ, chú / cháu...; người yêu: anh / em.
Giọng văn: tự nhiên, đời thường; không dùng ta / ngươi / nàng.
Tên người: Hán-Việt (王浩 → Vương Hạo). Chức danh nói kiểu Việt: tổng giám đốc, trưởng phòng, sếp.

Thuật ngữ:
总裁 = Tổng tài
老板 = sếp
经理 = giám đốc"""

_ACTION = """Phim hành động, tội phạm, quân sự.
Câu ngắn, dứt khoát, đúng nhịp; mệnh lệnh nói gọn (Nằm xuống! Đi mau!).
Thuật ngữ quân sự, cảnh sát dịch theo cách Việt quen dùng (đội trưởng, sếp, mục tiêu, yểm trợ).
Chửi thề dịch vừa phải, giữ sắc thái nhưng không thô tục quá mức."""

_COMEDY = """Phim hài.
Ưu tiên cho người xem bật cười: chơi chữ, câu đùa đổi sang cách nói tương đương của người Việt.
Có thể dùng từ lóng nhẹ, khẩu ngữ (trời đất, thôi xong, ủa alo) khi hợp nhân vật.
Giữ nhịp câu đùa: câu chốt để cuối dòng."""

_HORROR = """Phim kinh dị, ma quái, tâm linh.
Giọng văn căng, lạnh, câu ngắn; tiếng thì thầm, la hét dịch đúng cảm xúc.
Thuật ngữ tâm linh dùng từ Việt quen thuộc: ma, quỷ, oan hồn, bùa, trừ tà, đạo sĩ, thầy pháp.

Thuật ngữ:
鬼 = ma
厉鬼 = lệ quỷ
僵尸 = cương thi
道士 = đạo sĩ
符 = bùa
阴间 = âm phủ"""

_ANIME = """Anime, hoạt hình Nhật Bản.
Tên người giữ theo phiên âm Latinh (Tanjiro, Nezuko), không dịch nghĩa.
Hậu tố -san, -kun, -chan, -sama, -senpai, -sensei: giữ nguyên khi là cách gọi đặc trưng; nếu không,
chuyển sang anh / chị / em / cậu cho tự nhiên.
Tên chiêu thức: giữ cách gọi quen thuộc của fan (Hơi thở của Nước...).
Giọng văn: sinh động, đúng tính cách nhân vật (sôi nổi, lạnh lùng, đáng yêu)."""

_KOREAN = """Phim Hàn Quốc.
Tên người theo cách quen dùng ở Việt Nam (Kim Ji-won, Lee Min-ho).
Xưng hô: oppa → anh; unnie / noona → chị; hyung → anh; sunbae → tiền bối hoặc anh / chị (tùy ngữ cảnh).
Kính ngữ thể hiện qua cách xưng hô (cháu / bác, em / anh, tôi / ngài) thay vì dịch từng chữ.
Giọng văn: tình cảm, tự nhiên; giữ sắc thái lịch sự hay thân mật của câu gốc."""

_WESTERN = """Phim Âu Mỹ.
Tên người, địa danh giữ nguyên tiếng Anh.
Xưng hô theo quan hệ: bạn bè tao / mày hoặc tôi / cậu; người lạ tôi / anh / cô; cấp trên sếp, ngài.
Thành ngữ, câu đùa: dịch theo nghĩa và cách nói tương đương, không dịch từng chữ.
Từ chửi thề dịch vừa phải."""

_DOCUMENTARY = """Phim tài liệu, review, kể chuyện.
Văn phong rõ ràng, mạch lạc, như người dẫn chuyện; dùng "chúng ta", "người xem" khi hợp.
Số liệu, đơn vị đổi theo cách viết Việt (1.000, 2,5 km).
Thuật ngữ chuyên ngành dùng từ chuẩn tiếng Việt; tên khoa học giữ nguyên."""

BUILTIN = [
    ("general", "Default", _GENERAL),
    ("historical", "Chinese historical drama", _HISTORICAL),
    ("wuxia", "Wuxia / xianxia", _WUXIA),
    ("time_travel", "Time travel", _TIME_TRAVEL),
    ("modern_cn", "Modern Chinese drama", _MODERN),
    ("action", "Action", _ACTION),
    ("comedy", "Comedy", _COMEDY),
    ("horror", "Horror", _HORROR),
    ("anime", "Anime", _ANIME),
    ("korean", "Korean drama", _KOREAN),
    ("western", "Western films", _WESTERN),
    ("documentary", "Documentary / review", _DOCUMENTARY),
]
_BUILTIN = {k: (n, t) for k, n, t in BUILTIN}
BUILTIN_NAMES = {k: n for k, n, _t in BUILTIN}


# ---------------------------------------------------------------------------------------------- the user's file
def _path() -> str:
    return os.path.join(user_dir(), FILE)


def _load() -> dict:
    try:
        with open(_path(), encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data.setdefault("edited", {})
            data.setdefault("custom", [])
            return data
    except (OSError, ValueError):
        pass
    return {"edited": {}, "custom": []}


def _save(data: dict):
    tmp = _path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _path())


def all_styles() -> list[dict]:
    """[{id, name, text, builtin, edited, renamed}] in order: shipped ones, then the user's."""
    with _lock:
        data = _load()
    out = []
    for key, name, text in BUILTIN:
        e = data["edited"].get(key, {})
        out.append({"id": key, "name": e.get("name") or name, "text": e.get("text", text), "builtin": True,
                    "edited": "text" in e, "renamed": bool(e.get("name"))})
    for c in data["custom"]:
        if isinstance(c, dict) and c.get("id"):
            out.append({"id": c["id"], "name": c.get("name") or "Style", "text": c.get("text", ""),
                        "builtin": False, "edited": False, "renamed": True})
    return out


def get(style_id: str) -> dict:
    styles = all_styles()
    return next((s for s in styles if s["id"] == style_id), styles[0])


def update(style_id: str, name: str | None = None, text: str | None = None):
    with _lock:
        data = _load()
        if style_id in _BUILTIN:
            e = data["edited"].setdefault(style_id, {})
            if name is not None:
                if name.strip() and name.strip() != _BUILTIN[style_id][0]:
                    e["name"] = name.strip()
                else:
                    e.pop("name", None)
            if text is not None:
                if text != _BUILTIN[style_id][1]:
                    e["text"] = text
                else:
                    e.pop("text", None)
            if not e:
                data["edited"].pop(style_id, None)
        else:
            for c in data["custom"]:
                if c.get("id") == style_id:
                    if name is not None and name.strip():
                        c["name"] = name.strip()
                    if text is not None:
                        c["text"] = text
        _save(data)


def add(name: str, text: str = "") -> str:
    with _lock:
        data = _load()
        sid = f"custom_{int(time.time() * 1000)}"
        data["custom"].append({"id": sid, "name": name, "text": text})
        _save(data)
    return sid


def remove(style_id: str):
    """A user style goes; a shipped one gets its own name and text back."""
    with _lock:
        data = _load()
        if style_id in _BUILTIN:
            data["edited"].pop(style_id, None)
        else:
            data["custom"] = [c for c in data["custom"] if c.get("id") != style_id]
        _save(data)


def shipped_text(style_id: str) -> str | None:
    return _BUILTIN[style_id][1] if style_id in _BUILTIN else None


# ---------------------------------------------------------------------------------------------- use
_CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯]")


def term_pairs(text: str) -> list[tuple[str, str]]:
    """'皇上 = Hoàng thượng' lines (Chinese / Japanese / Korean on the left): fixed terms, longest first."""
    out = []
    for line in (text or "").splitlines():
        m = re.match(r"^\s*[-•*]?\s*([^=\n]+?)\s*=\s*(.+?)\s*$", line)
        if m and _CJK.search(m.group(1)) and len(m.group(1)) >= 2:
            out.append((m.group(1), m.group(2)))
    return sorted(out, key=lambda p: -len(p[0]))
