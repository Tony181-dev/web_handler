WEB HANDLER

Tự động hóa thao tác trên web và Excel, không cần viết code.

ĐỔI NGÔN NGỮ
App mặc định dùng tiếng Anh. Chọn Tiếng Việt ở góc trên bên phải của app, app sẽ nhớ lựa chọn này.

CÀI ĐẶT TRÊN WINDOWS (không cần mạng, không cần quyền admin, chọn một trong ba cách)
- Bộ cài: chạy WebHandler-Setup-<phiên bản>.exe, rồi mở Web Handler từ Desktop hoặc Start menu.
  Máy có thể cảnh báo vì file chưa được ký số. Nếu công ty chặn file exe, dùng cách dưới.
- Script: giải nén file zip, bấm đúp Install.bat.
- Không cài: giải nén file zip vào nơi bạn có quyền ghi, bấm đúp Start.bat, rồi bấm "Cài phần còn thiếu" trên màn hình Cài đặt.

CÀI ĐẶT TRÊN macOS (macOS 14 trở lên với chip Apple, macOS 13 trở lên với chip Intel; không cần mạng, không cần quyền admin)
Dùng file WebHandler-<phiên bản>-macOS-arm64.zip cho máy chip Apple (M1, M2...), -macOS-x86_64.zip cho máy chip Intel.
- Script: giải nén file zip, bấm đúp Install.command. App được cài vào ~/Library/Application Support/WebHandler,
  rồi mở Web Handler bằng Launchpad, Spotlight, hoặc thư mục Applications trong thư mục nhà của bạn.
- Không cài: giải nén file zip vào nơi bạn có quyền ghi, bấm đúp Start.command, rồi bấm "Cài phần còn thiếu" trên màn hình Cài đặt.
Gói chưa được Apple ký số, nên lần đầu macOS có thể báo không mở được file .command. Khi đó mở System Settings,
Privacy & Security, kéo xuống và bấm "Open Anyway". Hoặc mở Terminal và chạy:
  xattr -dr com.apple.quarantine <thư mục vừa giải nén>
macOS có thể hỏi cho Terminal truy cập thư mục Downloads hoặc Desktop: hãy cho phép để cài được.
Khác biệt trên Mac:
- Phím tắt dùng ⌘ thay cho Ctrl (⌘Z, ⌘⇧Z, ⌘A). Chọn thêm khối thì giữ ⌘ khi bấm hoặc khoanh.
- Mở DevTools của Chrome bằng ⌘⌥I thay cho F12. Xóa khối bằng phím delete.
- Mật khẩu nằm trong Keychain của macOS, mục WebHandler/.

Thử nhanh: bấm "Mở quy trình mẫu" để chạy trên trang demo có sẵn.

TẮT APP
Đóng cửa sổ đen của app (trên macOS là cửa sổ Terminal, hoặc bấm Ctrl+C trong đó). Quy trình đang chạy, trình duyệt của app và mọi tiến trình phụ đều tắt theo.
Nếu đang chạy dở, hàng đang làm được đánh dấu "Chưa rõ". Chrome và Excel của riêng bạn không bị ảnh hưởng.

DỰNG QUY TRÌNH CỦA BẠN
1. Bấm "Quy trình mới", chọn một mẫu có sẵn hoặc "Quy trình trống". Sau đó kéo khối từ cột bên trái, hoặc bấm "Ghi thao tác" để làm tay một lượt. Rê chuột lên khối để xem giải thích.
   Để đăng nhập, dùng khối Đăng nhập: lưu mật khẩu ở mục Bí mật trước, rồi chọn nó trong khối.
2. Chọn khối, rồi dán HTML của phần tử: F12, chuột phải vào phần tử, Copy, Copy outerHTML.
3. Để lặp các khối, bấm khối Vùng lặp ở cột trái rồi kéo khối vào đó. Hoặc kéo chuột khoanh các khối đã có, bỏ qua phần đăng nhập, rồi bấm "Tạo vùng lặp".
4. Bấm "Chọn Excel", chọn dải hàng ở thanh dưới, bấm "Chạy thử 1 lần", rồi "Chạy".
   Ví dụ cột A chứa link, cột B chứa nội dung: khối Mở link chọn Excel cột A, khối Điền chọn Excel cột B.
5. Muốn làm lại từ đầu, bấm "Xóa toàn bộ": xóa mọi khối, vùng lặp và cấu hình Excel của quy trình.

AN TOÀN DỮ LIỆU
- File Excel gốc không bị sửa. Kết quả nằm ở file <tên>_result_<thời gian> cạnh file gốc.
- Mật khẩu nằm trong Windows Credential Manager (trên macOS là Keychain), không nằm trong file quy trình hay nhật ký.
- "Chạy thử 1 lần" vẫn ghi thật lên trang web cho một hàng.
- "Kiểm tra trên trang thật" chạy thật các khối phía trước, với hàng đầu tiên của Excel. Riêng khối đang kiểm tra không được thực hiện.
- Nút "Nhật ký" hiện nhật ký của hệ thống. Nhật ký không chứa mật khẩu hay giá trị đã điền, và nằm trong thư mục data.
- Thư mục data chứa quy trình, ảnh chụp lỗi và phiên đăng nhập. Đừng gửi thư mục đó cho người khác.
- Khối "Đọc OCR" đọc được captcha đơn giản, và có thể đọc sai. Khối chạy trên máy này, ảnh không được lưu hay gửi đi đâu. Với MFA và captcha khó, hãy dùng khối "Làm tay".
- Hãy chắc chắn quy định của hệ thống cho phép tự động hóa, kể cả việc đọc captcha, trước khi dùng.

CHIA SẺ CHO NGƯỜI KHÁC
Bấm đúp Install.bat trong thư mục mã nguồn. Nó đóng gói app, lần đầu cần có mạng, Python 3 và Inno Setup 6.
Gửi file WebHandler-Setup-<phiên bản>.exe vừa tạo. Người nhận chỉ cần chạy đúng một file đó.
Trên macOS: bấm đúp Install.command trong thư mục mã nguồn (lần đầu cần có mạng và Python 3).
Nó tạo dist/WebHandler-<phiên bản>-macOS-<arm64 hoặc x86_64>.zip cho loại máy Mac đang dùng để đóng gói.
Gửi file zip đó. Người nhận giải nén rồi bấm đúp Install.command.

GỠ
Bấm "Uninstall Web Handler" trong Start menu, hoặc bấm đúp Uninstall.bat trong thư mục app.
Nó đóng app, xóa file và shortcut của app, rồi hỏi có xóa luôn dữ liệu không:
flow, lịch sử chạy, phiên đăng nhập và các bí mật đã lưu. Chọn No để giữ lại cho lần cài sau.
- Cài bằng bộ cài: gỡ trong Settings, Apps, Web Handler cũng được.
- Không cài (chỉ giải nén zip): Uninstall.bat trong thư mục đó gỡ theo cách tương tự.
Bí mật được giữ lại nằm trong Windows Credential Manager, mục WebHandler/.
Trên macOS: mở "Uninstall Web Handler" trong thư mục Applications của bạn, hoặc bấm đúp Uninstall.command trong
thư mục app (~/Library/Application Support/WebHandler). Nó hỏi tương tự, trả lời y hoặc n.
Bí mật được giữ lại nằm trong Keychain, mục WebHandler/ (xem bằng ứng dụng Keychain Access).
