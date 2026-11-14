# Kiểm thử điều hướng bất đồng bộ

Chạy từ root repository bằng Node.js có `node:test`:

```sh
node --test tests/js/*.test.cjs
```

Không cần npm package. Test chạy các hàm thật từ `resources/common.js` và
`templates/contest/media-js.html` trong VM với transport/DOM fake tối thiểu.
Các callback được chủ động trả sai thứ tự hoặc sau abort để kiểm tra chống
ghi đè, giữ draft trước điều hướng, polling, lỗi mạng và dọn trạng thái busy.
IDE tests chạy các hàm thật trong `resources/ide.js`: tạo Run/poll/JSON đến trễ
khi chuyển bài, hủy timer cũ, DOM bị thay, nhiều lần chuyển bài đồng thời và
khóa test case/I/O theo người dùng/ngữ cảnh.

Đây là kiểm thử logic điều phối request, không thay thế browser QA, xác nhận
layout, sự kiện click thực, tải static hoặc tích hợp máy chấm. Browser evidence
và checklist đi kèm bản bàn giao ghi riêng các kết quả đó.
