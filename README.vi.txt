WEB HANDLER

Tự động hóa thao tác trên web và Excel, không cần viết code.

ĐỔI NGÔN NGỮ
App mặc định dùng tiếng Anh. Chọn Tiếng Việt ở góc trên bên phải của app, app sẽ nhớ lựa chọn này.

CÀI ĐẶT (không cần mạng, không cần quyền admin, chọn một trong ba cách)
- Bộ cài: chạy WebHandler-Setup-<phiên bản>.exe, rồi mở Web Handler từ Desktop hoặc Start menu.
  Máy có thể cảnh báo vì file chưa được ký số. Nếu công ty chặn file exe, dùng cách dưới.
- Script: giải nén file zip, bấm đúp Install.bat.
- Không cài: giải nén file zip vào nơi bạn có quyền ghi, bấm đúp Start.bat, rồi bấm "Cài phần còn thiếu" trên màn hình Cài đặt.

Thử nhanh: bấm "Mở quy trình mẫu" để chạy trên trang demo có sẵn.

TẮT APP
Đóng cửa sổ đen của app. Quy trình đang chạy, trình duyệt của app và mọi tiến trình phụ đều tắt theo.
Nếu đang chạy dở, hàng đang làm được đánh dấu "Chưa rõ". Chrome và Excel của riêng bạn không bị ảnh hưởng.

DỰNG QUY TRÌNH CỦA BẠN
1. Bấm "Quy trình mới", chọn một mẫu có sẵn hoặc "Quy trình trống". Sau đó kéo khối từ cột bên trái, hoặc bấm "Ghi thao tác" để làm tay một lượt. Rê chuột lên khối để xem giải thích.
2. Chọn khối, rồi dán HTML của phần tử: F12, chuột phải vào phần tử, Copy, Copy outerHTML.
3. Kéo chuột khoanh các khối cần lặp, bỏ qua phần đăng nhập, rồi bấm "Tạo vùng lặp".
4. Bấm "Chọn Excel", chọn dải hàng ở thanh dưới, bấm "Chạy thử 1 lần", rồi "Chạy".
   Ví dụ cột A chứa link, cột B chứa nội dung: khối Mở link chọn Excel cột A, khối Điền chọn Excel cột B.
5. Muốn làm lại từ đầu, bấm "Xóa toàn bộ": xóa mọi khối, vùng lặp và cấu hình Excel của quy trình.

AN TOÀN DỮ LIỆU
- File Excel gốc không bị sửa. Kết quả nằm ở file <tên>_result_<thời gian> cạnh file gốc.
- Mật khẩu nằm trong Windows Credential Manager, không nằm trong file quy trình hay nhật ký.
- "Chạy thử 1 lần" vẫn ghi thật lên trang web cho một hàng.
- "Kiểm tra trên trang thật" chạy thật các khối phía trước, với hàng đầu tiên của Excel. Riêng khối đang kiểm tra không được thực hiện.
- Nút "Nhật ký" hiện nhật ký của hệ thống. Nhật ký không chứa mật khẩu hay giá trị đã điền, và nằm trong thư mục data.
- Thư mục data chứa quy trình, ảnh chụp lỗi và phiên đăng nhập. Đừng gửi thư mục đó cho người khác.
- Tool không vượt captcha hay MFA. Dùng khối "Làm tay" cho các bước đó.
- Hãy chắc chắn quy định của hệ thống cho phép tự động hóa trước khi dùng.

GỠ
- Cài bằng bộ cài: gỡ Web Handler trong Settings, Apps. Bộ gỡ sẽ hỏi có xóa dữ liệu của bạn không.
- Cài bằng script hoặc không cài: đóng app, rồi xóa thư mục và các shortcut.
Các bí mật đã lưu nằm trong Windows Credential Manager, mục WebHandler/.
