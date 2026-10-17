# FreeSub

Tạo phụ đề, dịch và thuyết minh cho video. Miễn phí, mã nguồn mở (MIT), chạy trên Windows 10/11 64-bit.

## Cài đặt

1. Tải mã nguồn và giải nén vào một thư mục (đường dẫn nên không có dấu).
2. Chạy `setup.bat`. Script sẽ tự cài Python 3.12 64-bit nếu máy chưa có, cài các thư viện và tải ffmpeg.
3. Chạy `run.bat`.

## Cách dùng

Khi mở app, màn hình **Projects** hiện các project đã làm trước đó (mới nhất ở đầu), lưu cho tới khi anh xóa
(chuột phải → *Delete project*). Bấm vào một project là mở lại video cùng phụ đề đã làm. Nút ô vuông
trên cùng để quay về màn hình này.

Màn hình làm việc chia ba cột: **danh sách video và Cut** bên trái, **preview và bảng phụ đề** ở giữa,
**công thức** bên phải.

1. Bấm **New project**, hai biểu tượng thêm file / thêm thư mục ở đầu cột video, hoặc kéo thả video, cả thư mục vào cửa sổ.
Màn hình làm việc: thanh lệnh ở trên (Projects, Batch; bên phải là **Export**, sáng/tối, cài đặt), danh sách video bên trái (trên cùng có nút thêm file, thêm thư mục, đổi kiểu xem dạng danh sách / lưới có ảnh xem trước, xóa), khung xem video và bảng phụ đề ở giữa, **Inspector** bên phải với 3 tab *Steps*, *Style*, *Cut*.
Thanh lệnh nằm luôn trên khung cửa sổ (thay thanh tiêu đề của Windows): kéo vùng trống để di chuyển, nhấp đúp để phóng to, vẫn snap được. Nếu thích thanh tiêu đề gốc của Windows: ⚙ → General → *Use the Windows title bar*, rồi mở lại app.

2. Ở tab **Steps** (Inspector), bật hoặc tắt từng bước rồi chỉnh tùy chọn. Các lựa chọn được lưu lại cho lần sau.
   - **Subtitles**: lấy phụ đề *từ giọng nói* (Whisper) hoặc *từ chữ trên hình* (OCR).
     Với OCR, bấm *Select the subtitle area* rồi kéo một khung quanh dòng phụ đề trên video.
     *New line when the speaker changes*: app nghe ra giọng từng người, hai người nói chung một dòng sẽ được tách thành hai dòng, mỗi người giữ một giọng thuyết minh (lần đầu tải model 28 MB).
   - **Translate**: Google Dịch (mặc định, không cần key), Gemini hoặc Cloudflare (cần API key, nhập ở ⚙ → Translation).
     Có thể ghi bối cảnh phim và bảng tên riêng cho AI dịch chuẩn hơn. Dịch vụ nào lỗi thì tự chuyển sang dịch vụ khác.
   - **Narration** (thuyết minh): đọc bản dịch bằng giọng Edge (trực tuyến, có giọng nam và nữ) hoặc Piper (chạy offline).
     Có thêm **Kokoro (tiếng Việt)**: 14 giọng tự nhiên (Ngọc Huyền, Mai Linh, Tuấn Ngọc…), chạy offline. Đây là giọng của **bên thứ ba** ([contextboxai/Kokoro-Vietnamese](https://huggingface.co/contextboxai/Kokoro-Vietnamese) trên Hugging Face): FreeSub **không kèm sẵn**, chỉ tải khi bạn bấm *Download the Kokoro voices* (khoảng 330 MB). Trang gốc không nói giọng được huấn luyện từ dữ liệu nào, nên hãy tự kiểm tra trước khi dùng cho mục đích thương mại. Phần *Sound mix* có 3 mức âm lượng riêng: **Narration** (giọng thuyết minh), **Background** (nhạc, tiếng động), **Original voice** (giọng gốc của video; cần bật *Split the original voice from the music*). *Lower the original voice…* hạ giọng gốc khi đang đọc thuyết minh, hết câu thì to lại.
     Nghe thử trước khi xuất: bấm **nút micro**; **chuột phải** vào nút micro → *Make the narration again* để làm lại thuyết minh theo phụ đề đã sửa và cài đặt hiện tại. Bấm **nút micro** trên thanh điều khiển video. App bắt đầu làm thuyết minh cho video đang chọn (không xuất video); câu nào xong là nghe được câu đó ngay khi phát video, và thanh thời gian sáng lên tới đâu là thuyết minh đã sẵn tới đó. Làm xong thì tự chuyển sang bản trộn đầy đủ; kéo các mức âm lượng là nghe thay đổi liền. Xuất sau đó chỉ trộn lại, không tạo lại giọng đọc.
     Nút **Listen** có menu chọn nghe câu mẫu bằng giọng nữ (Women) hoặc giọng nam (Men).
     Giọng nam/nữ của từng câu: app đoán từ cao độ giọng (đo trên giọng đã tách nhạc nếu có, gộp theo từng người nói, ngưỡng tự chỉnh theo từng video). Cột **Voice** trong bảng phụ đề hiện ♀/♂; bấm vào để sửa (lần 1: đổi giọng, lần 2: giữ giọng đoán nhưng đánh dấu, lần 3: về tự đoán). Đánh dấu 1–2 câu mỗi giọng là các câu nghe giống thế (cùng người nói, cùng "dấu vân giọng", hoặc cùng tầm cao độ) tự theo; chữ xanh đậm = anh đánh dấu, chữ trắng = học theo dấu, chữ mờ = tự đoán.
     Mặc định cả video dùng **một giọng** (*One voice for all*, cạnh Speed, giọng ở hàng *All lines*). Bỏ tick để dùng giọng nữ và giọng nam riêng (**đang thử nghiệm**, có thể chọn nhầm giọng ở vài câu; sửa ở cột *Voice* của bảng phụ đề, cột này chỉ hiện khi bỏ tick).
     Câu dịch dài hơn câu gốc: app không chỉ đọc nhanh hơn. Trước tiên nó gộp các câu ngắn đi liền nhau (một câu bị cắt làm hai dòng, hoặc câu dài đứng cạnh câu ngắn) thành một lần đọc, cho câu bắt đầu sớm một chút hoặc lấn sang khoảng của câu sau rồi bắt kịp ở chỗ ngừng, và ở đoạn thoại dày thì tăng tốc đều cả đoạn. Chỉ phần còn thiếu mới đọc nhanh hơn, tối đa theo ⚙ → Advanced → *Fastest reading* (mặc định 1.25x). Phụ đề hiện trên video (khung xem trước và video xuất) đi theo thời điểm câu được đọc: câu đọc sớm, trễ hay đọc gộp thì phụ đề cũng dời theo (nút đồng hồ có sóng âm cạnh nút *Fit the lines* trên bảng phụ đề bật/tắt: bật thì bảng và video hiện giờ đọc, tô màu xanh; tắt thì về giờ gốc, xuất video cũng theo nút này). File .srt vẫn giữ thời gian gốc.
   - Trên bảng phụ đề: nút **↻** làm lại phụ đề (và bản dịch) của video đang chọn theo cài đặt mới ở cột phải (các chỗ đã sửa tay sẽ bị thay); nút **nhập .srt** cho chọn dùng file làm *phụ đề gốc* hay *bản dịch*. Phụ đề và bản dịch mới làm xong được tự ngắt dòng cho vừa khung một lần (như nút *Fit the lines to the box*); bản dịch anh tự nhập hoặc đã sửa thì giữ nguyên.
   - Tab **Style**: font (danh sách font trong máy, có ô tìm kiếm), cỡ chữ, đậm, màu, viền chữ, và **Box**: bấm nút vẽ khung (biểu tượng khung cạnh dòng Box) rồi kéo trên video, phụ đề sẽ hiện trong khung đó trên nền mờ và tối (chỉnh được Dark, Blur). Bật *Always* thì khung hiện suốt video, không chỉ lúc có câu. Khung này cũng là vùng OCR đọc phụ đề cứng, nên vẽ một lần là dùng cho cả hai. Đổi gì cũng thấy ngay trên video.
   Thông tin máy (card đồ hoạ, ffmpeg), giải thích chất lượng và nguồn gốc các giọng/model nằm ở **Settings → About**.
   Các tuỳ chọn ít dùng (model Whisper, beam, encoder, MP4/MKV…) nằm trong **Settings → Advanced**.
3. Bấm **Export** rồi chọn: **Video with subtitles** (phụ đề in thẳng vào video; chọn phụ đề hiện, độ phân giải, chất lượng; bật Narration thì video có thuyết minh) hoặc **Subtitles only (.srt)** để chỉ xuất file `.srt`. Bấm OK để chạy hết các bước đã bật.
   Xong là thư mục kết quả tự mở ra.

**Batch** (trên thanh lệnh): mọi video trong danh sách hiện thành thẻ, có ảnh nhỏ, trạng thái từng bước (phụ đề, dịch, thuyết minh, xuất) và thanh tiến trình. Tick các video cần làm rồi bấm *Export* trên thanh lệnh (chọn video hoặc chỉ .srt): các video chạy lần lượt từng cái, dùng cài đặt chung ở cột phải. Muốn một video làm khác (không dịch, dịch sang tiếng khác, cỡ chữ khác…) thì bấm bánh răng trên thẻ của nó; video có cài đặt riêng có bánh răng tô màu. Bấm đúp ảnh để mở video trong trình chỉnh sửa. Xong cả lô mới mở thư mục kết quả một lần. Trên máy đủ mạnh (4 nhân CPU, 8 GB RAM trở lên, có card NVIDIA hoặc 8 nhân), Batch tự làm **hai video cùng lúc**: trong khi video trước đang thuyết minh và xuất, video sau đã làm phụ đề, không tốn thêm bộ nhớ card đồ họa. Đổi ở ⚙ → Advanced → *Videos at once* (Auto / One / Two).

**Video AV1** (hay gặp ở video tải từ YouTube): card đồ hoạ đời cũ (trước RTX 30) không phát được AV1 trong khung xem trước, nên FreeSub tự tạo một bản xem trước H.264 nhỏ (chỉ làm một lần). Vài giây đầu là xem được ngay, phần sau làm tới đâu xem được tới đó. Xuất video, OCR… vẫn dùng video gốc.

**Bảng phụ đề** (dưới video): bấm đúp vào ô để sửa câu. Nút *Fit the lines to the box* (cạnh nút lưu .srt) ngắt dòng lại toàn bộ phụ đề cho vừa khung đã vẽ (hoặc vừa chiều ngang video), các dòng dài đều nhau.

**Cut** (tab Cut trong Inspector): *Keep* chỉ xuất đoạn từ Start tới End; *Remove* bỏ đoạn đó đi. Gõ thời gian
(vd `1:05.5`) hoặc bấm *Now* để lấy vị trí đang xem. Hình, tiếng và phụ đề đều được cắt theo.

**Bảng phụ đề:** bấm đúp vào ô để sửa câu gốc, bản dịch hoặc thời gian. Sửa xong được lưu ngay.
Bấm vào một dòng để video nhảy tới câu đó.

**Khung thoại cho phụ đề cứng:** khi lấy phụ đề bằng OCR và in phụ đề lên hình, vùng chữ cũ được làm mờ
(blur 10 px) và phủ một lớp tối 20%, bản dịch hiện bên trong. Khung chỉ xuất hiện khi có thoại.
Preview hiển thị đúng như video sẽ xuất ra.

**Kết quả và file làm việc:**
- Thư mục kết quả: mặc định là thư mục `freesub_<5 từ đầu của tên video>` tạo cạnh video, hoặc một thư mục
  anh chọn trong ⚙. Trong đó chỉ có `Tên video.en.srt`, `Tên video.vi.srt` và `Tên video.freesub.mp4`.
- File làm việc (âm thanh, giọng tách, câu thuyết minh…) nằm trong `%LOCALAPPDATA%\FreeSub\projects`.
- Bị dừng giữa chừng thì lần sau chạy tiếp từ chỗ dừng. Bước nào đã xong thì không làm lại.
- Chuột phải vào video → *Start this video again* để làm lại từ đầu.
- Xóa project chỉ xóa file làm việc; video gốc, file `.srt` và video đã xuất vẫn giữ nguyên.

| Phím | Tác dụng |
|---|---|
| Space | Phát / dừng |
| ← / → | Lùi / tới 5 giây |
| , / . | Lùi / tới 1 khung hình |
| Ctrl + lăn chuột | Phóng to / thu nhỏ video |
| Ctrl+E | Export |
| Esc | Hủy kéo chọn vùng |

**Chất lượng nhận diện giọng nói** (Subtitles → Quality):

| Mức | Làm gì |
|---|---|
| Fast | large-v3-turbo, beam 1, cho bản nháp nhanh |
| Balanced | large-v3-turbo, beam 5, dùng cho đa số video (mặc định) |
| Best | Tách giọng khỏi nhạc nền rồi chạy large-v3. Tiếng Nhật dùng kotoba-whisper, tiếng Việt dùng PhoWhisper |

**Đọc tên nước ngoài:** giọng tiếng Việt đọc tên và từ nước ngoài theo cách người Việt vẫn nói
(Mike → Mai-cơ, Netflix → Nét-phờ-lích, Sasuke → Xa-xư-kê), nên cả câu chỉ cần một giọng.
Từ nào đọc chưa đúng thì sửa trong Narration → *Dictionary*.

**Lệnh hữu ích:**
- `run.bat --check`: kiểm tra GPU, ffmpeg, OCR và các model đã tải.
- `run.bat --cli export "D:\Videos"`: chạy công thức đã lưu cho cả thư mục mà không cần mở cửa sổ.

**Ngôn ngữ giao diện:** Settings → General → Language: *Theo Windows*, English hoặc Tiếng Việt (app tự mở lại).

**Dữ liệu của bạn** nằm trong `Documents\FreeSub Data`: `settings.json` (cài đặt và API key), `logs\`
(có thời gian của từng bước). Model và file tạm nằm trong `%LOCALAPPDATA%\FreeSub`.
Bản cũ để `settings.json` cạnh app sẽ được tự chép sang lần chạy đầu.

## Giấy phép

- FreeSub: MIT, xem `LICENSE`.
- **ffmpeg** được gọi như một chương trình riêng. `setup.bat` tải bản build sẵn (gyan.dev hoặc BtbN) vào
  thư mục `ffmpeg\`. Đây là bản GPL, giấy phép đi kèm trong thư mục đó. Xem thêm: https://ffmpeg.org/legal.html
- faster-whisper và CTranslate2 (MIT), model Whisper (MIT), PySide6 / Qt (LGPLv3), ONNX Runtime (MIT),
  RapidOCR (Apache-2.0), NVIDIA CUDA / cuDNN (giấy phép NVIDIA, cài qua pip), piper-tts và espeak-ng (GPL-3.0, cài qua pip),
  edge-tts (LGPL-3.0). Các model kotoba-whisper, PhoWhisper, Kim Vocal 2 (UVR) và giọng Piper: giấy phép xem
  trên trang của từng model.
