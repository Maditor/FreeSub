# FreeSub

Tạo phụ đề, dịch và thuyết minh cho video. Miễn phí, mã nguồn mở (MIT), chạy trên Windows 10/11 64-bit.

- **Phụ đề** từ giọng nói (Whisper) hoặc từ chữ có sẵn trên hình (OCR)
- **Dịch** bằng Google Dịch (không cần khoá), Gemini hoặc Cloudflare (khoá miễn phí)
- **Thuyết minh** bằng giọng Edge (trực tuyến), Piper hoặc Kokoro (offline), nghe thử ngay trên video
- **Xuất** video có phụ đề + thuyết minh, hoặc chỉ file `.srt`; làm **hàng loạt** nhiều video một lần

---

## Cấu hình máy

|  | Tối thiểu | Khuyên dùng |
|---|---|---|
| Hệ điều hành | Windows 10 64-bit | Windows 11 64-bit |
| CPU | 4 nhân (Core i5 đời 8 / Ryzen 5 2000 trở lên) | 6 nhân trở lên |
| RAM | 8 GB | 16 GB |
| Card đồ hoạ | Không bắt buộc | NVIDIA có 4 GB VRAM trở lên (GTX 1650, RTX 2050…) |
| Ổ đĩa trống | 8 GB | 20 GB (SSD) |

- **Có card NVIDIA**: nhận dạng giọng nói nhanh hơn nhiều lần. Không có thì vẫn chạy bằng CPU, nhưng chậm, có thể lâu hơn cả thời lượng video.
- Card AMD / Intel vẫn được dùng để tách giọng khỏi nhạc nền.

**Dung lượng cần** (ước tính):

| Thành phần | Dung lượng |
|---|---|
| FreeSub bản exe | tải về khoảng 250 MB, giải nén ra khoảng 600 MB |
| Thư viện (bản mã nguồn) | khoảng 2,5 GB |
| ffmpeg | 100 MB |
| Hỗ trợ card NVIDIA (bản exe, tải khi cần) | 1,3 GB |
| Model nghe: mỗi model | 1,5 – 3,1 GB |
| Tách giọng, nhận ra người nói, giọng Piper | dưới 150 MB |
| Giọng Kokoro (tuỳ chọn) | 330 MB |
| File tạm khi làm việc | khoảng 0,5 GB cho mỗi 10 phút video |

---

## Cài đặt

### Cách 1: bản exe (dễ nhất)

1. Vào trang [Releases](https://github.com/Maditor/FreeSub/releases), tải file `FreeSub-x.y.z-win64.zip` (khoảng 250 MB).
2. Giải nén vào một thư mục **có đường dẫn không dấu**, ví dụ `D:\FreeSub`. Không đặt trong `Program Files`.
3. Chạy `FreeSub.exe`. Lần đầu app hỏi tải **ffmpeg**: bấm đồng ý.
4. Máy có card NVIDIA: vào **Cài đặt → Tải về**, tải **hỗ trợ GPU NVIDIA** (1,3 GB).

> Windows có thể hiện *"Windows protected your PC"* vì exe chưa ký số: bấm **More info → Run anyway**.

### Cách 2: chạy từ mã nguồn

```bat
git clone https://github.com/Maditor/FreeSub.git
cd FreeSub
setup.bat
run.bat
```

- Không có git: bấm **Code → Download ZIP** trên GitHub rồi giải nén (đường dẫn không dấu).
- `setup.bat` tự cài Python 3.12 (nếu chưa có), thư viện và ffmpeg. Chỉ cần chạy một lần.
- `run.bat` mở FreeSub (không kèm cửa sổ cmd).
- **Cập nhật**: `git pull` rồi chạy lại `setup.bat`.
- **Tự đóng gói exe**: chạy `build.bat`, kết quả nằm trong `dist\`.

---

## Bắt đầu nhanh

1. Bấm **Dự án mới**, kéo thả video (hoặc cả thư mục) vào cột bên trái.
2. Ở tab **Các bước** bên phải, bật những gì cần: **Phụ đề**, **Dịch**, **Thuyết minh**.
3. Bấm **Chạy** để làm rồi xem lại, sửa trong bảng phụ đề (bấm đúp vào ô). Bấm **Dừng** để dừng.
4. Bấm **Xuất**. Xong thì thư mục kết quả tự mở.

Muốn mỗi video ra một file riêng: dùng **Hàng loạt**. Bị dừng giữa chừng thì lần sau làm tiếp từ chỗ dừng.

---

## Chọn model nghe

Chọn ở **Các bước → Phụ đề → Model**. Danh sách tự lọc theo **Ngôn ngữ** đã chọn bên trên.

| Ngôn ngữ trong video | Chính xác nhất | Nhanh hơn |
|---|---|---|
| Tiếng Trung | `belle-large-v3-zh` | `belle-large-v3-turbo-zh` |
| Tiếng Nhật | `kotoba-whisper-v2.0` | |
| Tiếng Anh | `large-v3` | `distil-large-v3` |
| Ngôn ngữ khác / Tự nhận | `large-v3` | `large-v3-turbo` |

- Model có chữ **turbo** chạy nhanh hơn nhưng **nghe kém chính xác hơn**. Nên dùng bản không có turbo nếu máy chạy được.
- **Phim, anime, video có nhạc nền**: bật **Cài đặt → Nâng cao → Tách giọng và nền → Trước khi tạo phụ đề**. Giọng được tách khỏi nhạc trước khi nghe, nên chính xác hơn rõ rệt.
- **Tên nhân vật hay bị nghe sai**: mở **Tên riêng** (nút bút chì), bấm **Tìm tên trong phụ đề** để AI gợi ý tên và cách dịch (cần khoá Gemini hoặc Cloudflare). Lưu lại rồi làm lại phụ đề (nút ↻ trên bảng phụ đề).
- Model được tải về lần đầu khi dùng, hoặc tải trước ở **Cài đặt → Tải về**.

---

## Dịch

Chọn ở **Các bước → Dịch → Dịch vụ**.

- **Google Dịch**: miễn phí, không cần khoá.
- **Gemini / Cloudflare**: dịch tự nhiên hơn vì AI đọc cả đoạn hội thoại. Khoá miễn phí, nhập ở **Cài đặt → Bản dịch**.

---

<details>
<summary><b>Phím tắt</b></summary>

| Phím | Tác dụng |
|---|---|
| Space | Phát / dừng |
| ← / → | Lùi / tới 1 khung hình (Shift: 5 giây) |
| ↑ / ↓ | Câu phụ đề (hoặc điểm cắt) trước / sau |
| Ctrl+B | Cắt đôi đoạn tại vạch |
| Q / W | Xoá phần bên trái / phải vạch |
| Ctrl+Z / Ctrl+Shift+Z | Hoàn tác / làm lại |
| Ctrl + lăn chuột | Phóng to / thu nhỏ |
| Ctrl+I / Ctrl+E | Thêm video / Xuất |
| Ctrl+F / Ctrl+H | Tìm / thay thế trong phụ đề |
| Bấm đúp vào chữ trong bảng phụ đề | Video tới câu đó, sửa chữ (bấm đúp vào thời gian: chỉ sửa thời gian) |
| Chuột phải trên bảng phụ đề | Xoá dòng, thêm dòng, làm lại giọng đọc |
| Kéo câu trên timeline | Dời câu / kéo đầu, cuối; tới gần vạch đứng thì tự bắt vào vạch |

</details>

## Dữ liệu

- **Kết quả**: thư mục `freesub_<tên video>` cạnh video (đổi được trong Cài đặt).
- **Cài đặt, khoá API, log**: `Documents\FreeSub Data`.
- **Model và file tạm**: `%LOCALAPPDATA%\FreeSub`. Dọn file tạm của một video: chuột phải video → *Làm lại video này (xóa file tạm)*.

Gặp lỗi: gửi file `freesub.log` trong `Documents\FreeSub Data\logs` khi báo lỗi trên GitHub.

## Giấy phép

FreeSub dùng giấy phép MIT (`LICENSE`). Thành phần khác theo giấy phép riêng: PySide6 / Qt (LGPL-3.0),
faster-whisper, ONNX Runtime (MIT), RapidOCR (Apache-2.0), edge-tts (LGPL-3.0). Không kèm sẵn, chỉ tải khi cần:
ffmpeg (GPL), piper-tts và espeak-ng (GPL-3.0), thư viện NVIDIA CUDA, giọng Kokoro (bên thứ ba).
Các model Whisper, kotoba-whisper, Belle-whisper, Kim Vocal 2 và giọng Piper: xem giấy phép trên trang từng model.
