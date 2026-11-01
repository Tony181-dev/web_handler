WEB HANDLER

Tự động hóa thao tác trên web và Excel, không cần viết code.

CÀI ĐẶT (không cần mạng, không cần quyền admin, chọn một trong ba cách)
- Bộ cài: chạy WebHandler-Setup-<phiên bản>.exe, rồi mở Web Handler từ Desktop hoặc Start menu.
  Máy có thể cảnh báo vì file chưa được ký số. Nếu công ty chặn file exe, dùng cách dưới.
- Script: giải nén file zip, bấm đúp Install.bat.
- Không cài: giải nén file zip vào nơi bạn có quyền ghi, bấm đúp Start.bat, rồi bấm "Install missing" trên màn hình Setup.

Thử nhanh: bấm "Open sample flow" để chạy trên trang demo có sẵn.

TẮT APP
Đóng cửa sổ đen của app. Quy trình đang chạy, trình duyệt của app và mọi tiến trình phụ đều tắt theo.
Nếu đang chạy dở, hàng đang làm được đánh dấu "Chưa rõ". Chrome và Excel của riêng bạn không bị ảnh hưởng.

DỰNG QUY TRÌNH CỦA BẠN
1. Bấm "New flow", chọn một mẫu có sẵn hoặc "Blank flow". Sau đó kéo khối từ cột bên trái, hoặc bấm "Record" để làm tay một lượt.
2. Chọn khối, rồi dán HTML của phần tử: F12, chuột phải vào phần tử, Copy, Copy outerHTML.
3. Kéo chuột khoanh các khối cần lặp, bỏ qua phần đăng nhập, rồi bấm "Create loop".
4. Bấm "Select Excel", chọn dải hàng ở thanh dưới, bấm "Run once", rồi "Run".
   Ví dụ cột A chứa link, cột B chứa nội dung: khối Open link chọn Excel cột A, khối Fill chọn Excel cột B.
5. Muốn làm lại từ đầu, bấm "Clear all": xóa mọi khối, vùng lặp và cấu hình Excel của quy trình.

AN TOÀN DỮ LIỆU
- File Excel gốc không bị sửa. Kết quả nằm ở file <tên>_result_<thời gian> cạnh file gốc.
- Mật khẩu nằm trong Windows Credential Manager, không nằm trong file quy trình hay log.
- "Run once" vẫn ghi thật lên trang web cho một hàng.
- "Check on live page" chạy thật các khối phía trước, với hàng đầu tiên của Excel. Riêng khối đang kiểm tra không được thực hiện.
- Nút "Log" hiện log của hệ thống. Log không chứa mật khẩu hay giá trị đã điền, và nằm trong thư mục data.
- Thư mục data chứa quy trình, ảnh chụp lỗi và phiên đăng nhập. Đừng gửi thư mục đó cho người khác.
- Tool không vượt captcha hay MFA. Dùng khối "Manual step" cho các bước đó.
- Hãy chắc chắn quy định của hệ thống cho phép tự động hóa trước khi dùng.

GỠ
- Cài bằng bộ cài: gỡ Web Handler trong Settings, Apps. Bộ gỡ sẽ hỏi có xóa dữ liệu của bạn không.
- Cài bằng script hoặc không cài: đóng app, rồi xóa thư mục và các shortcut.
Các bí mật đã lưu nằm trong Windows Credential Manager, mục WebHandler/.
