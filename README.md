# FreeSub - Đang phát triển, chưa ổn định
##(hết Token, thứ 5 cập nhật tiếp)

Tạo phụ đề, dịch và thuyết minh cho video. Miễn phí, mã nguồn mở (MIT), chạy trên Windows 10/11 64-bit.

---
<img width="1920" height="1080" alt="{A2E4E139-F04B-4368-9A95-ADB744FABA8F}" src="https://github.com/user-attachments/assets/f439783c-772b-4bd7-98fd-191cd715497a" />

<img width="1920" height="1080" alt="{997061E2-595D-4997-A9F8-8E5BA9C8C871}" src="https://github.com/user-attachments/assets/ac22128c-2b42-44da-b57d-a77fd980624b" />

<img width="1920" height="1080" alt="{113D3DD3-6D9A-4E88-829A-0E57F31BABC6}" src="https://github.com/user-attachments/assets/2988a136-4489-402f-869d-0ce4d0a3c011" />



---

## Tính năng

- **Phụ đề** từ giọng nói (Whisper) hoặc từ chữ có sẵn trên hình (OCR).
- **Dịch** bằng Google Dịch (không cần key), Gemini hoặc Cloudflare (key miễn phí, nhập trong Cài đặt).
- **Thuyết minh** bằng giọng Edge (trực tuyến) hoặc Piper (offline). Có thể nghe thử ngay trên video
  bằng nút micro trước khi xuất.
- **Xuất** video có phụ đề và thuyết minh, hoặc chỉ file `.srt`.
- **Dự án**: mỗi dự án chứa nhiều video (đánh số, kéo để đổi thứ tự) và dùng chung một kiểu chữ, khung phụ đề.
- **Timeline**: kéo video vào, cắt, đổi thứ tự, dời phụ đề, rồi xuất thành một video (hoặc một file `.srt`).
- **Hàng loạt** (Batch): làm từng video trong dự án, mỗi video ra một file.
- **Tốc độ phát** 0.25× – 2×: xem trước và xuất video đúng tốc độ đó.
- **Danh sách tên riêng** cho từng bộ phim: nghe đúng tên khi làm phụ đề, dịch đúng theo cách của bạn.
- Kiểu chữ, khung phụ đề, xem trước đúng như khi xuất. Giao diện tiếng Việt hoặc tiếng Anh.

## Cài đặt

1. Tải mã nguồn, giải nén vào thư mục có đường dẫn không dấu.
2. Chạy `setup.bat` (tự cài Python, thư viện và ffmpeg).
3. Chạy `run.bat`.

## Cách dùng

1. Bấm **Dự án mới**, kéo thả video (hoặc cả thư mục) vào cột Media.
2. Kéo video xuống timeline (cắt bỏ đoạn thừa nếu cần).
3. Ở cột phải, bật các bước cần làm: **Phụ đề**, **Dịch**, **Thuyết minh**.
4. Bấm **Xuất**. Xong thì thư mục kết quả tự mở. Muốn mỗi video ra một file riêng: dùng **Batch**.

Bị dừng giữa chừng thì lần sau làm tiếp từ chỗ dừng. Sửa phụ đề: bấm đúp vào ô trong bảng phụ đề.

| Phím | Tác dụng |
|---|---|
| Space | Phát / dừng |
| ← / → | Lùi / tới 1 khung hình (Shift: 5 giây) |
| ↑ / ↓ | Điểm cắt (hoặc câu phụ đề) trước / sau |
| Ctrl+B | Cắt đôi đoạn tại vạch |
| Q / W | Xoá phần bên trái / phải vạch |
| Ctrl+Z / Ctrl+Shift+Z | Hoàn tác / làm lại |
| Ctrl + lăn chuột | Phóng to / thu nhỏ |
| Ctrl+I / Ctrl+E | Thêm video / Xuất |

Bản exe (trang Releases): giải nén rồi chạy `FreeSub.exe`, không cần cài Python. Lần đầu app hỏi tải ffmpeg.
Piper và hỗ trợ GPU NVIDIA chỉ tải khi bạn bấm nút tải trong app.

Tự đóng gói exe: chạy `setup.bat` một lần, rồi `build.bat`. Kết quả nằm trong `dist\`.

## Dữ liệu

- Kết quả: thư mục `freesub_<tên video>` cạnh video (đổi được trong Cài đặt).
- Cài đặt, API key và log: `Documents\FreeSub Data`.
- Model và file tạm: `%LOCALAPPDATA%\FreeSub`.

## Giấy phép

FreeSub dùng giấy phép MIT (xem `LICENSE`). Các thành phần khác dùng theo giấy phép riêng:
ffmpeg (GPL, tải riêng qua `setup.bat`), PySide6 / Qt (LGPLv3), faster-whisper (MIT), ONNX Runtime (MIT),
RapidOCR (Apache-2.0), piper-tts và espeak-ng (GPL-3.0), edge-tts (LGPL-3.0).
Giọng Kokoro là của bên thứ ba, không kèm sẵn, chỉ tải khi bạn chọn.
Model Whisper, PhoWhisper, kotoba-whisper, Kim Vocal 2 và giọng Piper: xem giấy phép trên trang từng model.
