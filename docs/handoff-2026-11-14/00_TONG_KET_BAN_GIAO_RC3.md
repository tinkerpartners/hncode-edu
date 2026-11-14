# Tổng kết bàn giao HNCode v1.0.0-rc.3

Ngày 14/11/2026 · Bản review cho dev lead/QA, kế thừa toàn bộ RC2.

## 1. Phiên bản và nơi nhận code

| Nội dung | Giá trị |
|---|---|
| Nhánh local | `mrtee-hncode-v1.0.0-rc.3` |
| Base repository | `e665d7b3642478fbeb102b45411ad56d59197a3f` |
| Commit chức năng RC3 | `b5da9b373374d4e826de7b14ad58a7c71ad1c509` |
| HEAD bàn giao | Đọc `RELEASE_MANIFEST.json` trong ZIP; gồm commit tài liệu sau commit code |
| Checkout review | `D:\AI-Code\Build_HNCode\.local\worktrees\backlog-20261113` |
| Bộ cài đang chạy | `D:\AI-Code\Build_HNCode` — [localhost](http://localhost:8001/) |
| Gói gửi dev | `HNCODE_MRTEE_v1.0.0-rc.3_BAN_GIAO.zip` |
| GitHub/VPS | Nhánh mới chưa push; chưa tạo PR mới, merge hoặc deploy VPS |

Bundle chứa đầy đủ commit từ base, gồm backlog/UI/API/AI chuẩn bị của các bản trước. Không nhập đồng thời bundle và patch vào một working tree. Không có DB dump, settings cá nhân, mật khẩu/token. Dữ liệu QA giữ trên local.

## 2. Những phần đã có và trạng thái nghiệm thu

| Phần/issue | Kết quả kỹ thuật | Còn lại |
|---|---|---|
| RC3 UX | Run/Submit mobile hiện kết quả, giữ rail, abort response cũ, Ranking empty/error/retry, nhãn/bàn phím, hạng toàn tập khi lọc | Mobile thật, screen reader, ma trận tương tác của team |
| RC3 hiệu năng | Bỏ N+1 default Language khi dựng Profile cache; Grades Course 124→21 SQL, Lesson 125→22 SQL trên DB local | Đo tải mục tiêu; không suy rộng thời gian local thành production |
| #135 Grades | Fingerprint theo dòng, phát hiện đổi điểm dù tổng không đổi, polling và test | Nghiệm thu UI/poll/tải |
| #140 bảng điểm/Excel | STT, tổng điểm số, XLSX Course/Lesson/Contest; format % đúng; nút gọn; RC3 giữ hạng khi lọc | Mở Microsoft Excel, QA quyền/tiếng Việt/điểm |
| #39 hiệu năng Course | Batch tiến độ, benchmark toàn trang local trong RC3 | Benchmark theo lớp/quy mô issue; chưa đề nghị Closed |
| #110 IDE và #141/#142 | Nháp theo context, Ace/AJAX, strict IDE; Run và nộp thật | Reset Hủy/Xác nhận và ma trận strict contest |
| #138 Cầy Cốt | API GET, scope/token hash/quota/paging/che kết quả + tài liệu | Contract và client tích hợp thật |
| #109 AI | Luồng chuẩn bị, lỗi provider, quyền draft/test giả lập | **Thiếu API key**, chưa nghiệm thu provider |
| #124 kiểm kê bài | Phân loại nguồn, JSONL/prompt, command/admin và QA tổng hợp | **Thiếu DB ngân hàng bài thật**, chưa hoàn thành chuẩn hóa toàn bộ |
| #113 / #112 | Đã nghiên cứu | **Chưa triển khai** |

Trạng thái GitHub không được cập nhật trong lượt này. Các nhận định issue/PR lịch sử cần dev đối chiếu trạng thái remote tại thời điểm mở PR. #44 chỉ đề xuất Closed sau team xác nhận hồi quy.

## 3. Kiểm thử và bằng chứng

- **1.374 test PASS/148,329 giây**; nhóm liên quan **68 PASS/8,988 giây**. Regression kiểm tra cache Profile, preferred language, global rank HTML/CSV/XLSX và bảng rỗng.
- Browser: mobile 390×844 Run Accepted, submission [#86 AC 100/100](http://localhost:8001/submission/86) còn trên local; cột thu gọn giữ khi chuyển nhanh và request cũ bị hủy.
- Ranking: offline giữ dữ liệu cũ, retry thành công; search không khớp có thông báo/xóa lọc; file tải thật qa_rank_002 hạng 2/40 điểm.
- Benchmark: 28 route/vai trò, 5 mẫu nối tiếp, real DB/cache, gồm middleware/render. Giảm 103 query trên mỗi trang Grades, các trang khác không được kết luận đều nhanh hơn.

Chi tiết và ảnh/log/file Excel: [RC3 UX và hiệu năng](10_RC3_UX_VA_HIEU_NANG.md), [evidence RC3](evidence/rc3/README.md). RC2 vẫn là lịch sử cho theme/Excel, các tài liệu UI 03–07 ghi các vòng thiết kế cũ.

## 4. Team nhận và duyệt thế nào?

1. Gửi ZIP RC3 thay gói RC2. Dev lead đọc manifest, verify bundle và nhập nhánh theo [hướng dẫn 08](08_GUI_DEV_VA_QUAN_LY_NHANH.md).
2. Dev có quyền WRITE push nhánh vào repo chính hoặc fork, mở Draft PR vào main bằng [PR_DRAFT.md](PR_DRAFT.md). Đối chiếu #141/#142 và main mới trước khi chọn cách tích hợp.
3. Review code theo nhóm, chạy lại test trên SHA cuối của team; QA dùng [checklist](01_KIEM_THU_VA_NGHIEM_THU.md) và thêm ma trận RC3 ở báo cáo 10.
4. Ghi PASS/FAIL/BLOCKED/NOT RUN, giới hạn và người duyệt. Chốt scope AI/kiểm kê chuẩn bị; không coi #109/#124 đã xong.
5. Maintainer duyệt merge. Ops mới thực hiện staging/VPS theo [quy trình 02](02_QUY_TRINH_GIT_MERGE_VPS.md), backup/restore/rollback và smoke test.

Nhánh giữ tên mrtee-hncode theo yêu cầu. Các commit review có thể tiếp tục trên cùng nhánh; nếu đóng gói vòng sau dùng `mrtee-hncode-v1.0.0-rc.4` và manifest mới. Đây là bản để team kiểm tra, chưa phải release production đã nghiệm thu.
