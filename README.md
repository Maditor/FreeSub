# FreeSub

Tạo phụ đề, dịch và thuyết minh cho video. Miễn phí, mã nguồn mở (MIT), chạy trên Windows 10/11 64-bit.

## Tính năng

- **Phụ đề** từ giọng nói (Whisper) hoặc từ chữ có sẵn trên hình (OCR).
- **Dịch** bằng Google Dịch (không cần key), Gemini hoặc Cloudflare (key miễn phí, nhập trong Cài đặt).
- **Thuyết minh** bằng giọng Edge (trực tuyến) hoặc Piper (offline). Có thể nghe thử ngay trên video
  bằng nút micro trước khi xuất.
- **Xuất** video có phụ đề và thuyết minh, hoặc chỉ file `.srt`.
- **Hàng loạt**: làm nhiều video một lần, mỗi video có thể có cài đặt riêng.
- Kiểu chữ, khung phụ đề, cắt đoạn video, xem trước đúng như khi xuất.
- Giao diện tiếng Việt hoặc tiếng Anh.

## Cài đặt

1. Tải mã nguồn, giải nén vào thư mục có đường dẫn không dấu.
2. Chạy `setup.bat` (tự cài Python, thư viện và ffmpeg).
3. Chạy `run.bat`.

## Cách dùng

1. Kéo thả video (hoặc cả thư mục) vào cửa sổ.
2. Ở cột phải, bật các bước cần làm: **Phụ đề**, **Dịch**, **Thuyết minh**.
3. Bấm **Xuất**. Xong thì thư mục kết quả tự mở.

Bị dừng giữa chừng thì lần sau làm tiếp từ chỗ dừng. Sửa phụ đề: bấm đúp vào ô trong bảng phụ đề.

| Phím | Tác dụng |
|---|---|
| Space | Phát / dừng |
| ← / → | Lùi / tới 5 giây |
| Ctrl + lăn chuột | Phóng to / thu nhỏ video |
| Ctrl+E | Xuất |

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
