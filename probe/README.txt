WEB HANDLER - GÓI KIỂM TRA MÔI TRƯỜNG

Cách dùng
1. Giải nén toàn bộ thư mục WebHandlerProbe vào một nơi bạn có quyền ghi, ví dụ Desktop.
2. Windows: bấm đúp Probe.bat. macOS: bấm đúp Probe.command (nếu macOS chặn, xem mục bên dưới).
   Chờ đến khi có dòng KẾT LUẬN. Việc này có thể mất vài phút.
3. Gửi lại nội dung file probe_report.txt.

Gói này sẽ làm gì
- Giải nén Python riêng và cài thư viện vào chính thư mục này, không cần mạng và không cần quyền admin.
- Mở Chrome trong chốc lát, chỉ tới một trang thử trên chính máy này (127.0.0.1).
- Tạo một file Excel tạm, và một khóa thử trong Windows Credential Manager (trên macOS là Keychain) rồi xóa ngay.

Gói này không làm gì
- Không truy cập hay ghi vào hệ thống nội bộ nào.
- Không hỏi mật khẩu.
- Không sửa Python, PATH hay Chrome của máy.
- Không gửi dữ liệu đi đâu. Bạn tự quyết định gửi file báo cáo.

Trong báo cáo có phiên bản hệ điều hành, Python và Chrome. Tên người dùng, tên máy và đường dẫn cá nhân được che đi. Bạn có thể đọc file trước khi gửi.

Nếu phần mềm bảo mật cảnh báo hoặc chặn
Đừng tìm cách vượt qua. Hãy để nó chặn, chờ gói chạy xong rồi gửi báo cáo. Việc bị chặn cũng là kết quả cần biết. Nếu cần, hãy hỏi bộ phận IT của công ty.

macOS báo "không thể xác minh nhà phát triển"
Gói không được Apple ký số. Mở System Settings → Privacy & Security, kéo xuống và bấm "Open Anyway" cho Probe.command.
Hoặc mở Terminal và chạy: xattr -dr com.apple.quarantine <thư mục WebHandlerProbe>

Muốn gỡ
Xóa toàn bộ thư mục này.
